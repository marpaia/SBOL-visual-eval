from __future__ import annotations

import json
from pathlib import Path

import pytest

from sbol_visual_eval.evaluator import cli
from sbol_visual_eval.evaluator.judges import build_judge
from sbol_visual_eval.judge import ClaudeCLIJudge, CodexCLIJudge
from sbol_visual_eval.judge.voting import SelfConsistencyJudge

from ..figures.helpers import build_fixture_pdf
from ..judge.helpers import RULES

RUBRIC_CSV = """\
rule_key,rubric_id,category,section,historical_rule_id,canonical_specification_section,normative_keyword,statement,notes_json,source_url
compliance:5.2.1,historical_2025_rubric_v1,compliance,5.2.1,5.2.1,5.2.1,MUST,The bounding box of a feature glyph MUST be in contact with the backbone,[],https://example.test/rubric
"""


def test_build_judge_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown judge backend"):
        build_judge("mystery", RULES)


def test_build_judge_claude_cli_backend() -> None:
    judge = build_judge("claude-cli", RULES, model="opus")
    assert isinstance(judge, ClaudeCLIJudge)
    assert judge.command()[:4] == ["claude", "-p", "--model", "opus"]


def test_build_judge_codex_cli_backend() -> None:
    judge = build_judge("codex-cli", RULES, model="gpt-5.6-sol")

    assert isinstance(judge, CodexCLIJudge)
    assert judge.model == "gpt-5.6-sol"


def test_build_judge_can_wrap_self_consistency() -> None:
    judge = build_judge("claude-cli", RULES, self_consistency_samples=3)

    assert isinstance(judge, SelfConsistencyJudge)


def test_score_requires_publication_year_for_era_conditioning(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="--publication-year is required"):
        cli.main(
            [
                "--root",
                str(tmp_path),
                "score",
                str(tmp_path / "paper.pdf"),
                "--era-conditioned",
            ]
        )


def test_score_command_writes_historical_format_json(tmp_path: Path, monkeypatch) -> None:
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    (processed / "rubric.csv").write_text(RUBRIC_CSV, encoding="utf-8")
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [[(72, 300, "Figure 1. A genetic circuit.", True)]],
    )
    output_path = tmp_path / "score.json"

    def fake_runner(command: list[str], prompt: str, cwd: Path) -> str:
        return json.dumps(
            {
                "figure_number": 1,
                "compatible": True,
                "rationale": "circuit",
                "findings": [{"rule_key": "compliance:5.2.1", "verdict": "pass", "evidence": ""}],
            }
        )

    monkeypatch.setattr("sbol_visual_eval.judge.claude_cli._run_claude", fake_runner)
    cli.main(
        [
            "--root",
            str(tmp_path),
            "score",
            str(pdf_path),
            "--judge",
            "claude-cli",
            "--output",
            str(output_path),
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["figures_total"] == 1
    assert payload["figures_sbol_visual_compatible"] == 1
    assert payload["figures_sbol_visual_compliant"] == 1
    assert payload["has_compatible_figures"] is True
    assert payload["figures"][0]["figure_number"] == 1
