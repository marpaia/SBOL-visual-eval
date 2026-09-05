"""Corpus evaluation sweeps: run the evaluator and score it against history.

A sweep evaluates locally available papers through the full pipeline,
pairs every predicted score with the paper's historical counts, and
writes three reports: per-paper rows (``evaluator_agreement.csv``), the
agreement summary (``evaluator_agreement.json``), and the complete
per-figure verdicts for audit (``evaluator_verdicts.jsonl``).
"""

from __future__ import annotations

import csv
import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
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
from ..judge.prompt import COMPATIBILITY_PROMPT_PROFILE
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from .groundtruth import GroundTruthPaper, ground_truth_by_doi, load_ground_truth
from .metrics import ScoredPair, score_agreement
from .schema import HISTORICAL_COUNT_FIELDS, PaperScore

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
    whole_paper: bool,
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
            whole_paper=whole_paper,
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


def _checkpoint_evaluations(
    path: Path,
    papers: list[SweepPaper],
    *,
    judging_mode: str,
    prompt_profile: str,
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    if not path.exists():
        return {}
    requested = {entry.paper.doi: entry for entry in papers}
    completed = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            doi = str(record["doi"])
            entry = requested.get(doi)
            row = record.get("row")
            evaluation = record.get("evaluation")
            if (
                entry is not None
                and isinstance(row, dict)
                and isinstance(evaluation, dict)
                and not row.get("evaluation_error")
                and str(row.get("pdf_path")) == entry.pdf_path
                and int(row.get("year")) == entry.paper.year
                and str(record.get("judging_mode", "per_figure")) == judging_mode
                and str(record.get("prompt_profile", COMPATIBILITY_PROMPT_PROFILE))
                == prompt_profile
                and all(
                    int(row[f"historical_{field}"]) == getattr(entry.paper.score, field)
                    for field in HISTORICAL_COUNT_FIELDS
                )
            ):
                completed[doi] = (row, evaluation)
    return completed


def _score_from_evaluation(evaluation: dict[str, Any]) -> PaperScore:
    return PaperScore(**{field: int(evaluation[field]) for field in HISTORICAL_COUNT_FIELDS})


def run_sweep(
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    papers: list[SweepPaper],
    *,
    report_stem: str = "evaluator_agreement",
    workers: int = 1,
    whole_paper: bool = False,
    prompt_profile: str = COMPATIBILITY_PROMPT_PROFILE,
    resume: bool = False,
) -> dict[str, Any]:
    """Evaluate the given papers and write agreement and verdict reports."""
    checkpoint_path = layout.reports / f"{report_stem}.checkpoint.jsonl"
    judging_mode = "whole_paper" if whole_paper else "per_figure"
    completed: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = (
        _checkpoint_evaluations(
            checkpoint_path,
            papers,
            judging_mode=judging_mode,
            prompt_profile=prompt_profile,
        )
        if resume
        else {}
    )
    if not resume:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text("", encoding="utf-8")
    resumed_papers = len(completed)
    remaining = [entry for entry in papers if entry.paper.doi not in completed]

    with (
        checkpoint_path.open("a", encoding="utf-8") as checkpoint,
        ThreadPoolExecutor(max_workers=max(workers, 1)) as pool,
    ):
        futures = {
            pool.submit(_evaluate_one, layout, judge, rules, entry, whole_paper): entry
            for entry in remaining
        }
        for future in as_completed(futures):
            entry = futures[future]
            row, evaluation = future.result()
            evaluation_record = evaluation.to_dict() if evaluation is not None else None
            completed[entry.paper.doi] = (row, evaluation_record)
            checkpoint.write(
                json.dumps(
                    {
                        "doi": entry.paper.doi,
                        "judging_mode": judging_mode,
                        "prompt_profile": prompt_profile,
                        "row": row,
                        "evaluation": evaluation_record,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            checkpoint.flush()

    rows: list[dict[str, Any]] = []
    pairs: list[ScoredPair] = []
    verdict_records: list[dict[str, Any]] = []
    for entry in papers:
        row, evaluation = completed[entry.paper.doi]
        rows.append(row)
        if evaluation is None:
            continue
        score = _score_from_evaluation(evaluation)
        pairs.append((entry.paper, score))
        verdict_records.append({"doi": entry.paper.doi, **evaluation})
        print(f"{entry.paper.doi}: predicted {score.counts} historical {entry.paper.score.counts}")

    agreement = score_agreement(pairs)
    summary = {
        "generated_at": utc_now(),
        "papers_attempted": len(papers),
        "papers_evaluated": len(pairs),
        "evaluation_errors": len(papers) - len(pairs),
        "resumed_papers": resumed_papers,
        "judging_mode": judging_mode,
        "prompt_profile": prompt_profile,
        "agreement": agreement.to_dict(),
        "report_path": f"data/reports/{report_stem}.csv",
    }
    sampling_statistics = getattr(judge, "sampling_statistics", None)
    if callable(sampling_statistics):
        summary["self_consistency"] = sampling_statistics()
    judge_metadata = getattr(judge, "metadata", None)
    if callable(judge_metadata):
        summary["judge"] = judge_metadata()

    write_csv(layout.reports / f"{report_stem}.csv", rows, EVALUATOR_AGREEMENT_FIELDS)
    write_json(layout.reports / f"{report_stem}.json", summary)
    verdict_path = layout.reports / f"{report_stem.replace('agreement', 'verdicts')}.jsonl"
    atomic_write_bytes(verdict_path, serialize_jsonl(verdict_records))
    if len(pairs) == len(papers):
        checkpoint_path.unlink(missing_ok=True)
    return summary
