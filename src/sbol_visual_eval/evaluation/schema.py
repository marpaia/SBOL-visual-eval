"""The paper-level score vocabulary shared by the evaluator and the historical corpus.

A :class:`PaperScore` carries the same four figure counts the retrospective
study released for every paper, and derives the same paper-level flags with
the same rules the corpus build applies to the yearly workbooks. Any evaluator
output expressed as a :class:`PaperScore` is therefore directly comparable to
a row of ``data/processed/papers.csv``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HISTORICAL_COUNT_FIELDS = (
    "figures_total",
    "figures_sbol_visual_compatible",
    "figures_sbol_visual_compliant",
    "figures_best_practices",
)

DERIVED_FLAG_FIELDS = (
    "has_compatible_figures",
    "has_compliant_figures",
    "all_compatible_figures_compliant",
    "has_best_practice_figures",
    "all_compatible_figures_best_practice",
)


@dataclass(frozen=True)
class PaperScore:
    """The four historical figure counts for one paper."""

    figures_total: int
    figures_sbol_visual_compatible: int
    figures_sbol_visual_compliant: int
    figures_best_practices: int

    @property
    def has_compatible_figures(self) -> bool:
        return self.figures_sbol_visual_compatible > 0

    @property
    def has_compliant_figures(self) -> bool:
        return self.figures_sbol_visual_compliant > 0

    @property
    def all_compatible_figures_compliant(self) -> bool:
        return (
            self.figures_sbol_visual_compatible > 0
            and self.figures_sbol_visual_compatible == self.figures_sbol_visual_compliant
        )

    @property
    def has_best_practice_figures(self) -> bool:
        return self.figures_best_practices > 0

    @property
    def all_compatible_figures_best_practice(self) -> bool:
        return (
            self.figures_sbol_visual_compatible > 0
            and self.figures_sbol_visual_compatible
            == self.figures_sbol_visual_compliant
            == self.figures_best_practices
        )

    @property
    def counts(self) -> tuple[int, int, int, int]:
        return (
            self.figures_total,
            self.figures_sbol_visual_compatible,
            self.figures_sbol_visual_compliant,
            self.figures_best_practices,
        )

    def count_invariant_violations(self) -> list[str]:
        """The monotone cascade every score must satisfy: total >= compatible >= compliant >= best."""
        violations = []
        if self.figures_sbol_visual_compatible > self.figures_total:
            violations.append("figures_sbol_visual_compatible exceeds figures_total")
        if self.figures_sbol_visual_compliant > self.figures_sbol_visual_compatible:
            violations.append(
                "figures_sbol_visual_compliant exceeds figures_sbol_visual_compatible"
            )
        if self.figures_best_practices > self.figures_sbol_visual_compliant:
            violations.append("figures_best_practices exceeds figures_sbol_visual_compliant")
        if any(count < 0 for count in self.counts):
            violations.append("counts must be non-negative")
        return violations

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {field: getattr(self, field) for field in HISTORICAL_COUNT_FIELDS}
        for field in DERIVED_FLAG_FIELDS:
            row[field] = getattr(self, field)
        return row
