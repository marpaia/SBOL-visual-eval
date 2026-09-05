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
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import utc_now, write_csv, write_json
from ..figures import census_pdf, render_page_png, scope_for_pdf
from ..judge.prompt import COMPATIBILITY_PROMPT_PROFILE
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from ..judge.schema import FigureContext, FigureVerdict
from .groundtruth import ground_truth_by_doi, load_ground_truth
from .harness import SweepPaper

CASCADE_FIELDS = (
    "doi",
    "year",
    "prompt_profile",
    "judging_mode",
    "self_consistency_samples",
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
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    figure: CascadeFigure,
    prompt_profile: str,
    self_consistency_samples: int,
) -> dict[str, Any]:
    row = _base_row(figure, "per_figure", prompt_profile, self_consistency_samples)
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
    return _verdict_row(row, verdict, figure, rules)


def _base_row(
    figure: CascadeFigure,
    judging_mode: str,
    prompt_profile: str,
    self_consistency_samples: int,
) -> dict[str, Any]:
    return {
        "doi": figure.doi,
        "year": figure.year,
        "prompt_profile": prompt_profile,
        "judging_mode": judging_mode,
        "self_consistency_samples": self_consistency_samples,
        "figure_number": figure.figure_number,
        "expected_compatible": figure.expected_compatible,
        "expected_compliant": figure.expected_compliant,
        "expected_best_practice": figure.expected_best_practice,
    }


def _error_row(row: dict[str, Any], error: Exception) -> dict[str, Any]:
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


def _verdict_row(
    row: dict[str, Any],
    verdict: FigureVerdict,
    figure: CascadeFigure,
    rules: list[RubricRule],
) -> dict[str, Any]:
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


def _judge_paper(
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    figures: list[CascadeFigure],
    prompt_profile: str,
    self_consistency_samples: int,
) -> list[dict[str, Any]]:
    rows = [
        _base_row(figure, "whole_paper", prompt_profile, self_consistency_samples)
        for figure in figures
    ]
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
        _verdict_row(row, verdict, figure, rules)
        for row, verdict, figure in zip(rows, verdicts, figures, strict=True)
    ]


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


def _checkpoint_rows(
    path: Path,
    figures: list[CascadeFigure],
    *,
    prompt_profile: str,
    judging_mode: str,
    self_consistency_samples: int,
) -> dict[tuple[str, int], dict[str, Any]]:
    if not path.exists():
        return {}
    requested = {(figure.doi, figure.figure_number): figure for figure in figures}
    rows = {}
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
                and str(row.get("prompt_profile", COMPATIBILITY_PROMPT_PROFILE)) == prompt_profile
                and str(row.get("judging_mode", "per_figure")) == judging_mode
                and int(row.get("self_consistency_samples", 1)) == self_consistency_samples
                and bool(row["expected_compatible"]) is figure.expected_compatible
                and bool(row["expected_compliant"]) is figure.expected_compliant
                and bool(row["expected_best_practice"]) is figure.expected_best_practice
                and not row.get("judge_error")
            ):
                rows[key] = row
    return rows


def run_cascade_benchmark(
    layout: Layout,
    judge: FigureJudge,
    rules: list[RubricRule],
    figures: list[CascadeFigure],
    *,
    workers: int = 4,
    report_stem: str = "cascade_benchmark",
    prompt_profile: str = COMPATIBILITY_PROMPT_PROFILE,
    resume: bool = False,
    whole_paper: bool = False,
    benchmark_definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge labeled figures and report per-stage accuracy and blocking rules."""
    checkpoint_path = layout.reports / f"{report_stem}.checkpoint.jsonl"
    judging_mode = "whole_paper" if whole_paper else "per_figure"
    sampling_statistics = getattr(judge, "sampling_statistics", None)
    self_consistency_samples = (
        int(sampling_statistics()["samples"]) if callable(sampling_statistics) else 1
    )
    completed = (
        _checkpoint_rows(
            checkpoint_path,
            figures,
            prompt_profile=prompt_profile,
            judging_mode=judging_mode,
            self_consistency_samples=self_consistency_samples,
        )
        if resume
        else {}
    )
    if not resume:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text("", encoding="utf-8")
    resumed_figures = len(completed)
    with (
        checkpoint_path.open("a", encoding="utf-8") as checkpoint,
        ThreadPoolExecutor(max_workers=max(workers, 1)) as pool,
    ):
        if whole_paper:
            paper_groups: dict[str, list[CascadeFigure]] = defaultdict(list)
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
                pool.submit(
                    _judge_paper,
                    layout,
                    judge,
                    rules,
                    paper_figures,
                    prompt_profile,
                    self_consistency_samples,
                ): paper_figures
                for paper_figures in remaining_papers
            }
            for future in as_completed(futures):
                for row in future.result():
                    key = (str(row["doi"]), int(row["figure_number"]))
                    completed[key] = row
                    checkpoint.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                checkpoint.flush()
        else:
            remaining = [
                figure for figure in figures if (figure.doi, figure.figure_number) not in completed
            ]
            futures = {
                pool.submit(
                    _judge_figure,
                    layout,
                    judge,
                    rules,
                    figure,
                    prompt_profile,
                    self_consistency_samples,
                ): figure
                for figure in remaining
            }
            for future in as_completed(futures):
                figure = futures[future]
                row = future.result()
                completed[(figure.doi, figure.figure_number)] = row
                checkpoint.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                checkpoint.flush()

    rows = [completed[(figure.doi, figure.figure_number)] for figure in figures]
    for row in rows:
        row.setdefault("judging_mode", judging_mode)

    judged = [row for row in rows if not row["judge_error"]]
    summary = {
        "generated_at": utc_now(),
        "figures": len(rows),
        "judged": len(judged),
        "judge_errors": len(rows) - len(judged),
        "resumed_figures": resumed_figures,
        "prompt_profile": prompt_profile,
        "judging_mode": judging_mode,
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
    if callable(sampling_statistics):
        summary["self_consistency"] = sampling_statistics()
    judge_metadata = getattr(judge, "metadata", None)
    if callable(judge_metadata):
        summary["judge"] = judge_metadata()
    if benchmark_definition is not None:
        summary["benchmark_definition"] = benchmark_definition
    write_csv(layout.reports / f"{report_stem}.csv", rows, CASCADE_FIELDS)
    write_json(layout.reports / f"{report_stem}.json", summary)
    if len(judged) == len(rows):
        checkpoint_path.unlink(missing_ok=True)
    return summary
