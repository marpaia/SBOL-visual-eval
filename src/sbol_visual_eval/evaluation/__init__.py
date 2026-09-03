"""Scoring the evaluator against the historical retrospective.

- :mod:`.schema` defines :class:`PaperScore`, the four-count paper score whose
  fields and derived flags match ``data/processed/papers.csv`` exactly.
- :mod:`.groundtruth` loads the historical paper-level counts and derives the
  figure-level labels that saturated counts entail.
- :mod:`.metrics` measures count, flag, and yearly-trend agreement between
  evaluator output and the historical record.
"""

from .groundtruth import (
    GroundTruthPaper,
    SaturatedLabels,
    StageLabels,
    ground_truth_by_doi,
    load_ground_truth,
    saturated_labels,
)
from .metrics import AgreementReport, score_agreement
from .schema import PaperScore

__all__ = [
    "AgreementReport",
    "GroundTruthPaper",
    "PaperScore",
    "SaturatedLabels",
    "StageLabels",
    "ground_truth_by_doi",
    "load_ground_truth",
    "saturated_labels",
    "score_agreement",
]
