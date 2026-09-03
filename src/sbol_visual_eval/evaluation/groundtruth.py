"""Historical ground truth: paper-level counts and the figure labels they entail.

The released supervision is ``paper_aggregate_counts``; no figure identities
exist. Saturated counts still pin down exact figure-level labels for a large
share of the corpus: a paper with zero compatible figures labels every figure
a compatible-negative, and a paper whose compatible and compliant counts match
labels every compatible figure compliant. :func:`saturated_labels` derives the
per-stage label state each paper's counts entail; anything ``MIXED`` must be
handled with count-level supervision, never invented figure identities.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..corpus.layout import Layout
from .schema import PaperScore


class StageLabels(Enum):
    """What a paper's counts entail for every figure at one cascade stage."""

    ALL_TRUE = "all_true"
    ALL_FALSE = "all_false"
    MIXED = "mixed"
    EMPTY = "empty"


@dataclass(frozen=True)
class SaturatedLabels:
    """Figure-level label states entailed by one paper's aggregate counts.

    ``compatible`` ranges over every main-manuscript figure; ``compliant``
    ranges over the compatible figures only; ``best_practice`` ranges over the
    compliant figures only. ``EMPTY`` means the stage has no figures in range.
    """

    compatible: StageLabels
    compliant: StageLabels
    best_practice: StageLabels


@dataclass(frozen=True)
class GroundTruthPaper:
    """One paper's historical annotation joined with its identifying metadata."""

    record_id: str
    doi: str
    year: int
    title: str
    score: PaperScore
    historical_count_invariant_valid: bool
    requires_adjudication: bool

    @property
    def scoreable(self) -> bool:
        """Whether the historical counts are internally consistent and adjudicated."""
        return self.historical_count_invariant_valid and not self.requires_adjudication


def _saturate(positives: int, in_range: int) -> StageLabels:
    if in_range == 0:
        return StageLabels.EMPTY
    if positives == 0:
        return StageLabels.ALL_FALSE
    if positives == in_range:
        return StageLabels.ALL_TRUE
    return StageLabels.MIXED


def saturated_labels(score: PaperScore) -> SaturatedLabels:
    return SaturatedLabels(
        compatible=_saturate(score.figures_sbol_visual_compatible, score.figures_total),
        compliant=_saturate(
            score.figures_sbol_visual_compliant, score.figures_sbol_visual_compatible
        ),
        best_practice=_saturate(score.figures_best_practices, score.figures_sbol_visual_compliant),
    )


def _parse_bool(value: str) -> bool:
    return value == "True"


def load_ground_truth(source: Layout | Path) -> list[GroundTruthPaper]:
    """Load ``data/processed/papers.csv`` (or an explicit CSV path) as ground truth."""
    path = source if isinstance(source, Path) else source.processed / "papers.csv"
    papers = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            papers.append(
                GroundTruthPaper(
                    record_id=row["record_id"],
                    doi=row["doi"],
                    year=int(row["year"]),
                    title=row["title_source"],
                    score=PaperScore(
                        figures_total=int(row["figures_total"]),
                        figures_sbol_visual_compatible=int(row["figures_sbol_visual_compatible"]),
                        figures_sbol_visual_compliant=int(row["figures_sbol_visual_compliant"]),
                        figures_best_practices=int(row["figures_best_practices"]),
                    ),
                    historical_count_invariant_valid=_parse_bool(
                        row["historical_count_invariant_valid"]
                    ),
                    requires_adjudication=_parse_bool(row["requires_adjudication"]),
                )
            )
    return papers


def ground_truth_by_doi(papers: list[GroundTruthPaper]) -> dict[str, GroundTruthPaper]:
    return {paper.doi: paper for paper in papers}
