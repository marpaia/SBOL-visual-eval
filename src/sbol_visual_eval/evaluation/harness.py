"""Corpus evaluation sweeps: run the evaluator and score it against history.

A sweep evaluates locally available papers through the full pipeline,
pairs every predicted score with the paper's historical counts, and
writes three reports: per-paper rows (``evaluator_agreement.csv``), the
agreement summary (``evaluator_agreement.json``), and the complete
per-figure verdicts for audit (``evaluator_verdicts.jsonl``).
"""

from __future__ import annotations

import csv
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import (
    atomic_write_bytes,
    serialize_jsonl,
    utc_now,
    write_csv,
    write_json,
)
from ..evaluator.pipeline import PaperEvaluation, evaluate_pdf
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from .groundtruth import GroundTruthPaper, ground_truth_by_doi, load_ground_truth
from .metrics import ScoredPair, score_agreement
from .schema import HISTORICAL_COUNT_FIELDS

EVALUATOR_AGREEMENT_FIELDS = (
    "record_id",
    "doi",
    "year",
    "pdf_path",
    "pdf_is_version_of_record",
    "predicted_figures_total",
    "predicted_figures_sbol_visual_compatible",
    "predicted_figures_sbol_visual_compliant",
    "predicted_figures_best_practices",
    "historical_figures_total",
    "historical_figures_sbol_visual_compatible",
    "historical_figures_sbol_visual_compliant",
    "historical_figures_best_practices",
    "all_counts_exact",
    "evaluation_error",
)


@dataclass(frozen=True)
class SweepPaper:
    """One locally evaluable paper joined with its ground truth."""

    paper: GroundTruthPaper
    pdf_path: str
    pdf_is_version_of_record: bool


def sweep_papers(
    layout: Layout,
    *,
    version_of_record_only: bool = True,
    years: tuple[int, ...] | None = None,
) -> list[SweepPaper]:
    """Scoreable papers with a verified preferred local PDF."""
    ground_truth = ground_truth_by_doi(load_ground_truth(layout))
    papers = []
    inventory_path = layout.reports / "local_corpus_inventory.csv"
    with inventory_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            paper = ground_truth.get(row["doi"])
            if not row["preferred_pdf_path"] or paper is None or not paper.scoreable:
                continue
            is_vor = row["preferred_pdf_is_version_of_record"] == "True"
            if version_of_record_only and not is_vor:
                continue
            if years is not None and paper.year not in years:
                continue
            papers.append(
                SweepPaper(
                    paper=paper,
                    pdf_path=row["preferred_pdf_path"],
                    pdf_is_version_of_record=is_vor,
                )
            )
    papers.sort(key=lambda entry: (entry.paper.year, entry.paper.doi))
    return papers


def sample_papers(papers: list[SweepPaper], sample: int | None, seed: int) -> list[SweepPaper]:
    if sample is None or sample >= len(papers):
        return papers
    return sorted(
        random.Random(seed).sample(papers, sample),
        key=lambda entry: (entry.paper.year, entry.paper.doi),
    )


def _evaluate_one(
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    entry: SweepPaper,
) -> tuple[dict[str, Any], PaperEvaluation | None]:
    row: dict[str, Any] = {
        "record_id": entry.paper.record_id,
        "doi": entry.paper.doi,
        "year": entry.paper.year,
        "pdf_path": entry.pdf_path,
        "pdf_is_version_of_record": entry.pdf_is_version_of_record,
    }
    for field in HISTORICAL_COUNT_FIELDS:
        row[f"historical_{field}"] = getattr(entry.paper.score, field)
    try:
        evaluation = evaluate_pdf(
            layout.root / entry.pdf_path,
            judge,
            rules,
            publication_year=entry.paper.year,
        )
    except Exception as error:  # noqa: BLE001 - one failed paper must not stop the sweep
        row.update(
            {f"predicted_{field}": "" for field in HISTORICAL_COUNT_FIELDS},
            all_counts_exact=False,
            evaluation_error=f"{type(error).__name__}: {error}",
        )
        return row, None
    for field in HISTORICAL_COUNT_FIELDS:
        row[f"predicted_{field}"] = getattr(evaluation.score, field)
    row["all_counts_exact"] = evaluation.score.counts == entry.paper.score.counts
    row["evaluation_error"] = ""
    return row, evaluation


def run_sweep(
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    papers: list[SweepPaper],
    *,
    report_stem: str = "evaluator_agreement",
    workers: int = 1,
) -> dict[str, Any]:
    """Evaluate the given papers and write agreement and verdict reports."""
    rows: list[dict[str, Any]] = []
    pairs: list[ScoredPair] = []
    verdict_records: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        results = pool.map(lambda entry: _evaluate_one(layout, judge, rules, entry), papers)
        for entry, (row, evaluation) in zip(papers, results, strict=True):
            rows.append(row)
            if evaluation is None:
                continue
            pairs.append((entry.paper, evaluation.score))
            verdict_records.append({"doi": entry.paper.doi, **evaluation.to_dict()})
            print(
                f"{entry.paper.doi}: predicted {evaluation.score.counts} "
                f"historical {entry.paper.score.counts}"
            )

    agreement = score_agreement(pairs)
    summary = {
        "generated_at": utc_now(),
        "papers_attempted": len(papers),
        "papers_evaluated": len(pairs),
        "evaluation_errors": len(papers) - len(pairs),
        "agreement": agreement.to_dict(),
        "report_path": f"data/reports/{report_stem}.csv",
    }

    write_csv(layout.reports / f"{report_stem}.csv", rows, EVALUATOR_AGREEMENT_FIELDS)
    write_json(layout.reports / f"{report_stem}.json", summary)
    verdict_path = layout.reports / f"{report_stem.replace('agreement', 'verdicts')}.jsonl"
    atomic_write_bytes(verdict_path, serialize_jsonl(verdict_records))
    return summary
