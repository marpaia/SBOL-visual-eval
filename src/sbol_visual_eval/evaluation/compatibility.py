"""Figure-level compatibility benchmarking against count-saturated papers.

Papers whose historical compatible count is zero label every one of their
figures a certain compatible-negative, and papers whose compatible count
equals their figure total label every figure a certain positive. Those two
subsets give exact figure-level supervision for the compatibility stage
without inventing a single figure identity, which makes the compatibility
boundary — the largest remaining source of count disagreement — directly
measurable and tunable.
"""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import utc_now, write_csv, write_json
from ..figures import census_pdf, render_page_png, scope_for_pdf
from ..judge.protocol import FigureJudge
from ..judge.schema import FigureContext
from .groundtruth import StageLabels, ground_truth_by_doi, load_ground_truth, saturated_labels
from .harness import SweepPaper

# The saturated-label pool is 2,674 negative and 274 positive figures. A
# benchmark sample is enriched for positives relative to that mix, so error
# rates are projected onto these shares before being read as a count bias.
CORPUS_NEGATIVE_SHARE = 2674 / (2674 + 274)
CORPUS_POSITIVE_SHARE = 274 / (2674 + 274)

COMPATIBILITY_FIELDS = (
    "doi",
    "year",
    "figure_number",
    "expected_compatible",
    "predicted_compatible",
    "correct",
    "rationale",
    "judge_error",
)


@dataclass(frozen=True)
class SaturatedFigure:
    """One figure whose compatibility label is entailed by its paper's counts."""

    doi: str
    year: int
    pdf_path: str
    figure_number: int
    caption_text: str
    page_number: int
    expected_compatible: bool


def saturated_compatibility_papers(
    layout: Layout, *, expected: bool | None = None
) -> list[SweepPaper]:
    """Version-of-Record papers whose compatible stage is fully saturated."""
    ground_truth = ground_truth_by_doi(load_ground_truth(layout))
    papers = []
    inventory_path = layout.reports / "local_corpus_inventory.csv"
    with inventory_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            paper = ground_truth.get(row["doi"])
            if not row["preferred_pdf_path"] or paper is None or not paper.scoreable:
                continue
            if row["preferred_pdf_is_version_of_record"] != "True":
                continue
            stage = saturated_labels(paper.score).compatible
            wanted = {
                True: (StageLabels.ALL_TRUE,),
                False: (StageLabels.ALL_FALSE,),
                None: (StageLabels.ALL_TRUE, StageLabels.ALL_FALSE),
            }[expected]
            if stage in wanted:
                papers.append(SweepPaper(paper, row["preferred_pdf_path"], True))
    papers.sort(key=lambda entry: (entry.paper.year, entry.paper.doi))
    return papers


def saturated_figures(layout: Layout, papers: list[SweepPaper]) -> list[SaturatedFigure]:
    """Census the given papers into individually labeled figures.

    Papers whose census disagrees with ``figures_total`` are skipped: their
    figure set is not the set the reviewers scored, so their entailed labels
    are not trustworthy.
    """
    figures = []
    for entry in papers:
        pdf_path = layout.root / entry.pdf_path
        try:
            census = census_pdf(pdf_path, scope_for_pdf(pdf_path))
        except Exception as error:  # noqa: BLE001 - a broken PDF must not stop the benchmark
            print(f"census failed for {entry.paper.doi}: {type(error).__name__}: {error}")
            continue
        if census.figure_count != entry.paper.score.figures_total:
            continue
        expected = entry.paper.score.figures_sbol_visual_compatible > 0
        for caption in census.captions:
            figures.append(
                SaturatedFigure(
                    doi=entry.paper.doi,
                    year=entry.paper.year,
                    pdf_path=entry.pdf_path,
                    figure_number=caption.figure_number,
                    caption_text=caption.text,
                    page_number=caption.page_number,
                    expected_compatible=expected,
                )
            )
    return figures


def _judge_figure(layout: Layout, judge: FigureJudge, figure: SaturatedFigure) -> dict[str, Any]:
    row: dict[str, Any] = {
        "doi": figure.doi,
        "year": figure.year,
        "figure_number": figure.figure_number,
        "expected_compatible": figure.expected_compatible,
    }
    try:
        context = FigureContext(
            figure_number=figure.figure_number,
            caption_text=figure.caption_text,
            page_png=render_page_png(layout.root / figure.pdf_path, figure.page_number),
        )
        verdict = judge.judge(context)
    except Exception as error:  # noqa: BLE001 - one failed figure must not stop the sweep
        row.update(
            predicted_compatible="",
            correct=False,
            rationale="",
            judge_error=f"{type(error).__name__}: {error}",
        )
        return row
    row.update(
        predicted_compatible=verdict.compatible,
        correct=verdict.compatible == figure.expected_compatible,
        rationale=verdict.rationale,
        judge_error="",
    )
    return row


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [row for row in rows if not row["judge_error"]]
    positives = [row for row in judged if row["expected_compatible"]]
    negatives = [row for row in judged if not row["expected_compatible"]]
    true_positives = sum(row["predicted_compatible"] for row in positives)
    false_positives = sum(row["predicted_compatible"] for row in negatives)
    predicted_positive = true_positives + false_positives
    false_negatives = len(positives) - true_positives
    return {
        "figures": len(rows),
        "judged": len(judged),
        "judge_errors": len(rows) - len(judged),
        "accuracy": round(sum(row["correct"] for row in judged) / len(judged), 4)
        if judged
        else 0.0,
        "certain_positive_figures": len(positives),
        "certain_negative_figures": len(negatives),
        "recall": round(true_positives / len(positives), 4) if positives else 0.0,
        "false_positive_rate": (round(false_positives / len(negatives), 4) if negatives else 0.0),
        "precision": (round(true_positives / predicted_positive, 4) if predicted_positive else 0.0),
        # Count agreement depends on the net of both error directions, not on
        # either rate alone: false positives and false negatives cancel in a
        # paper's compatible count. A benchmark sample is enriched for
        # positives relative to the corpus, so the bias is also projected onto
        # the corpus mix, where negatives outnumber positives about ten to one.
        "expected_positive_figures": len(positives),
        "predicted_positive_figures": predicted_positive,
        "net_count_bias": predicted_positive - len(positives),
        "net_count_bias_per_100_corpus_figures": round(
            100
            * (
                CORPUS_NEGATIVE_SHARE * (false_positives / len(negatives) if negatives else 0.0)
                - CORPUS_POSITIVE_SHARE * (false_negatives / len(positives) if positives else 0.0)
            ),
            2,
        ),
    }


def run_compatibility_benchmark(
    layout: Layout,
    judge: FigureJudge,
    figures: list[SaturatedFigure],
    *,
    workers: int = 4,
    report_stem: str = "compatibility_benchmark",
) -> dict[str, Any]:
    """Judge each labeled figure and report compatibility-stage accuracy."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda figure: _judge_figure(layout, judge, figure), figures))

    summary = {
        "generated_at": utc_now(),
        **_summary(rows),
        "report_path": f"data/reports/{report_stem}.csv",
    }
    write_csv(layout.reports / f"{report_stem}.csv", rows, COMPATIBILITY_FIELDS)
    write_json(layout.reports / f"{report_stem}.json", summary)
    return summary
