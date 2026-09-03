from __future__ import annotations

import csv
import json
from pathlib import Path

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.reconciliation import build_figure_census

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
        + "doi:10.1/a,10.1/a,2015,Paper A,2,1,1,0,True,False\n"
        + "doi:10.1/b,10.1/b,2016,Paper B,3,0,0,0,True,False\n"
        + "doi:10.1/c,10.1/c,2016,Paper C,1,1,1,1,True,False\n",
        encoding="utf-8",
    )

    paper_a = layout.papers / "2015" / "10.1__a"
    paper_a.mkdir(parents=True)
    build_fixture_pdf(
        paper_a / "paper.pdf",
        [
            [
                (72, 300, "Figure 1. First construct.", True),
                (72, 500, "Figure 2. Second construct.", True),
            ]
        ],
    )
    paper_b = layout.papers / "2016" / "10.1__b"
    paper_b.mkdir(parents=True)
    build_fixture_pdf(
        paper_b / "paper.pdf",
        [[(72, 300, "Figure 1. Only detected figure.", True)]],
    )

    (layout.reports / "local_corpus_inventory.csv").write_text(
        INVENTORY_HEADER
        + "doi:10.1/a,10.1/a,2015,data/papers/2015/10.1__a/paper.pdf,True\n"
        + "doi:10.1/b,10.1/b,2016,data/papers/2016/10.1__b/paper.pdf,False\n"
        + "doi:10.1/c,10.1/c,2016,,False\n",
        encoding="utf-8",
    )
    return layout


def test_build_figure_census_reconciles_against_history(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    summary = build_figure_census(layout, workers=1)

    assert summary["all_pdfs"]["papers"] == 2
    assert summary["all_pdfs"]["exact"] == 1
    assert summary["version_of_record_pdfs"] == {
        "papers": 1,
        "censused": 1,
        "census_errors": 0,
        "exact": 1,
        "exact_rate": 1.0,
        "within_one": 1,
        "mean_absolute_error": 0.0,
        "over_count": 0,
        "under_count": 0,
    }
    assert summary["other_edition_pdfs"]["under_count"] == 1
    assert summary["by_year"]["2016"]["papers"] == 1

    with (layout.reports / "figure_census.csv").open(newline="", encoding="utf-8") as handle:
        rows = {row["doi"]: row for row in csv.DictReader(handle)}
    assert rows["10.1/a"]["exact"] == "True"
    assert rows["10.1/a"]["figure_numbers"] == "1 2"
    assert rows["10.1/b"]["count_error"] == "-2"
    assert "10.1/c" not in rows

    payload = json.loads((layout.reports / "figure_census.json").read_text(encoding="utf-8"))
    assert payload["all_pdfs"]["papers"] == 2
