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

from .parsing import JudgeParseError, parse_verdict
from .prompt import SYSTEM_PROMPT, build_compatibility_prompt, build_user_prompt
from .rubric import RubricRule
from .schema import FigureContext, FigureVerdict

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
    ) -> None:
        self._rules = rules
        self._model = model
        self._runner = _run_claude if runner is None else runner
        self._compatibility_only = compatibility_only

    def command(self) -> list[str]:
        return ["claude", "-p", "--model", self._model, "--allowed-tools", "Read"]

    def _prompt(self, context: FigureContext) -> str:
        if self._compatibility_only:
            return build_compatibility_prompt(context.figure_number, context.caption_text)
        return build_user_prompt(context.figure_number, context.caption_text, self._rules)

    def judge(self, context: FigureContext) -> FigureVerdict:
        with tempfile.TemporaryDirectory(prefix="sbol-judge-") as workdir:
            image_path = Path(workdir) / "page.png"
            image_path.write_bytes(context.page_png)
            prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"Read the manuscript page image at {image_path} first.\n\n" + self._prompt(context)
            )
            last_error: JudgeParseError | None = None
            for _ in range(PARSE_RETRY_ATTEMPTS):
                reply = self._runner(self.command(), prompt, Path(workdir))
                try:
                    return parse_verdict(reply, context.figure_number)
                except JudgeParseError as error:
                    last_error = error
        assert last_error is not None
        raise JudgeParseError(
            f"judge returned no parseable verdict after {PARSE_RETRY_ATTEMPTS} attempts: "
            f"{last_error}"
        )
