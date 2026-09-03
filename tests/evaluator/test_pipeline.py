from __future__ import annotations

from pathlib import Path

from sbol_visual_eval.evaluation.schema import PaperScore
from sbol_visual_eval.evaluator.pipeline import evaluate_pdf, score_from_verdicts

from ..figures.helpers import build_fixture_pdf
from ..judge.helpers import RULES
from .helpers import ScriptedJudge, verdict


def test_score_from_verdicts_applies_full_cascade() -> None:
    verdicts = [
        verdict(1, compatible=True, passed_rules=("compliance:5.2.1", "best_practice:5.1.2")),
        verdict(2, compatible=True, failed_rules=("compliance:5.2.1",)),
        verdict(3, compatible=True, failed_rules=("best_practice:5.1.2",)),
        verdict(4, compatible=False),
    ]
    score = score_from_verdicts(6, verdicts, RULES)
    assert score == PaperScore(
        figures_total=6,
        figures_sbol_visual_compatible=3,
        figures_sbol_visual_compliant=2,
        figures_best_practices=1,
    )


def test_evaluate_pdf_runs_census_render_judge_and_aggregation(tmp_path: Path) -> None:
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [
            [
                (72, 300, "Figure 1. A genetic circuit diagram.", True),
                (72, 600, "Figure 2. A data plot.", True),
            ]
        ],
    )
    judge = ScriptedJudge(
        {
            1: verdict(1, compatible=True, passed_rules=("compliance:5.2.1",)),
            2: verdict(2, compatible=False),
        }
    )
    evaluation = evaluate_pdf(pdf_path, judge, RULES)

    assert evaluation.score == PaperScore(2, 1, 1, 1)
    assert [context.figure_number for context in judge.contexts] == [1, 2]
    assert judge.contexts[0].caption_text.startswith("Figure 1.")
    assert judge.contexts[0].page_png.startswith(b"\x89PNG")

    payload = evaluation.to_dict()
    assert payload["figures_total"] == 2
    assert payload["figures"][0]["compliant"] is True
    assert payload["figures"][1]["compatible"] is False
    assert payload["has_compatible_figures"] is True
