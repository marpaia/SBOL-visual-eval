from __future__ import annotations

from sbol_visual_eval.judge.schema import FigureContext, FigureVerdict, RuleVerdict
from sbol_visual_eval.judge.voting import SelfConsistencyJudge

from ..evaluator.helpers import verdict

CONTEXT = FigureContext(1, "Figure 1.", b"png")


class SequenceJudge:
    def __init__(self, verdicts: list[FigureVerdict]) -> None:
        self._verdicts = iter(verdicts)
        self.calls = 0

    def judge(self, context: FigureContext) -> FigureVerdict:
        self.calls += 1
        return next(self._verdicts)


def test_non_borderline_verdict_uses_one_sample() -> None:
    underlying = SequenceJudge([verdict(1, compatible=True)])
    judge = SelfConsistencyJudge(underlying, samples=3)

    assert judge.judge(CONTEXT).compatible
    assert underlying.calls == 1
    assert judge.sampling_statistics()["extra_figure_verdicts"] == 0


def test_borderline_compatibility_uses_majority_vote() -> None:
    underlying = SequenceJudge(
        [
            verdict(1, compatible=True, borderline=True),
            verdict(1, compatible=False),
            verdict(1, compatible=False),
        ]
    )
    judge = SelfConsistencyJudge(underlying, samples=3)

    result = judge.judge(CONTEXT)

    assert not result.compatible
    assert result.findings == ()
    assert underlying.calls == 3
    assert judge.sampling_statistics()["extra_figure_verdicts"] == 2


def test_borderline_rule_finding_uses_majority_vote() -> None:
    underlying = SequenceJudge(
        [
            verdict(
                1,
                compatible=True,
                failed_rules=("best_practice:5.1.2",),
                borderline=True,
            ),
            verdict(1, compatible=True, passed_rules=("best_practice:5.1.2",)),
            verdict(1, compatible=True, passed_rules=("best_practice:5.1.2",)),
        ]
    )
    judge = SelfConsistencyJudge(underlying, samples=3)

    result = judge.judge(CONTEXT)

    assert result.compatible
    assert result.findings[0].verdict is RuleVerdict.PASS
    assert not result.borderline


def test_whole_paper_resamples_once_and_only_replaces_borderline_figures() -> None:
    class SequencePaperJudge:
        def __init__(self) -> None:
            self.samples = iter(
                (
                    (
                        verdict(1, compatible=True, borderline=True),
                        verdict(2, compatible=False),
                    ),
                    (verdict(1, compatible=False), verdict(2, compatible=True)),
                    (verdict(1, compatible=False), verdict(2, compatible=True)),
                )
            )
            self.calls = 0

        def judge(self, context):
            raise AssertionError("per-figure judging should not be used")

        def judge_paper(self, contexts):
            self.calls += 1
            return next(self.samples)

    underlying = SequencePaperJudge()
    judge = SelfConsistencyJudge(underlying, samples=3)
    contexts = (CONTEXT, FigureContext(2, "Figure 2.", b"png"))

    result = judge.judge_paper(contexts)

    assert [item.compatible for item in result] == [False, False]
    assert underlying.calls == 3
    assert judge.sampling_statistics()["extra_paper_calls"] == 2
    assert judge.sampling_statistics()["extra_figure_verdicts"] == 4
