"""Verdict vocabulary for per-figure judgments.

A verdict records the judge's compatible/not-compatible decision and one
finding per rubric rule. The compliant and best-practice outcomes are always
derived from the rule findings under the historical cascade — a figure is
compliant only if compatible with no failed compliance rule, and follows best
practices only if compliant with no failed best-practice rule — never taken
from a model's own summary field.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .rubric import BEST_PRACTICE, COMPLIANCE, RubricRule


class RuleVerdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class RuleFinding:
    """The judge's decision on one rubric rule for one figure."""

    rule_key: str
    verdict: RuleVerdict
    evidence: str


@dataclass(frozen=True)
class FigureVerdict:
    """One figure's complete judgment."""

    figure_number: int
    compatible: bool
    rationale: str
    findings: tuple[RuleFinding, ...]

    def _category_clean(self, rules: list[RubricRule], category: str) -> bool:
        keys = {rule.rule_key for rule in rules if rule.category == category}
        return not any(
            finding.verdict is RuleVerdict.FAIL
            for finding in self.findings
            if finding.rule_key in keys
        )

    def compliant(self, rules: list[RubricRule]) -> bool:
        return self.compatible and self._category_clean(rules, COMPLIANCE)

    def best_practice(self, rules: list[RubricRule]) -> bool:
        return self.compliant(rules) and self._category_clean(rules, BEST_PRACTICE)


@dataclass(frozen=True)
class FigureContext:
    """Everything the judge receives about one figure."""

    figure_number: int
    caption_text: str
    page_png: bytes
    publication_year: int | None = None
