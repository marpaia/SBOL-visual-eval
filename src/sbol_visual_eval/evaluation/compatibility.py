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
import json
import random
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import (
    canonical_json_sha256,
    read_gzip_json,
    utc_now,
    write_csv,
    write_gzip_json,
    write_json,
)
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
    "era",
    "benchmark_partition",
    "judging_mode",
    "figure_number",
    "expected_compatible",
    "predicted_compatible",
    "correct",
    "rationale",
    "judge_error",
)

COMPATIBILITY_ERAS = (
    (2012, 2013),
    (2014, 2016),
    (2017, 2023),
)

COMPATIBILITY_POOL_CACHE_VERSION = 1


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
    benchmark_partition: str = "unassigned"


def compatibility_era(year: int) -> str:
    """Return the historical-review era containing a publication year."""
    for first_year, last_year in COMPATIBILITY_ERAS:
        if first_year <= year <= last_year:
            return f"{first_year}-{last_year}"
    return str(year)


def assign_era_stratified_partitions(
    figures: list[SaturatedFigure],
    *,
    holdout_fraction: float = 0.2,
    seed: int = 20260904,
) -> list[SaturatedFigure]:
    """Assign whole papers to calibration or holdout within era and label strata."""
    if not 0 <= holdout_fraction < 1:
        raise ValueError("holdout_fraction must be in [0, 1)")

    papers_by_stratum: dict[tuple[str, bool], set[str]] = defaultdict(set)
    for figure in figures:
        papers_by_stratum[(compatibility_era(figure.year), figure.expected_compatible)].add(
            figure.doi
        )

    holdout_dois: set[str] = set()
    for stratum, doi_set in sorted(papers_by_stratum.items()):
        dois = sorted(doi_set)
        random.Random(f"{seed}:{stratum[0]}:{int(stratum[1])}").shuffle(dois)
        holdout_count = round(len(dois) * holdout_fraction)
        if holdout_fraction and len(dois) > 1:
            holdout_count = max(1, min(len(dois) - 1, holdout_count))
        holdout_dois.update(dois[:holdout_count])

    return [
        replace(
            figure,
            benchmark_partition="holdout" if figure.doi in holdout_dois else "calibration",
        )
        for figure in figures
    ]


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
    cache_path = _saturated_pool_cache_path(layout, papers)
    if cache_path.exists():
        try:
            payload = read_gzip_json(cache_path)
            if payload.get("cache_version") == COMPATIBILITY_POOL_CACHE_VERSION:
                return [SaturatedFigure(**row) for row in payload["figures"]]
        except (KeyError, OSError, TypeError, ValueError):
            pass

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
    write_gzip_json(
        cache_path,
        {
            "cache_version": COMPATIBILITY_POOL_CACHE_VERSION,
            "figures": [asdict(figure) for figure in figures],
        },
    )
    return figures


def _saturated_pool_cache_path(layout: Layout, papers: list[SweepPaper]) -> Path:
    paper_inputs = []
    for entry in papers:
        pdf_path = layout.root / entry.pdf_path
        try:
            stat = pdf_path.stat()
            pdf_state: dict[str, int | None] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        except OSError:
            pdf_state = {"size": None, "mtime_ns": None}
        paper_inputs.append(
            {
                "doi": entry.paper.doi,
                "year": entry.paper.year,
                "pdf_path": entry.pdf_path,
                "figures_total": entry.paper.score.figures_total,
                "figures_sbol_visual_compatible": (
                    entry.paper.score.figures_sbol_visual_compatible
                ),
                **pdf_state,
            }
        )
    fingerprint = canonical_json_sha256(
        {
            "cache_version": COMPATIBILITY_POOL_CACHE_VERSION,
            "papers": paper_inputs,
        }
    )
    return layout.cache / "evaluation" / "compatibility" / f"{fingerprint}.json.gz"


def _judge_figure(layout: Layout, judge: FigureJudge, figure: SaturatedFigure) -> dict[str, Any]:
    row = _base_row(figure, "per_figure")
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
        return _error_row(row, error)
    return _verdict_row(row, verdict.compatible, verdict.rationale, figure)


def _base_row(figure: SaturatedFigure, judging_mode: str) -> dict[str, Any]:
    return {
        "doi": figure.doi,
        "year": figure.year,
        "era": compatibility_era(figure.year),
        "benchmark_partition": figure.benchmark_partition,
        "judging_mode": judging_mode,
        "figure_number": figure.figure_number,
        "expected_compatible": figure.expected_compatible,
    }


def _error_row(row: dict[str, Any], error: Exception) -> dict[str, Any]:
    row.update(
        predicted_compatible="",
        correct=False,
        rationale="",
        judge_error=f"{type(error).__name__}: {error}",
    )
    return row


def _verdict_row(
    row: dict[str, Any],
    compatible: bool,
    rationale: str,
    figure: SaturatedFigure,
) -> dict[str, Any]:
    row.update(
        predicted_compatible=compatible,
        correct=compatible == figure.expected_compatible,
        rationale=rationale,
        judge_error="",
    )
    return row


def _judge_paper(
    layout: Layout, judge: FigureJudge, figures: list[SaturatedFigure]
) -> list[dict[str, Any]]:
    rows = [_base_row(figure, "whole_paper") for figure in figures]
    try:
        judge_paper = getattr(judge, "judge_paper", None)
        if not callable(judge_paper):
            raise TypeError("judge does not support whole-paper evaluation")
        rendered_pages: dict[int, bytes] = {}
        contexts = []
        for figure in figures:
            if figure.page_number not in rendered_pages:
                rendered_pages[figure.page_number] = render_page_png(
                    layout.root / figure.pdf_path, figure.page_number
                )
            contexts.append(
                FigureContext(
                    figure_number=figure.figure_number,
                    caption_text=figure.caption_text,
                    page_png=rendered_pages[figure.page_number],
                    publication_year=figure.year,
                    page_number=figure.page_number,
                )
            )
        verdicts = judge_paper(tuple(contexts))
        expected_numbers = [figure.figure_number for figure in figures]
        if [verdict.figure_number for verdict in verdicts] != expected_numbers:
            raise ValueError("judge returned figures that do not match the paper census")
    except Exception as error:  # noqa: BLE001 - one failed paper must not stop the sweep
        return [_error_row(row, error) for row in rows]
    return [
        _verdict_row(row, verdict.compatible, verdict.rationale, figure)
        for row, verdict, figure in zip(rows, verdicts, figures, strict=True)
    ]


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


def _grouped_summaries(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    values = sorted({str(row[field]) for row in rows})
    return {value: _summary([row for row in rows if str(row[field]) == value]) for value in values}


def _checkpoint_rows(
    path: Path, figures: list[SaturatedFigure], judging_mode: str
) -> dict[tuple[str, int], dict[str, Any]]:
    if not path.exists():
        return {}
    requested = {(figure.doi, figure.figure_number): figure for figure in figures}
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = (str(row["doi"]), int(row["figure_number"]))
            figure = requested.get(key)
            if (
                figure is not None
                and int(row["year"]) == figure.year
                and str(row["era"]) == compatibility_era(figure.year)
                and str(row["benchmark_partition"]) == figure.benchmark_partition
                and str(row.get("judging_mode", "per_figure")) == judging_mode
                and bool(row["expected_compatible"]) is figure.expected_compatible
                and not row.get("judge_error")
            ):
                rows[key] = row
    return rows


def run_compatibility_benchmark(
    layout: Layout,
    judge: FigureJudge,
    figures: list[SaturatedFigure],
    *,
    workers: int = 4,
    report_stem: str = "compatibility_benchmark",
    resume: bool = False,
    whole_paper: bool = False,
) -> dict[str, Any]:
    """Judge each labeled figure with a resumable checkpoint and report accuracy."""
    checkpoint_path = layout.reports / f"{report_stem}.checkpoint.jsonl"
    judging_mode = "whole_paper" if whole_paper else "per_figure"
    completed = _checkpoint_rows(checkpoint_path, figures, judging_mode) if resume else {}
    if not resume:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text("", encoding="utf-8")

    resumed_figures = len(completed)
    progress_interval = max(1, len(figures) // 100)
    with (
        checkpoint_path.open("a", encoding="utf-8") as checkpoint,
        ThreadPoolExecutor(max_workers=max(workers, 1)) as pool,
    ):
        if whole_paper:
            paper_groups: dict[str, list[SaturatedFigure]] = defaultdict(list)
            for figure in figures:
                paper_groups[figure.doi].append(figure)
            remaining_papers = [
                paper_figures
                for paper_figures in paper_groups.values()
                if not all(
                    (figure.doi, figure.figure_number) in completed for figure in paper_figures
                )
            ]
            futures = {
                pool.submit(_judge_paper, layout, judge, paper_figures): paper_figures
                for paper_figures in remaining_papers
            }
            last_progress = resumed_figures
            for future in as_completed(futures):
                for row in future.result():
                    key = (str(row["doi"]), int(row["figure_number"]))
                    completed[key] = row
                    checkpoint.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                checkpoint.flush()
                if len(completed) - last_progress >= progress_interval or len(completed) == len(
                    figures
                ):
                    print(
                        f"compatibility progress: {len(completed):,}/{len(figures):,}", flush=True
                    )
                    last_progress = len(completed)
        else:
            remaining = [
                figure for figure in figures if (figure.doi, figure.figure_number) not in completed
            ]
            futures = {
                pool.submit(_judge_figure, layout, judge, figure): figure for figure in remaining
            }
            for completed_count, future in enumerate(as_completed(futures), start=1):
                figure = futures[future]
                row = future.result()
                completed[(figure.doi, figure.figure_number)] = row
                checkpoint.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                checkpoint.flush()
                total_complete = len(figures) - len(remaining) + completed_count
                if total_complete % progress_interval == 0 or total_complete == len(figures):
                    print(
                        f"compatibility progress: {total_complete:,}/{len(figures):,}", flush=True
                    )

    rows = [completed[(figure.doi, figure.figure_number)] for figure in figures]
    for row in rows:
        row.setdefault("judging_mode", judging_mode)

    summary = {
        "generated_at": utc_now(),
        **_summary(rows),
        "resumed_figures": resumed_figures,
        "judging_mode": judging_mode,
        "by_year": _grouped_summaries(rows, "year"),
        "by_era": _grouped_summaries(rows, "era"),
        "by_partition": _grouped_summaries(rows, "benchmark_partition"),
        "report_path": f"data/reports/{report_stem}.csv",
    }
    write_csv(layout.reports / f"{report_stem}.csv", rows, COMPATIBILITY_FIELDS)
    write_json(layout.reports / f"{report_stem}.json", summary)
    checkpoint_path.unlink(missing_ok=True)
    return summary
