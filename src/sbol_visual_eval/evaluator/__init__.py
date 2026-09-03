"""The PDF-in, score-out evaluator.

- :mod:`.pipeline` runs the full cascade for one manuscript PDF: figure
  census, page rendering, per-figure judgment, and aggregation into the
  historical four-count :class:`~sbol_visual_eval.evaluation.PaperScore`.
- :mod:`.judges` selects a judge backend.
- :mod:`.cli` is the ``sbol-visual-eval`` command.
"""

from .judges import build_judge
from .pipeline import PaperEvaluation, evaluate_pdf, score_from_verdicts

__all__ = [
    "PaperEvaluation",
    "build_judge",
    "evaluate_pdf",
    "score_from_verdicts",
]
