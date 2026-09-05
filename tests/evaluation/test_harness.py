from __future__ import annotations

import csv
import json
from pathlib import Path

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.harness import (
    exclude_reported_sweep_papers,
    run_sweep,
    sample_papers,
    sweep_papers,
)

from ..evaluator.helpers import ScriptedJudge, verdict
from ..figures.helpers import build_fixture_pdf
from ..judge.helpers import RULES

PAPERS_CSV_HEADER = (
    "record_id,doi,year,title_source,"
    "figures_total,figures_sbol_visual_compatible,"
    "figures_sbol_visual_compliant,figures_best_practices,"
    "historical_count_invariant_valid,requires_adjudication\n"
)

INVENTORY_HEADER = "record_id,doi,year,preferred_pdf_path,preferred_pdf_is_version_of_record\n"


def _prepare_layout(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    layout.reports.mkdir(parents=True)

    (layout.processed / "papers.csv").write_text(
        PAPERS_CSV_HEADER
        + "doi:10.1/a,10.1/a,2015,Paper A,2,1,1,0,True,False\n"
        + "doi:10.1/b,10.1/b,2016,Paper B,1,0,0,0,True,False\n"
        + "doi:10.1/c,10.1/c,2016,Paper C,1,1,1,1,True,False\n",
        encoding="utf-8",
    )

    for slug, year, pages in (
        (
            "10.1__a",
            2015,
            [[(72, 300, "Figure 1. Circuit.", True), (72, 600, "Figure 2. Plot.", True)]],
        ),
        ("10.1__b", 2016, [[(72, 300, "Figure 1. Micrograph.", True)]]),
    ):
        directory = layout.papers / str(year) / slug
        directory.mkdir(parents=True)
        build_fixture_pdf(directory / "paper.pdf", pages)

    (layout.reports / "local_corpus_inventory.csv").write_text(
        INVENTORY_HEADER
        + "doi:10.1/a,10.1/a,2015,data/papers/2015/10.1__a/paper.pdf,True\n"
        + "doi:10.1/b,10.1/b,2016,data/papers/2016/10.1__b/paper.pdf,False\n"
        + "doi:10.1/c,10.1/c,2016,,False\n",
        encoding="utf-8",
    )
    return layout


def test_sweep_papers_filters_vor_and_years(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)

    vor_only = sweep_papers(layout)
    assert [entry.paper.doi for entry in vor_only] == ["10.1/a"]

    every_edition = sweep_papers(layout, version_of_record_only=False)
    assert [entry.paper.doi for entry in every_edition] == ["10.1/a", "10.1/b"]

    year_filtered = sweep_papers(layout, version_of_record_only=False, years=(2016,))
    assert [entry.paper.doi for entry in year_filtered] == ["10.1/b"]


def test_sample_papers_is_deterministic(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = sweep_papers(layout, version_of_record_only=False)
    first = sample_papers(papers, 1, seed=7)
    second = sample_papers(papers, 1, seed=7)
    assert [entry.paper.doi for entry in first] == [entry.paper.doi for entry in second]
    assert sample_papers(papers, 5, seed=7) == papers


def test_exclude_reported_sweep_papers_uses_doi_column(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = sweep_papers(layout, version_of_record_only=False)
    report = tmp_path / "prior.csv"
    report.write_text("doi,metric\n10.1/a,1\n", encoding="utf-8")

    remaining = exclude_reported_sweep_papers(papers, [report])

    assert [entry.paper.doi for entry in remaining] == ["10.1/b"]


def test_run_sweep_scores_and_writes_reports(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = sweep_papers(layout, version_of_record_only=False)
    judge = ScriptedJudge(
        {
            1: verdict(1, compatible=True, passed_rules=("compliance:5.2.1",)),
            2: verdict(2, compatible=False),
        }
    )

    summary = run_sweep(
        layout,
        judge,
        RULES,
        papers,
        benchmark_definition={"partition": "test"},
    )

    assert summary["papers_evaluated"] == 2
    assert summary["evaluation_errors"] == 0
    assert summary["prompt_profile"] == "historical_2025_prose_v1"
    assert summary["benchmark_definition"] == {"partition": "test"}
    agreement = summary["agreement"]
    assert agreement["papers"] == 2
    assert agreement["counts"]["figures_total"]["exact"] == 2

    with (layout.reports / "evaluator_agreement.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["doi"]: row for row in csv.DictReader(handle)}
    assert rows["10.1/a"]["predicted_figures_sbol_visual_compatible"] == "1"
    assert rows["10.1/a"]["all_counts_exact"] == "False"
    assert rows["10.1/b"]["all_counts_exact"] == "False"

    verdicts = [
        json.loads(line)
        for line in (layout.reports / "evaluator_verdicts.jsonl").read_text().splitlines()
    ]
    assert verdicts[0]["doi"] == "10.1/a"
    assert len(verdicts[0]["figures"]) == 2

    payload = json.loads((layout.reports / "evaluator_agreement.json").read_text())
    assert payload["papers_attempted"] == 2


def test_run_sweep_resumes_only_successful_papers(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = sweep_papers(layout, version_of_record_only=False)

    class OnePaperFails:
        def judge(self, context):
            if "Micrograph" in context.caption_text:
                raise RuntimeError("temporary judge failure")
            return verdict(context.figure_number, compatible=context.figure_number == 1)

    first = run_sweep(
        layout,
        OnePaperFails(),
        RULES,
        papers,
        report_stem="resumable_agreement",
        workers=1,
    )

    assert first["papers_evaluated"] == 1
    assert first["evaluation_errors"] == 1
    assert (layout.reports / "resumable_agreement.checkpoint.jsonl").exists()

    replacement = ScriptedJudge({1: verdict(1, compatible=False)})
    second = run_sweep(
        layout,
        replacement,
        RULES,
        papers,
        report_stem="resumable_agreement",
        workers=1,
        resume=True,
    )

    assert second["resumed_papers"] == 1
    assert second["papers_evaluated"] == 2
    assert second["evaluation_errors"] == 0
    assert len(replacement.contexts) == 1
    assert not (layout.reports / "resumable_agreement.checkpoint.jsonl").exists()
