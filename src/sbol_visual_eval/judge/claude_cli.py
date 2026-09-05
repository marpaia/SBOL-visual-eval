"""The Claude Code CLI judge backend.

Runs ``claude -p`` headlessly against a temporary directory holding the
rendered page image, so judging works with an authenticated Claude Code
installation when no API key is configured. The prompt instructs the CLI
to read the image before applying the rubric.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from .parsing import JudgeParseError, parse_verdict, parse_verdicts
from .prompt import (
    PAPER_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_compatibility_paper_prompt,
    build_compatibility_prompt,
    build_paper_user_prompt,
    build_user_prompt,
    render_compatibility_exemplar,
)
from .rubric import RubricRule
from .schema import CompatibilityExemplar, FigureContext, FigureVerdict

DEFAULT_MODEL = "opus"
DEFAULT_TIMEOUT_SECONDS = 600

# Sustained parallel sweeps intermittently hit throttling, which surfaces as a
# bare nonzero exit; without retries one throttling window fails half a run.
RETRY_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (15.0, 60.0, 180.0)
PARSE_RETRY_ATTEMPTS = 2

Runner = Callable[[list[str], str, Path], str]


def _run_claude(command: list[str], prompt: str, cwd: Path) -> str:
    last_error = ""
    for attempt in range(RETRY_ATTEMPTS):
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout
        last_error = (
            f"claude CLI failed ({completed.returncode}):"
            f" stderr={completed.stderr[:300]!r} stdout={completed.stdout[:300]!r}"
        )
        if attempt < len(RETRY_DELAYS_SECONDS):
            time.sleep(RETRY_DELAYS_SECONDS[attempt])
    raise RuntimeError(last_error)


class ClaudeCLIJudge:
    def __init__(
        self,
        rules: list[RubricRule],
        *,
        model: str = DEFAULT_MODEL,
        runner: Runner | None = None,
        compatibility_only: bool = False,
        exemplars: tuple[CompatibilityExemplar, ...] = (),
        era_conditioned: bool = False,
    ) -> None:
        self._rules = rules
        self._model = model
        self._runner = _run_claude if runner is None else runner
        self._compatibility_only = compatibility_only
        self._exemplars = exemplars
        self._era_conditioned = era_conditioned

    def command(self) -> list[str]:
        return ["claude", "-p", "--model", self._model, "--allowed-tools", "Read"]

    def _prompt(self, context: FigureContext) -> str:
        if self._compatibility_only:
            return build_compatibility_prompt(
                context.figure_number,
                context.caption_text,
                publication_year=context.publication_year,
                era_conditioned=self._era_conditioned,
            )
        return build_user_prompt(
            context.figure_number,
            context.caption_text,
            self._rules,
            publication_year=context.publication_year,
            era_conditioned=self._era_conditioned,
        )

    def _paper_prompt(self, contexts: tuple[FigureContext, ...]) -> str:
        if self._compatibility_only:
            return build_compatibility_paper_prompt(contexts, era_conditioned=self._era_conditioned)
        return build_paper_user_prompt(contexts, self._rules, era_conditioned=self._era_conditioned)

    def _reference_prompt(self, workdir: Path) -> str:
        reference_prompts = []
        for index, exemplar in enumerate(self._exemplars, start=1):
            reference_path = workdir / f"reference-{index}.png"
            reference_path.write_bytes(exemplar.page_png)
            reference_prompts.append(
                f"Read the historical reference image at {reference_path}.\n"
                f"{render_compatibility_exemplar(exemplar)}\n"
            )
        references = "\n".join(reference_prompts)
        if not references:
            return ""
        return (
            "Use these image-backed historical labels as calibration examples. "
            "Do not return verdicts for them.\n\n"
            f"{references}\n"
        )

    def judge(self, context: FigureContext) -> FigureVerdict:
        with tempfile.TemporaryDirectory(prefix="sbol-judge-") as workdir:
            workdir_path = Path(workdir)
            image_path = workdir_path / "page.png"
            image_path.write_bytes(context.page_png)
            references = self._reference_prompt(workdir_path)
            if references:
                target_instruction = f"Read the target manuscript page image at {image_path} first."
            else:
                target_instruction = f"Read the manuscript page image at {image_path} first."
            prompt = f"{SYSTEM_PROMPT}\n\n{references}{target_instruction}\n\n" + self._prompt(
                context
            )
            last_error: JudgeParseError | None = None
            for _ in range(PARSE_RETRY_ATTEMPTS):
                reply = self._runner(self.command(), prompt, workdir_path)
                try:
                    return parse_verdict(reply, context.figure_number)
                except JudgeParseError as error:
                    last_error = error
        assert last_error is not None
        raise JudgeParseError(
            f"judge returned no parseable verdict after {PARSE_RETRY_ATTEMPTS} attempts: "
            f"{last_error}"
        )

    def judge_paper(self, contexts: tuple[FigureContext, ...]) -> tuple[FigureVerdict, ...]:
        if not contexts:
            return ()
        with tempfile.TemporaryDirectory(prefix="sbol-paper-judge-") as workdir:
            workdir_path = Path(workdir)
            references = self._reference_prompt(workdir_path)
            page_groups: dict[int | str, list[FigureContext]] = {}
            for context in contexts:
                page_key: int | str = (
                    context.page_number
                    if context.page_number is not None
                    else f"figure-{context.figure_number}"
                )
                page_groups.setdefault(page_key, []).append(context)

            image_prompts = []
            for page_key, page_contexts in page_groups.items():
                image_path = workdir_path / f"paper-page-{page_key}.png"
                image_path.write_bytes(page_contexts[0].page_png)
                figures = ", ".join(str(context.figure_number) for context in page_contexts)
                image_prompts.append(
                    f"Read target manuscript page image {image_path}; it contains "
                    f"Figure(s) {figures}."
                )

            prompt = (
                f"{PAPER_SYSTEM_PROMPT}\n\n{references}"
                + "\n".join(image_prompts)
                + "\n\n"
                + self._paper_prompt(contexts)
            )
            last_error: JudgeParseError | None = None
            for _ in range(PARSE_RETRY_ATTEMPTS):
                reply = self._runner(self.command(), prompt, workdir_path)
                try:
                    return parse_verdicts(
                        reply, tuple(context.figure_number for context in contexts)
                    )
                except JudgeParseError as error:
                    last_error = error
        assert last_error is not None
        raise JudgeParseError(
            f"judge returned no parseable whole-paper verdict after "
            f"{PARSE_RETRY_ATTEMPTS} attempts: {last_error}"
        )
