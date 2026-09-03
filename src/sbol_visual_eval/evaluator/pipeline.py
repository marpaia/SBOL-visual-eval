"""The end-to-end evaluator: manuscript PDF in, historical-format score out.

The pipeline mirrors the historical review cascade. It censuses the
scoped main-manuscript pages for numbered figures, renders each figure's
page, asks the judge for a per-rule verdict, and aggregates the verdicts
into the four counts of a :class:`PaperScore`: total figures, figures
compatible with SBOL Visual, compliant figures, and best-practice
figures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..evaluation.schema import PaperScore
from ..figures import FigureCensus, census_pdf, render_page_png, scope_for_pdf
from ..figures.scope import PageScope
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from ..judge.schema import FigureContext, FigureVerdict


@dataclass(frozen=True)
class PaperEvaluation:
    """One paper's score plus the census and verdicts behind it."""

    pdf_path: Path
    score: PaperScore
    census: FigureCensus
    verdicts: tuple[FigureVerdict, ...]
    rules: tuple[RubricRule, ...]

    def to_dict(self) -> dict[str, Any]:
        rules = list(self.rules)
        return {
            "pdf_path": str(self.pdf_path),
            **self.score.to_dict(),
            "figures": [
                {
                    "figure_number": verdict.figure_number,
                    "compatible": verdict.compatible,
                    "compliant": verdict.compliant(rules),
                    "best_practice": verdict.best_practice(rules),
                    "rationale": verdict.rationale,
                    "findings": [
                        {
                            "rule_key": finding.rule_key,
                            "verdict": finding.verdict.value,
                            "evidence": finding.evidence,
                        }
                        for finding in verdict.findings
                    ],
                }
                for verdict in self.verdicts
            ],
        }


def score_from_verdicts(
    figures_total: int, verdicts: list[FigureVerdict], rules: list[RubricRule]
) -> PaperScore:
    return PaperScore(
        figures_total=figures_total,
        figures_sbol_visual_compatible=sum(verdict.compatible for verdict in verdicts),
        figures_sbol_visual_compliant=sum(verdict.compliant(rules) for verdict in verdicts),
        figures_best_practices=sum(verdict.best_practice(rules) for verdict in verdicts),
    )


def evaluate_pdf(
    pdf_path: Path,
    judge: FigureJudge,
    rules: list[RubricRule],
    *,
    scope: PageScope | None = None,
) -> PaperEvaluation:
    """Evaluate one manuscript PDF through the full cascade."""
    census = census_pdf(pdf_path, scope if scope is not None else scope_for_pdf(pdf_path))
    verdicts = []
    for caption in census.captions:
        context = FigureContext(
            figure_number=caption.figure_number,
            caption_text=caption.text,
            page_png=render_page_png(pdf_path, caption.page_number),
        )
        verdicts.append(judge.judge(context))
    return PaperEvaluation(
        pdf_path=pdf_path,
        score=score_from_verdicts(census.figure_count, verdicts, rules),
        census=census,
        verdicts=tuple(verdicts),
        rules=tuple(rules),
    )
