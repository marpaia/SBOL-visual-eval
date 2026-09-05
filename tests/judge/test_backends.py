from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sbol_visual_eval.judge.anthropic_api import AnthropicAPIJudge
from sbol_visual_eval.judge.claude_cli import ClaudeCLIJudge
from sbol_visual_eval.judge.schema import CompatibilityExemplar, FigureContext

from .helpers import RULES

VERDICT_JSON = json.dumps(
    {
        "figure_number": 1,
        "compatible": True,
        "rationale": "Genetic design diagram.",
        "findings": [{"rule_key": "compliance:5.2.1", "verdict": "pass", "evidence": "contact"}],
    }
)

CONTEXT = FigureContext(figure_number=1, caption_text="Figure 1. Construct.", page_png=b"png-bytes")
EXEMPLAR = CompatibilityExemplar(
    identifier="certain-negative",
    publication_year=2016,
    figure_number=2,
    caption_text="Figure 2. Reference.",
    expected_compatible=False,
    rationale="The construct sketch is incidental to a data panel.",
    page_png=b"reference-png",
)


@dataclass
class _TextBlock:
    type: str
    text: str


class _StubMessages:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)

        class Response:
            def __init__(self) -> None:
                self.content = [_TextBlock(type="text", text=VERDICT_JSON)]

        return Response()


class _StubClient:
    def __init__(self) -> None:
        self.messages = _StubMessages()


def test_anthropic_judge_sends_image_and_prompt_and_parses_verdict() -> None:
    client = _StubClient()
    judge = AnthropicAPIJudge(RULES, client=client)
    verdict = judge.judge(CONTEXT)

    assert verdict.compatible
    request = client.messages.requests[0]
    assert request["model"] == "claude-opus-5"
    image_block, text_block = request["messages"][0]["content"]
    assert image_block["source"]["data"] == base64.standard_b64encode(b"png-bytes").decode()
    assert "Figure 1" in text_block["text"]
    assert "compliance:5.2.1" in text_block["text"]


def test_anthropic_judge_sends_image_backed_exemplars_before_target() -> None:
    client = _StubClient()
    judge = AnthropicAPIJudge(RULES, client=client, exemplars=(EXEMPLAR,))

    assert judge.judge(CONTEXT).compatible
    reference_image, reference_text, target_image, target_text = client.messages.requests[0][
        "messages"
    ][0]["content"]
    assert reference_image["source"]["data"] == base64.standard_b64encode(b"reference-png").decode()
    assert "historical verdict is NOT compatible" in reference_text["text"]
    assert target_image["source"]["data"] == base64.standard_b64encode(b"png-bytes").decode()
    assert "Figure 1" in target_text["text"]


def test_claude_cli_judge_writes_image_and_parses_reply() -> None:
    calls: list[tuple[list[str], str, Path]] = []

    def runner(command: list[str], prompt: str, cwd: Path) -> str:
        calls.append((command, prompt, cwd))
        assert (cwd / "page.png").read_bytes() == b"png-bytes"
        return f"Assessment:\n{VERDICT_JSON}"

    judge = ClaudeCLIJudge(RULES, runner=runner)
    verdict = judge.judge(CONTEXT)

    assert verdict.compatible
    command, prompt, _ = calls[0]
    assert command[:2] == ["claude", "-p"]
    assert "page.png" in prompt
    assert "compliance:5.2.1" in prompt


def test_claude_cli_judge_writes_image_backed_exemplars() -> None:
    def runner(command: list[str], prompt: str, cwd: Path) -> str:
        assert (cwd / "reference-1.png").read_bytes() == b"reference-png"
        assert "historical verdict is NOT compatible" in prompt
        assert "Do not return verdicts for them" in prompt
        return VERDICT_JSON

    judge = ClaudeCLIJudge(RULES, runner=runner, exemplars=(EXEMPLAR,))

    assert judge.judge(CONTEXT).compatible


def test_claude_cli_judge_retries_unparseable_reply() -> None:
    replies = iter(("I cannot assess this image.", VERDICT_JSON))
    calls = []

    def runner(command: list[str], prompt: str, cwd: Path) -> str:
        calls.append((command, prompt, cwd))
        return next(replies)

    judge = ClaudeCLIJudge(RULES, runner=runner)

    assert judge.judge(CONTEXT).compatible
    assert len(calls) == 2


def test_claude_cli_runner_retries_transient_failures(monkeypatch) -> None:
    from sbol_visual_eval.judge import claude_cli

    attempts = []

    class Completed:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.stdout = VERDICT_JSON if returncode == 0 else ""
            self.stderr = ""

    def fake_run(command, **kwargs):
        attempts.append(command)
        return Completed(returncode=1 if len(attempts) < 3 else 0)

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(claude_cli.time, "sleep", lambda seconds: None)

    reply = claude_cli._run_claude(["claude", "-p"], "prompt", Path("."))
    assert len(attempts) == 3
    assert VERDICT_JSON in reply


def test_claude_cli_runner_raises_after_exhausting_retries(monkeypatch) -> None:
    import pytest

    from sbol_visual_eval.judge import claude_cli

    class Completed:
        returncode = 1
        stdout = "limit reached"
        stderr = ""

    monkeypatch.setattr(claude_cli.subprocess, "run", lambda command, **kwargs: Completed())
    monkeypatch.setattr(claude_cli.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="limit reached"):
        claude_cli._run_claude(["claude", "-p"], "prompt", Path("."))
