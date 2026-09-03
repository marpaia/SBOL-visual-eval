"""Agreement metrics between evaluator scores and the historical counts.

Ground truth is a single reviewer panel's judgment, so agreement is reported
at several tolerances: exact and within-one count agreement plus mean absolute
error per count field, precision/recall on the derived paper flags, and the
yearly count totals whose trend is the retrospective study's headline result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .groundtruth import GroundTruthPaper
from .schema import DERIVED_FLAG_FIELDS, HISTORICAL_COUNT_FIELDS, PaperScore

ScoredPair = tuple[GroundTruthPaper, PaperScore]


@dataclass(frozen=True)
class CountAgreement:
    field: str
    papers: int
    exact: int
    within_one: int
    absolute_error_sum: int
    historical_total: int
    predicted_total: int

    @property
    def exact_rate(self) -> float:
        return self.exact / self.papers if self.papers else 0.0

    @property
    def within_one_rate(self) -> float:
        return self.within_one / self.papers if self.papers else 0.0

    @property
    def mean_absolute_error(self) -> float:
        return self.absolute_error_sum / self.papers if self.papers else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "papers": self.papers,
            "exact": self.exact,
            "exact_rate": round(self.exact_rate, 4),
            "within_one": self.within_one,
            "within_one_rate": round(self.within_one_rate, 4),
            "mean_absolute_error": round(self.mean_absolute_error, 4),
            "historical_total": self.historical_total,
            "predicted_total": self.predicted_total,
        }


@dataclass(frozen=True)
class FlagAgreement:
    field: str
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def papers(self) -> int:
        return (
            self.true_positives + self.false_positives + self.false_negatives + self.true_negatives
        )

    @property
    def precision(self) -> float:
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 0.0

    @property
    def accuracy(self) -> float:
        return (self.true_positives + self.true_negatives) / self.papers if self.papers else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "papers": self.papers,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "accuracy": round(self.accuracy, 4),
        }


@dataclass(frozen=True)
class AgreementReport:
    papers: int
    all_counts_exact: int
    counts: dict[str, CountAgreement]
    flags: dict[str, FlagAgreement]
    yearly_totals: dict[int, dict[str, dict[str, int]]]

    @property
    def all_counts_exact_rate(self) -> float:
        return self.all_counts_exact / self.papers if self.papers else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "papers": self.papers,
            "all_counts_exact": self.all_counts_exact,
            "all_counts_exact_rate": round(self.all_counts_exact_rate, 4),
            "counts": {field: agreement.to_dict() for field, agreement in self.counts.items()},
            "flags": {field: agreement.to_dict() for field, agreement in self.flags.items()},
            "yearly_totals": {
                str(year): totals for year, totals in sorted(self.yearly_totals.items())
            },
        }


def score_agreement(pairs: list[ScoredPair]) -> AgreementReport:
    """Compare predicted scores against historical papers, skipping unscoreable ground truth."""
    scoreable = [(paper, predicted) for paper, predicted in pairs if paper.scoreable]

    counts = {}
    for field in HISTORICAL_COUNT_FIELDS:
        errors = [
            abs(getattr(predicted, field) - getattr(paper.score, field))
            for paper, predicted in scoreable
        ]
        counts[field] = CountAgreement(
            field=field,
            papers=len(errors),
            exact=sum(error == 0 for error in errors),
            within_one=sum(error <= 1 for error in errors),
            absolute_error_sum=sum(errors),
            historical_total=sum(getattr(paper.score, field) for paper, _ in scoreable),
            predicted_total=sum(getattr(predicted, field) for _, predicted in scoreable),
        )

    flags = {}
    for field in DERIVED_FLAG_FIELDS:
        outcomes = [
            (getattr(paper.score, field), getattr(predicted, field))
            for paper, predicted in scoreable
        ]
        flags[field] = FlagAgreement(
            field=field,
            true_positives=sum(actual and predicted for actual, predicted in outcomes),
            false_positives=sum(not actual and predicted for actual, predicted in outcomes),
            false_negatives=sum(actual and not predicted for actual, predicted in outcomes),
            true_negatives=sum(not actual and not predicted for actual, predicted in outcomes),
        )

    yearly: dict[int, dict[str, dict[str, int]]] = defaultdict(
        lambda: {field: {"historical": 0, "predicted": 0} for field in HISTORICAL_COUNT_FIELDS}
    )
    for paper, predicted in scoreable:
        for field in HISTORICAL_COUNT_FIELDS:
            yearly[paper.year][field]["historical"] += getattr(paper.score, field)
            yearly[paper.year][field]["predicted"] += getattr(predicted, field)

    return AgreementReport(
        papers=len(scoreable),
        all_counts_exact=sum(
            paper.score.counts == predicted.counts for paper, predicted in scoreable
        ),
        counts=counts,
        flags=flags,
        yearly_totals=dict(yearly),
    )
