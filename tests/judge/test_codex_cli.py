from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sbol_visual_eval.judge.codex_cli import CodexCLIJudge, _run_codex
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
WHOLE_PAPER_JSON = json.dumps(
    {
        "figures": [
            {"figure_number": 1, "compatible": True, "rationale": "Construct."},
            {"figure_number": 2, "compatible": False, "rationale": "Plot."},
        ]
    }
)
CONTEXT = FigureContext(
    figure_number=1,
    caption_text="Figure 1. Construct.",
    page_png=b"png-bytes",
    publication_year=2018,
    page_number=4,
)
PAPER_CONTEXTS = (
    CONTEXT,
    FigureContext(
        figure_number=2,
        caption_text="Figure 2. Plot.",
        page_png=b"png-bytes",
        publication_year=2018,
        page_number=4,
    ),
)
EXEMPLAR = CompatibilityExemplar(
    identifier="certain-negative",
    publication_year=2016,
    figure_number=2,
    caption_text="Figure 2. Reference.",
    expected_compatible=False,
    rationale="The construct sketch is incidental to a data panel.",
    page_png=b"reference-png",
)


def test_codex_cli_judge_attaches_target_and_uses_structured_output() -> None:
    calls: list[tuple[list[str], str, Path, Path]] = []

    def runner(command: list[str], prompt: str, cwd: Path, reply_path: Path) -> str:
        calls.append((command, prompt, cwd, reply_path))
        assert (cwd / "target-page.png").read_bytes() == b"png-bytes"
        schema = json.loads((cwd / "verdict.schema.json").read_text(encoding="utf-8"))
        assert schema["properties"]["findings"]["type"] == "array"
        return VERDICT_JSON

    verdict = CodexCLIJudge(RULES, runner=runner).judge(CONTEXT)

    assert verdict.compatible
    command, prompt, _, _ = calls[0]
    assert command[:2] == ["codex", "exec"]
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert command[-2] == "--image"
    assert command[-1].endswith("target-page.png")
    assert "Attached image 1 is the target manuscript page" in prompt
    assert "compliance:5.2.1" in prompt


def test_codex_cli_judge_orders_image_backed_exemplars_before_target() -> None:
    def runner(command: list[str], prompt: str, cwd: Path, reply_path: Path) -> str:
        image_paths = [
            Path(command[index + 1]) for index, item in enumerate(command) if item == "--image"
        ]
        assert [path.name for path in image_paths] == ["reference-1.png", "target-page.png"]
        assert (cwd / "reference-1.png").read_bytes() == b"reference-png"
        assert "Attached image 1 is a historical calibration reference" in prompt
        assert "historical verdict is NOT compatible" in prompt
        assert "Attached image 2 is the target manuscript page" in prompt
        return VERDICT_JSON

    judge = CodexCLIJudge(RULES, runner=runner, exemplars=(EXEMPLAR,))

    assert judge.judge(CONTEXT).compatible


def test_codex_cli_compatibility_schema_supports_borderline() -> None:
    reply = json.dumps(
        {
            "figure_number": 1,
            "compatible": True,
            "borderline": True,
            "rationale": "Boundary case.",
        }
    )

    def runner(command: list[str], prompt: str, cwd: Path, reply_path: Path) -> str:
        schema = json.loads((cwd / "verdict.schema.json").read_text(encoding="utf-8"))
        assert schema["required"] == [
            "figure_number",
            "compatible",
            "rationale",
            "borderline",
        ]
        assert "findings" not in schema["properties"]
        assert 'Set "borderline" to true' in prompt
        return reply

    judge = CodexCLIJudge(
        RULES,
        runner=runner,
        compatibility_only=True,
        request_borderline=True,
    )

    assert judge.judge(CONTEXT).borderline


def test_codex_cli_judges_each_distinct_paper_page_once() -> None:
    calls = []

    def runner(command: list[str], prompt: str, cwd: Path, reply_path: Path) -> str:
        calls.append(command)
        image_paths = [
            Path(command[index + 1]) for index, item in enumerate(command) if item == "--image"
        ]
        assert [path.name for path in image_paths] == ["target-page-4.png"]
        assert "Figure(s) 1, 2" in prompt
        schema = json.loads((cwd / "verdict.schema.json").read_text(encoding="utf-8"))
        assert schema["properties"]["figures"]["type"] == "array"
        return WHOLE_PAPER_JSON

    judge = CodexCLIJudge(RULES, runner=runner, compatibility_only=True)
    verdicts = judge.judge_paper(PAPER_CONTEXTS)

    assert [verdict.compatible for verdict in verdicts] == [True, False]
    assert len(calls) == 1


def test_codex_cli_runner_reads_final_message_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply_path = tmp_path / "reply.json"
    attempts = []

    def run(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 2:
            reply_path.write_text(VERDICT_JSON, encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="", stdout="events")
        return SimpleNamespace(returncode=1, stderr="", stdout="")

    delays = []
    monkeypatch.setattr("sbol_visual_eval.judge.codex_cli.subprocess.run", run)
    monkeypatch.setattr("sbol_visual_eval.judge.codex_cli.time.sleep", delays.append)

    assert _run_codex(["codex", "exec"], "prompt", tmp_path, reply_path) == VERDICT_JSON
    assert len(attempts) == 2
    assert delays == [15.0]


def test_codex_cli_runner_does_not_retry_terminal_usage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=1,
            stderr="You've hit your usage limit; try again later.",
            stdout="",
        )

    monkeypatch.setattr("sbol_visual_eval.judge.codex_cli.subprocess.run", run)
    monkeypatch.setattr(
        "sbol_visual_eval.judge.codex_cli.time.sleep",
        lambda delay: pytest.fail(f"terminal failure slept for {delay}"),
    )

    with pytest.raises(RuntimeError, match="usage limit"):
        _run_codex(["codex", "exec"], "prompt", tmp_path, tmp_path / "reply.json")

    assert len(calls) == 1
