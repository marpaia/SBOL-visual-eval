"""Deterministic judge fakes for evaluator tests."""

from __future__ import annotations

from sbol_visual_eval.judge.schema import FigureContext, FigureVerdict, RuleFinding, RuleVerdict


class ScriptedJudge:
    """Returns pre-scripted verdicts keyed by figure number."""

    def __init__(self, verdicts: dict[int, FigureVerdict]) -> None:
        self._verdicts = verdicts
        self.contexts: list[FigureContext] = []

    def judge(self, context: FigureContext) -> FigureVerdict:
        self.contexts.append(context)
        return self._verdicts[context.figure_number]


def verdict(
    figure_number: int,
    *,
    compatible: bool,
    failed_rules: tuple[str, ...] = (),
    passed_rules: tuple[str, ...] = (),
) -> FigureVerdict:
    findings = [RuleFinding(key, RuleVerdict.PASS, "") for key in passed_rules]
    findings += [RuleFinding(key, RuleVerdict.FAIL, "") for key in failed_rules]
    return FigureVerdict(
        figure_number=figure_number,
        compatible=compatible,
        rationale="scripted",
        findings=tuple(findings),
    )
