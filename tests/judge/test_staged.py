from __future__ import annotations

from sbol_visual_eval.judge.schema import FigureContext
from sbol_visual_eval.judge.staged import StagedCascadeJudge

from ..evaluator.helpers import ScriptedJudge, verdict


def _context(number: int) -> FigureContext:
    return FigureContext(number, f"Figure {number}.", b"png")


def test_staged_judge_skips_downstream_for_incompatible_figure() -> None:
    compatibility = ScriptedJudge({1: verdict(1, compatible=False)})
    downstream = ScriptedJudge({1: verdict(1, compatible=True)})
    judge = StagedCascadeJudge(compatibility, downstream)

    result = judge.judge(_context(1))

    assert result.compatible is False
    assert len(compatibility.contexts) == 1
    assert downstream.contexts == []


def test_staged_judge_preserves_first_stage_compatibility_and_downstream_rules() -> None:
    compatibility = ScriptedJudge({1: verdict(1, compatible=True)})
    downstream = ScriptedJudge(
        {1: verdict(1, compatible=False, failed_rules=("compliance:5.2.1",))}
    )
    judge = StagedCascadeJudge(compatibility, downstream)

    result = judge.judge(_context(1))

    assert result.compatible is True
    assert [finding.rule_key for finding in result.findings] == ["compliance:5.2.1"]
    assert "Compatibility stage" in result.rationale
    assert "Downstream stage" in result.rationale


def test_staged_whole_paper_sends_only_compatible_figures_downstream() -> None:
    class PaperJudge:
        def __init__(self, compatible_numbers: set[int]) -> None:
            self.compatible_numbers = compatible_numbers
            self.batches = []

        def judge_paper(self, contexts):
            self.batches.append(contexts)
            return tuple(
                verdict(
                    context.figure_number,
                    compatible=context.figure_number in self.compatible_numbers,
                )
                for context in contexts
            )

    compatibility = PaperJudge({1})
    downstream = PaperJudge(set())
    judge = StagedCascadeJudge(compatibility, downstream)

    results = judge.judge_paper((_context(1), _context(2)))

    assert [result.compatible for result in results] == [True, False]
    assert [[context.figure_number for context in batch] for batch in downstream.batches] == [[1]]
