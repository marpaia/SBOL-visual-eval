"""Full-cascade benchmarking against papers saturated at every stage.

Papers whose four counts are all equal to their figure total label every
figure a certain positive through the whole cascade: compatible, compliant,
and best-practice. Papers with compliant figures but zero best-practice
figures label every compliant figure a certain best-practice negative.
Judging those figures with the full rubric prompt measures compliance and
best-practice strictness directly, the way :mod:`.compatibility` measures the
compatibility boundary.
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
from ..judge.rubric import RubricRule
from ..judge.schema import FigureContext
from .groundtruth import ground_truth_by_doi, load_ground_truth
from .harness import SweepPaper

CASCADE_FIELDS = (
    "doi",
    "year",
    "figure_number",
    "expected_compatible",
    "expected_compliant",
    "expected_best_practice",
    "predicted_compatible",
    "predicted_compliant",
    "predicted_best_practice",
    "compatible_correct",
    "compliant_correct",
    "best_practice_correct",
    "failed_rules",
    "judge_error",
)


@dataclass(frozen=True)
class CascadeFigure:
    """One figure whose full-cascade labels are entailed by its paper's counts."""

    doi: str
    year: int
    pdf_path: str
    figure_number: int
    caption_text: str
    page_number: int
    expected_compatible: bool
    expected_compliant: bool
    expected_best_practice: bool


def _cascade_expectation(score: Any) -> tuple[bool, bool, bool] | None:
    """The per-figure labels a paper's counts entail for every one of its figures.

    Only two shapes entail a label for every figure: all four counts equal
    (every figure positive throughout), and every figure compatible and
    compliant with zero best-practice figures (every figure a best-practice
    negative).
    """
    total = score.figures_total
    if total == 0:
        return None
    compatible = score.figures_sbol_visual_compatible
    compliant = score.figures_sbol_visual_compliant
    best = score.figures_best_practices
    if total == compatible == compliant == best:
        return (True, True, True)
    if total == compatible == compliant and best == 0:
        return (True, True, False)
    return None


def cascade_papers(layout: Layout) -> list[SweepPaper]:
    """Version-of-Record papers whose every figure has entailed cascade labels."""
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
            if _cascade_expectation(paper.score) is None:
                continue
            papers.append(SweepPaper(paper, row["preferred_pdf_path"], True))
    papers.sort(key=lambda entry: (entry.paper.year, entry.paper.doi))
    return papers


def cascade_figures(layout: Layout, papers: list[SweepPaper]) -> list[CascadeFigure]:
    """Census the given papers into individually labeled cascade figures."""
    figures = []
    for entry in papers:
        expectation = _cascade_expectation(entry.paper.score)
        if expectation is None:
            continue
        pdf_path = layout.root / entry.pdf_path
        try:
            census = census_pdf(pdf_path, scope_for_pdf(pdf_path))
        except Exception as error:  # noqa: BLE001 - a broken PDF must not stop the benchmark
            print(f"census failed for {entry.paper.doi}: {type(error).__name__}: {error}")
            continue
        if census.figure_count != entry.paper.score.figures_total:
            continue
        compatible, compliant, best_practice = expectation
        for caption in census.captions:
            figures.append(
                CascadeFigure(
                    doi=entry.paper.doi,
                    year=entry.paper.year,
                    pdf_path=entry.pdf_path,
                    figure_number=caption.figure_number,
                    caption_text=caption.text,
                    page_number=caption.page_number,
                    expected_compatible=compatible,
                    expected_compliant=compliant,
                    expected_best_practice=best_practice,
                )
            )
    return figures


def _judge_figure(
    layout: Layout, judge: FigureJudge, rules: list[RubricRule], figure: CascadeFigure
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "doi": figure.doi,
        "year": figure.year,
        "figure_number": figure.figure_number,
        "expected_compatible": figure.expected_compatible,
        "expected_compliant": figure.expected_compliant,
        "expected_best_practice": figure.expected_best_practice,
    }
    try:
        context = FigureContext(
            figure_number=figure.figure_number,
            caption_text=figure.caption_text,
            page_png=render_page_png(layout.root / figure.pdf_path, figure.page_number),
            publication_year=figure.year,
            page_number=figure.page_number,
        )
        verdict = judge.judge(context)
    except Exception as error:  # noqa: BLE001 - one failed figure must not stop the sweep
        row.update(
            predicted_compatible="",
            predicted_compliant="",
            predicted_best_practice="",
            compatible_correct=False,
            compliant_correct=False,
            best_practice_correct=False,
            failed_rules="",
            judge_error=f"{type(error).__name__}: {error}",
        )
        return row
    compliant = verdict.compliant(rules)
    best_practice = verdict.best_practice(rules)
    row.update(
        predicted_compatible=verdict.compatible,
        predicted_compliant=compliant,
        predicted_best_practice=best_practice,
        compatible_correct=verdict.compatible == figure.expected_compatible,
        compliant_correct=compliant == figure.expected_compliant,
        best_practice_correct=best_practice == figure.expected_best_practice,
        failed_rules=" ".join(
            finding.rule_key for finding in verdict.findings if finding.verdict.value == "fail"
        ),
        judge_error="",
    )
    return row


def _stage_accuracy(rows: list[dict[str, Any]], stage: str) -> float:
    if not rows:
        return 0.0
    return round(sum(row[f"{stage}_correct"] for row in rows) / len(rows), 4)


def _rule_failure_counts(
    rows: list[dict[str, Any]], expected_stage: str, prefix: str | None = None
) -> dict[str, int]:
    """Which rules fail on figures the panel judged positive at this stage.

    Only rules that can actually block the stage are counted, so a
    best-practice failure never appears as a reason a figure missed
    compliance.
    """
    counts: dict[str, int] = {}
    for row in rows:
        if not row[expected_stage]:
            continue
        for rule_key in str(row["failed_rules"]).split():
            if prefix is not None and not rule_key.startswith(prefix):
                continue
            counts[rule_key] = counts.get(rule_key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def run_cascade_benchmark(
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    figures: list[CascadeFigure],
    *,
    workers: int = 4,
    report_stem: str = "cascade_benchmark",
) -> dict[str, Any]:
    """Judge each labeled figure and report per-stage accuracy and blocking rules."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(lambda figure: _judge_figure(layout, judge, rules, figure), figures))

    judged = [row for row in rows if not row["judge_error"]]
    summary = {
        "generated_at": utc_now(),
        "figures": len(rows),
        "judged": len(judged),
        "judge_errors": len(rows) - len(judged),
        "compatible_accuracy": _stage_accuracy(judged, "compatible"),
        "compliant_accuracy": _stage_accuracy(judged, "compliant"),
        "best_practice_accuracy": _stage_accuracy(judged, "best_practice"),
        "expected_best_practice_figures": sum(row["expected_best_practice"] for row in judged),
        "rules_blocking_expected_compliant": _rule_failure_counts(
            judged, "expected_compliant", prefix="compliance:"
        ),
        "rules_blocking_expected_best_practice": _rule_failure_counts(
            judged, "expected_best_practice", prefix="best_practice:"
        ),
        "report_path": f"data/reports/{report_stem}.csv",
    }
    sampling_statistics = getattr(judge, "sampling_statistics", None)
    if callable(sampling_statistics):
        summary["self_consistency"] = sampling_statistics()
    write_csv(layout.reports / f"{report_stem}.csv", rows, CASCADE_FIELDS)
    write_json(layout.reports / f"{report_stem}.json", summary)
    return summary
