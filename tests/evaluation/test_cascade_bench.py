from __future__ import annotations

import csv
import json
from pathlib import Path

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.cascade_bench import (
    cascade_figures,
    cascade_papers,
    run_cascade_benchmark,
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
        # every count equal: every figure is positive through the whole cascade
        + "doi:10.1/all,10.1/all,2015,All,2,2,2,2,True,False\n"
        # compliant everywhere but no best practice: every figure a BP negative
        + "doi:10.1/nobp,10.1/nobp,2016,NoBP,1,1,1,0,True,False\n"
        # partially saturated: entails no per-figure label
        + "doi:10.1/mix,10.1/mix,2016,Mixed,2,1,1,0,True,False\n",
        encoding="utf-8",
    )

    for slug, year, pages in (
        (
            "10.1__all",
            2015,
            [
                [
                    (72, 300, "Figure 1. Circuit one.", True),
                    (72, 600, "Figure 2. Circuit two.", True),
                ]
            ],
        ),
        ("10.1__nobp", 2016, [[(72, 300, "Figure 1. Plain circuit.", True)]]),
        (
            "10.1__mix",
            2016,
            [[(72, 300, "Figure 1. Circuit.", True), (72, 600, "Figure 2. Plot.", True)]],
        ),
    ):
        directory = layout.papers / str(year) / slug
        directory.mkdir(parents=True)
        build_fixture_pdf(directory / "paper.pdf", pages)

    (layout.reports / "local_corpus_inventory.csv").write_text(
        INVENTORY_HEADER
        + "doi:10.1/all,10.1/all,2015,data/papers/2015/10.1__all/paper.pdf,True\n"
        + "doi:10.1/nobp,10.1/nobp,2016,data/papers/2016/10.1__nobp/paper.pdf,True\n"
        + "doi:10.1/mix,10.1/mix,2016,data/papers/2016/10.1__mix/paper.pdf,True\n",
        encoding="utf-8",
    )
    return layout


def test_cascade_papers_keep_only_fully_entailed_shapes(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = cascade_papers(layout)
    assert sorted(entry.paper.doi for entry in papers) == ["10.1/all", "10.1/nobp"]


def test_cascade_figures_carry_per_stage_expectations(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = {
        (figure.doi, figure.figure_number): figure
        for figure in cascade_figures(layout, cascade_papers(layout))
    }
    assert set(figures) == {("10.1/all", 1), ("10.1/all", 2), ("10.1/nobp", 1)}

    positive = figures[("10.1/all", 1)]
    assert (
        positive.expected_compatible,
        positive.expected_compliant,
        positive.expected_best_practice,
    ) == (True, True, True)

    no_best_practice = figures[("10.1/nobp", 1)]
    assert (
        no_best_practice.expected_compatible,
        no_best_practice.expected_compliant,
        no_best_practice.expected_best_practice,
    ) == (True, True, False)


def test_cascade_benchmark_reports_blocking_rules(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = cascade_figures(layout, cascade_papers(layout))
    # Every figure compatible, but a best-practice rule fails on all of them:
    # correct for the no-best-practice paper, wrong for the all-positive paper.
    judge = ScriptedJudge(
        {
            number: verdict(
                number,
                compatible=True,
                passed_rules=("compliance:5.2.1", "compliance:5.2.6"),
                failed_rules=("best_practice:5.1.2",),
            )
            for number in (1, 2)
        }
    )

    summary = run_cascade_benchmark(layout, judge, RULES, figures, workers=1)

    assert summary["judged"] == 3
    assert summary["prompt_profile"] == "historical_2025_prose_v1"
    assert summary["compatible_accuracy"] == 1.0
    assert summary["compliant_accuracy"] == 1.0
    # Two all-positive figures wrongly denied best practice; the BP-negative is right.
    assert summary["best_practice_accuracy"] == round(1 / 3, 4)
    assert summary["expected_best_practice_figures"] == 2
    assert summary["rules_blocking_expected_best_practice"] == {"best_practice:5.1.2": 2}

    with (layout.reports / "cascade_benchmark.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert all(row["failed_rules"] == "best_practice:5.1.2" for row in rows)

    payload = json.loads((layout.reports / "cascade_benchmark.json").read_text())
    assert payload["judge_errors"] == 0


def test_cascade_benchmark_resumes_successful_figures(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = cascade_figures(layout, cascade_papers(layout))

    class OneFigureFails:
        def judge(self, context):
            if "Plain" in context.caption_text:
                raise RuntimeError("temporary judge failure")
            return verdict(
                context.figure_number,
                compatible=True,
                passed_rules=("compliance:5.2.1", "best_practice:5.1.2"),
            )

    first = run_cascade_benchmark(
        layout,
        OneFigureFails(),
        RULES,
        figures,
        workers=1,
        report_stem="resumable_cascade",
    )

    assert first["judged"] == 2
    assert first["judge_errors"] == 1
    assert (layout.reports / "resumable_cascade.checkpoint.jsonl").exists()

    replacement = ScriptedJudge(
        {
            1: verdict(
                1,
                compatible=True,
                passed_rules=("compliance:5.2.1",),
                failed_rules=("best_practice:5.1.2",),
            )
        }
    )
    second = run_cascade_benchmark(
        layout,
        replacement,
        RULES,
        figures,
        workers=1,
        report_stem="resumable_cascade",
        resume=True,
    )

    assert second["resumed_figures"] == 2
    assert second["judged"] == 3
    assert second["judge_errors"] == 0
    assert len(replacement.contexts) == 1
    assert not (layout.reports / "resumable_cascade.checkpoint.jsonl").exists()


def test_blocking_rule_counts_are_scoped_to_their_stage(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = cascade_figures(layout, cascade_papers(layout))
    # A compliance failure and a best-practice failure on every figure.
    judge = ScriptedJudge(
        {
            number: verdict(
                number,
                compatible=True,
                failed_rules=("compliance:5.2.1", "best_practice:5.1.2"),
            )
            for number in (1, 2)
        }
    )

    summary = run_cascade_benchmark(layout, judge, RULES, figures, workers=1)

    # Only compliance rules can explain a missed compliant verdict, and only
    # best-practice rules a missed best-practice verdict.
    assert summary["rules_blocking_expected_compliant"] == {"compliance:5.2.1": 3}
    assert summary["rules_blocking_expected_best_practice"] == {"best_practice:5.1.2": 2}
