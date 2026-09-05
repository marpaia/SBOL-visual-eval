"""The authenticated Codex CLI judge backend.

Runs ``codex exec`` non-interactively with rendered page images attached to
the first message. The local Codex installation supplies ChatGPT/OpenAI
authentication, while a JSON Schema constrains the final verdict for reliable
downstream parsing.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

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

DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_TIMEOUT_SECONDS = 600
RETRY_ATTEMPTS = 4
RETRY_DELAYS_SECONDS = (15.0, 60.0, 180.0)
PARSE_RETRY_ATTEMPTS = 2
NON_RETRYABLE_FAILURE_MARKERS = (
    "you've hit your usage limit",
    "usage limit has been reached",
    "insufficient_quota",
    "authentication required",
    "not logged in",
)

Runner = Callable[[list[str], str, Path, Path], str]


def _run_codex(command: list[str], prompt: str, cwd: Path, reply_path: Path) -> str:
    """Run one Codex request, retrying only failures that may be transient."""
    last_error = ""
    for attempt in range(RETRY_ATTEMPTS):
        reply_path.unlink(missing_ok=True)
        completed = subprocess.run(
            command,
            input=prompt,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode == 0 and reply_path.exists():
            return reply_path.read_text(encoding="utf-8")
        last_error = (
            f"Codex CLI failed ({completed.returncode}):"
            f" stderr={completed.stderr[:300]!r} stdout={completed.stdout[:300]!r}"
        )
        combined_output = f"{completed.stderr}\n{completed.stdout}".casefold()
        if any(marker in combined_output for marker in NON_RETRYABLE_FAILURE_MARKERS):
            break
        if attempt < len(RETRY_DELAYS_SECONDS):
            time.sleep(RETRY_DELAYS_SECONDS[attempt])
    raise RuntimeError(last_error)


def _figure_schema(*, compatibility_only: bool, request_borderline: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "figure_number": {"type": "integer"},
        "compatible": {"type": "boolean"},
        "rationale": {"type": "string"},
    }
    required = ["figure_number", "compatible", "rationale"]
    if request_borderline:
        properties["borderline"] = {"type": "boolean"}
        required.append("borderline")
    if not compatibility_only:
        properties["findings"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_key": {"type": "string"},
                    "verdict": {
                        "type": "string",
                        "enum": ["pass", "fail", "not_applicable"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["rule_key", "verdict", "evidence"],
                "additionalProperties": False,
            },
        }
        required.append("findings")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _output_schema(
    *, compatibility_only: bool, request_borderline: bool, whole_paper: bool
) -> dict[str, Any]:
    figure_schema = _figure_schema(
        compatibility_only=compatibility_only,
        request_borderline=request_borderline,
    )
    if not whole_paper:
        return figure_schema
    return {
        "type": "object",
        "properties": {
            "figures": {
                "type": "array",
                "items": figure_schema,
            }
        },
        "required": ["figures"],
        "additionalProperties": False,
    }


class CodexCLIJudge:
    """Judge figures through the user's authenticated local Codex CLI."""

    def __init__(
        self,
        rules: list[RubricRule],
        *,
        model: str = DEFAULT_MODEL,
        runner: Runner | None = None,
        compatibility_only: bool = False,
        exemplars: tuple[CompatibilityExemplar, ...] = (),
        era_conditioned: bool = False,
        request_borderline: bool = False,
    ) -> None:
        self._rules = rules
        self._model = model
        self._runner = _run_codex if runner is None else runner
        self._compatibility_only = compatibility_only
        self._exemplars = exemplars
        self._era_conditioned = era_conditioned
        self._request_borderline = request_borderline

    @property
    def model(self) -> str:
        return self._model

    def metadata(self) -> dict[str, str]:
        """Identify the provider and pinned model used in benchmark reports."""
        return {"backend": "codex-cli", "model": self._model}

    def command(
        self,
        image_paths: Sequence[Path],
        reply_path: Path,
        schema_path: Path,
    ) -> list[str]:
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--model",
            self._model,
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(reply_path),
            "-",
        ]
        for image_path in image_paths:
            command.extend(("--image", str(image_path)))
        return command

    def _prompt(self, context: FigureContext) -> str:
        if self._compatibility_only:
            return build_compatibility_prompt(
                context.figure_number,
                context.caption_text,
                publication_year=context.publication_year,
                era_conditioned=self._era_conditioned,
                request_borderline=self._request_borderline,
            )
        return build_user_prompt(
            context.figure_number,
            context.caption_text,
            self._rules,
            publication_year=context.publication_year,
            era_conditioned=self._era_conditioned,
            request_borderline=self._request_borderline,
            reinforce_historical_threshold=True,
        )

    def _paper_prompt(self, contexts: tuple[FigureContext, ...]) -> str:
        if self._compatibility_only:
            return build_compatibility_paper_prompt(
                contexts,
                era_conditioned=self._era_conditioned,
                request_borderline=self._request_borderline,
            )
        return build_paper_user_prompt(
            contexts,
            self._rules,
            era_conditioned=self._era_conditioned,
            request_borderline=self._request_borderline,
            reinforce_historical_threshold=True,
        )

    def _reference_images(self, workdir: Path) -> tuple[list[Path], str]:
        paths = []
        descriptions = []
        for index, exemplar in enumerate(self._exemplars, start=1):
            path = workdir / f"reference-{index}.png"
            path.write_bytes(exemplar.page_png)
            paths.append(path)
            descriptions.append(
                f"Attached image {index} is a historical calibration reference.\n"
                f"{render_compatibility_exemplar(exemplar)}"
            )
        if not descriptions:
            return paths, ""
        return paths, (
            "Use the following image-backed historical labels as calibration examples. "
            "Do not return verdicts for them.\n\n" + "\n\n".join(descriptions) + "\n\n"
        )

    def _write_schema(self, workdir: Path, *, whole_paper: bool) -> Path:
        schema_path = workdir / "verdict.schema.json"
        schema_path.write_text(
            json.dumps(
                _output_schema(
                    compatibility_only=self._compatibility_only,
                    request_borderline=self._request_borderline,
                    whole_paper=whole_paper,
                )
            ),
            encoding="utf-8",
        )
        return schema_path

    def judge(self, context: FigureContext) -> FigureVerdict:
        with tempfile.TemporaryDirectory(prefix="sbol-codex-judge-") as workdir:
            workdir_path = Path(workdir)
            image_paths, references = self._reference_images(workdir_path)
            target_path = workdir_path / "target-page.png"
            target_path.write_bytes(context.page_png)
            image_paths.append(target_path)
            target_number = len(image_paths)
            prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                "The images are already attached to this message. Do not use tools or inspect "
                "unrelated files.\n\n"
                f"{references}Attached image {target_number} is the target manuscript page.\n\n"
                f"{self._prompt(context)}"
            )
            schema_path = self._write_schema(workdir_path, whole_paper=False)
            reply_path = workdir_path / "reply.json"
            last_error: JudgeParseError | None = None
            for _ in range(PARSE_RETRY_ATTEMPTS):
                reply = self._runner(
                    self.command(image_paths, reply_path, schema_path),
                    prompt,
                    workdir_path,
                    reply_path,
                )
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
        with tempfile.TemporaryDirectory(prefix="sbol-codex-paper-judge-") as workdir:
            workdir_path = Path(workdir)
            image_paths, references = self._reference_images(workdir_path)
            page_groups: dict[int | str, list[FigureContext]] = {}
            for context in contexts:
                page_key: int | str = (
                    context.page_number
                    if context.page_number is not None
                    else f"figure-{context.figure_number}"
                )
                page_groups.setdefault(page_key, []).append(context)

            target_descriptions = []
            for page_key, page_contexts in page_groups.items():
                image_path = workdir_path / f"target-page-{page_key}.png"
                image_path.write_bytes(page_contexts[0].page_png)
                image_paths.append(image_path)
                figures = ", ".join(str(context.figure_number) for context in page_contexts)
                target_descriptions.append(
                    f"Attached image {len(image_paths)} is a target manuscript page containing "
                    f"Figure(s) {figures}."
                )

            prompt = (
                f"{PAPER_SYSTEM_PROMPT}\n\n"
                "The images are already attached to this message. Do not use tools or inspect "
                "unrelated files.\n\n"
                f"{references}{' '.join(target_descriptions)}\n\n"
                f"{self._paper_prompt(contexts)}"
            )
            schema_path = self._write_schema(workdir_path, whole_paper=True)
            reply_path = workdir_path / "reply.json"
            last_error: JudgeParseError | None = None
            for _ in range(PARSE_RETRY_ATTEMPTS):
                reply = self._runner(
                    self.command(image_paths, reply_path, schema_path),
                    prompt,
                    workdir_path,
                    reply_path,
                )
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
