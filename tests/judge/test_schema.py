from __future__ import annotations

from sbol_visual_eval.judge.schema import FigureVerdict, RuleFinding, RuleVerdict

from .helpers import RULES


def _verdict(compatible: bool, findings: list[RuleFinding]) -> FigureVerdict:
    return FigureVerdict(
        figure_number=1, compatible=compatible, rationale="", findings=tuple(findings)
    )


def test_incompatible_figure_is_never_compliant() -> None:
    verdict = _verdict(False, [])
    assert not verdict.compliant(RULES)
    assert not verdict.best_practice(RULES)


def test_compatible_with_clean_findings_cascades_fully() -> None:
    verdict = _verdict(
        True,
        [
            RuleFinding("compliance:5.2.1", RuleVerdict.PASS, ""),
            RuleFinding("compliance:5.2.6", RuleVerdict.NOT_APPLICABLE, ""),
            RuleFinding("best_practice:5.1.2", RuleVerdict.PASS, ""),
        ],
    )
    assert verdict.compliant(RULES)
    assert verdict.best_practice(RULES)


def test_failed_compliance_rule_blocks_compliance_and_best_practice() -> None:
    verdict = _verdict(
        True,
        [
            RuleFinding("compliance:5.2.1", RuleVerdict.FAIL, "glyph floats above backbone"),
            RuleFinding("best_practice:5.1.2", RuleVerdict.PASS, ""),
        ],
    )
    assert not verdict.compliant(RULES)
    assert not verdict.best_practice(RULES)


def test_failed_best_practice_rule_blocks_only_best_practice() -> None:
    verdict = _verdict(
        True,
        [
            RuleFinding("compliance:5.2.1", RuleVerdict.PASS, ""),
            RuleFinding("best_practice:5.1.2", RuleVerdict.FAIL, "vertical backbone"),
        ],
    )
    assert verdict.compliant(RULES)
    assert not verdict.best_practice(RULES)


def test_unknown_rule_keys_are_ignored() -> None:
    verdict = _verdict(True, [RuleFinding("compliance:9.9.9", RuleVerdict.FAIL, "")])
    assert verdict.compliant(RULES)
