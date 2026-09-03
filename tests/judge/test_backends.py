from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sbol_visual_eval.judge.anthropic_api import AnthropicAPIJudge
from sbol_visual_eval.judge.claude_cli import ClaudeCLIJudge
from sbol_visual_eval.judge.schema import FigureContext

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
