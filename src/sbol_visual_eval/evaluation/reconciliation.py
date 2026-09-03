"""Reconciling the figure census against the historical ``figures_total`` labels.

Every locally available paper PDF is censused and compared with the
paper's historical figure count. The per-paper rows and the summary land
in ``data/reports/figure_census.csv`` and ``figure_census.json``; the
summary separates Version-of-Record PDFs from other editions because
reviewers scored the published article and other editions can
legitimately differ in figure count.
"""

from __future__ import annotations

import csv
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import utc_now, write_csv, write_json
from ..figures import census_pdf, scope_for_pdf
from .groundtruth import ground_truth_by_doi, load_ground_truth

FIGURE_CENSUS_FIELDS = (
    "record_id",
    "doi",
    "year",
    "pdf_path",
    "pdf_is_version_of_record",
    "page_count",
    "scoped_first_page",
    "scoped_last_page",
    "predicted_figures_total",
    "historical_figures_total",
    "count_error",
    "exact",
    "figure_numbers",
    "missing_figure_numbers",
    "contiguous",
    "census_error",
)


def _census_row(task: dict[str, Any]) -> dict[str, Any]:
    row = dict(task)
    pdf_path = Path(row.pop("absolute_pdf_path"))
    try:
        census = census_pdf(pdf_path, scope_for_pdf(pdf_path))
    except Exception as error:  # noqa: BLE001 - one broken PDF must not stop the sweep
        row.update(
            {
                "page_count": "",
                "scoped_first_page": "",
                "scoped_last_page": "",
                "predicted_figures_total": "",
                "count_error": "",
                "exact": False,
                "figure_numbers": "",
                "missing_figure_numbers": "",
                "contiguous": "",
                "census_error": f"{type(error).__name__}: {error}",
            }
        )
        return row
    predicted = census.figure_count
    historical = row["historical_figures_total"]
    row.update(
        {
            "page_count": census.page_count,
            "scoped_first_page": census.scoped_first_page,
            "scoped_last_page": census.scoped_last_page,
            "predicted_figures_total": predicted,
            "count_error": predicted - historical,
            "exact": predicted == historical,
            "figure_numbers": " ".join(str(number) for number in census.figure_numbers),
            "missing_figure_numbers": " ".join(
                str(number) for number in census.missing_figure_numbers
            ),
            "contiguous": census.contiguous,
            "census_error": "",
        }
    )
    return row


def _census_tasks(layout: Layout, limit: int | None) -> list[dict[str, Any]]:
    ground_truth = ground_truth_by_doi(load_ground_truth(layout))
    tasks = []
    inventory_path = layout.reports / "local_corpus_inventory.csv"
    with inventory_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            paper = ground_truth.get(row["doi"])
            if not row["preferred_pdf_path"] or paper is None or not paper.scoreable:
                continue
            tasks.append(
                {
                    "record_id": paper.record_id,
                    "doi": paper.doi,
                    "year": paper.year,
                    "pdf_path": row["preferred_pdf_path"],
                    "pdf_is_version_of_record": row["preferred_pdf_is_version_of_record"] == "True",
                    "historical_figures_total": paper.score.figures_total,
                    "absolute_pdf_path": str(layout.root / row["preferred_pdf_path"]),
                }
            )
    tasks.sort(key=lambda task: (task["year"], task["doi"]))
    return tasks[:limit] if limit else tasks


def _subset_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    censused = [row for row in rows if not row["census_error"]]
    exact = [row for row in censused if row["exact"]]
    return {
        "papers": len(rows),
        "censused": len(censused),
        "census_errors": len(rows) - len(censused),
        "exact": len(exact),
        "exact_rate": round(len(exact) / len(censused), 4) if censused else 0.0,
        "within_one": sum(abs(row["count_error"]) <= 1 for row in censused),
        "mean_absolute_error": (
            round(sum(abs(row["count_error"]) for row in censused) / len(censused), 4)
            if censused
            else 0.0
        ),
        "over_count": sum(row["count_error"] > 0 for row in censused),
        "under_count": sum(row["count_error"] < 0 for row in censused),
    }


def build_figure_census(
    layout: Layout, *, workers: int = 8, limit: int | None = None
) -> dict[str, Any]:
    """Census every preferred local PDF and reconcile against historical counts."""
    tasks = _census_tasks(layout, limit)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_census_row, tasks, chunksize=8))

    vor_rows = [row for row in rows if row["pdf_is_version_of_record"]]
    other_rows = [row for row in rows if not row["pdf_is_version_of_record"]]
    summary = {
        "generated_at": utc_now(),
        "all_pdfs": _subset_summary(rows),
        "version_of_record_pdfs": _subset_summary(vor_rows),
        "other_edition_pdfs": _subset_summary(other_rows),
        "by_year": {
            str(year): _subset_summary([row for row in rows if row["year"] == year])
            for year in sorted({row["year"] for row in rows})
        },
        "report_path": "data/reports/figure_census.csv",
    }

    write_csv(layout.reports / "figure_census.csv", rows, FIGURE_CENSUS_FIELDS)
    write_json(layout.reports / "figure_census.json", summary)
    return summary
