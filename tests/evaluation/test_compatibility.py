from __future__ import annotations

import csv
import json
from pathlib import Path

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.compatibility import (
    run_compatibility_benchmark,
    saturated_compatibility_papers,
    saturated_figures,
)

from ..evaluator.helpers import ScriptedJudge, verdict
from ..figures.helpers import build_fixture_pdf

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
        # zero compatible: every figure is a certain negative
        + "doi:10.1/neg,10.1/neg,2015,Negative,2,0,0,0,True,False\n"
        # all compatible: every figure is a certain positive
        + "doi:10.1/pos,10.1/pos,2016,Positive,1,1,1,0,True,False\n"
        # mixed: entails no figure-level label
        + "doi:10.1/mix,10.1/mix,2016,Mixed,2,1,1,0,True,False\n"
        # census disagrees with figures_total: untrustworthy labels
        + "doi:10.1/drift,10.1/drift,2017,Drift,5,0,0,0,True,False\n",
        encoding="utf-8",
    )

    for slug, year, pages in (
        (
            "10.1__neg",
            2015,
            [
                [
                    (72, 300, "Figure 1. A gel image.", True),
                    (72, 600, "Figure 2. A bar chart.", True),
                ]
            ],
        ),
        ("10.1__pos", 2016, [[(72, 300, "Figure 1. A genetic circuit.", True)]]),
        (
            "10.1__mix",
            2016,
            [[(72, 300, "Figure 1. Circuit.", True), (72, 600, "Figure 2. Plot.", True)]],
        ),
        ("10.1__drift", 2017, [[(72, 300, "Figure 1. Only one detected.", True)]]),
    ):
        directory = layout.papers / str(year) / slug
        directory.mkdir(parents=True)
        build_fixture_pdf(directory / "paper.pdf", pages)

    (layout.reports / "local_corpus_inventory.csv").write_text(
        INVENTORY_HEADER
        + "doi:10.1/neg,10.1/neg,2015,data/papers/2015/10.1__neg/paper.pdf,True\n"
        + "doi:10.1/pos,10.1/pos,2016,data/papers/2016/10.1__pos/paper.pdf,True\n"
        + "doi:10.1/mix,10.1/mix,2016,data/papers/2016/10.1__mix/paper.pdf,True\n"
        + "doi:10.1/drift,10.1/drift,2017,data/papers/2017/10.1__drift/paper.pdf,True\n",
        encoding="utf-8",
    )
    return layout


def test_saturated_papers_exclude_mixed_stages(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = saturated_compatibility_papers(layout)
    assert sorted(entry.paper.doi for entry in papers) == ["10.1/drift", "10.1/neg", "10.1/pos"]

    positives = saturated_compatibility_papers(layout, expected=True)
    assert [entry.paper.doi for entry in positives] == ["10.1/pos"]

    negatives = saturated_compatibility_papers(layout, expected=False)
    assert sorted(entry.paper.doi for entry in negatives) == ["10.1/drift", "10.1/neg"]


def test_saturated_figures_label_every_figure_and_skip_census_drift(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))

    assert {(figure.doi, figure.figure_number) for figure in figures} == {
        ("10.1/neg", 1),
        ("10.1/neg", 2),
        ("10.1/pos", 1),
    }
    assert all(not figure.expected_compatible for figure in figures if figure.doi == "10.1/neg")
    assert all(figure.expected_compatible for figure in figures if figure.doi == "10.1/pos")


def test_compatibility_benchmark_reports_boundary_errors(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    # Judge marks everything compatible: perfect recall, worst false-positive rate.
    judge = ScriptedJudge({1: verdict(1, compatible=True), 2: verdict(2, compatible=True)})

    summary = run_compatibility_benchmark(layout, judge, figures, workers=1)

    assert summary["figures"] == 3
    assert summary["judged"] == 3
    assert summary["certain_positive_figures"] == 1
    assert summary["certain_negative_figures"] == 2
    assert summary["recall"] == 1.0
    assert summary["false_positive_rate"] == 1.0
    assert summary["accuracy"] == round(1 / 3, 4)

    with (layout.reports / "compatibility_benchmark.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert {row["correct"] for row in rows} == {"True", "False"}

    payload = json.loads((layout.reports / "compatibility_benchmark.json").read_text())
    assert payload["judge_errors"] == 0
