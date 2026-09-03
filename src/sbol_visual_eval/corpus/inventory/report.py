"""The one-row-per-paper local coverage report and its corpus-level summary."""

from __future__ import annotations

from typing import Any

from ..config import YEARS
from ..layout import Layout
from ..util.storage import read_jsonl, utc_now, write_csv, write_json
from .collect import PaperInventory

LOCAL_INVENTORY_FIELDS = (
    "record_id",
    "doi",
    "year",
    "title",
    "has_local_article_pdf",
    "has_local_article_text",
    "has_local_source",
    "has_verified_local_article_pdf",
    "has_verified_local_article_text",
    "has_verified_local_source",
    "has_local_version_of_record_pdf",
    "article_pdf_count",
    "verified_article_pdf_count",
    "verified_article_text_count",
    "version_of_record_pdf_count",
    "preferred_pdf_path",
    "preferred_pdf_is_version_of_record",
    "article_pdf_paths_json",
    "verified_article_pdf_paths_json",
    "version_of_record_pdf_paths_json",
    "article_text_paths_json",
    "verified_article_text_paths_json",
    "article_artifacts_json",
    "verified_article_artifact_count",
    "invalid_artifact_count",
    "unverified_artifact_count",
    "artifact_issues_json",
    "source_versions_json",
    "pmcid",
    "pmc_status",
    "pmc_media_count",
    "pmc_image_count",
    "pmc_media_paths_json",
    "figures_total",
    "figures_sbol_visual_compatible",
    "figures_sbol_visual_compliant",
    "figures_best_practices",
    "has_compatible_figures",
    "all_compatible_figures_compliant",
)

HISTORICAL_COUNT_FIELDS = (
    "figures_total",
    "figures_sbol_visual_compatible",
    "figures_sbol_visual_compliant",
    "figures_best_practices",
)


def _summary(inventory: list[dict[str, Any]], orphan_root_pdfs: list[str]) -> dict[str, Any]:
    rows_with_pdf = [row for row in inventory if row["has_local_article_pdf"]]
    rows_with_verified_pdf = [row for row in inventory if row["has_verified_local_article_pdf"]]
    rows_with_version_of_record_pdf = [
        row for row in inventory if row["has_local_version_of_record_pdf"]
    ]
    rows_with_source = [row for row in inventory if row["has_local_source"]]
    rows_with_verified_source = [row for row in inventory if row["has_verified_local_source"]]
    compatible_rows = [row for row in inventory if row["has_compatible_figures"]]
    return {
        "generated_at": utc_now(),
        "papers_total": len(inventory),
        "papers_with_local_article_pdf": len(rows_with_pdf),
        "papers_with_local_text": sum(row["has_local_article_text"] for row in inventory),
        "papers_with_any_local_source": len(rows_with_source),
        "local_article_pdf_files": sum(row["article_pdf_count"] for row in inventory),
        "papers_with_verified_local_article_pdf": len(rows_with_verified_pdf),
        "papers_with_verified_local_text": sum(
            row["has_verified_local_article_text"] for row in inventory
        ),
        "papers_with_any_verified_local_source": len(rows_with_verified_source),
        "verified_local_article_pdf_files": sum(
            row["verified_article_pdf_count"] for row in inventory
        ),
        "verified_local_article_text_files": sum(
            row["verified_article_text_count"] for row in inventory
        ),
        "papers_with_local_version_of_record_pdf": len(rows_with_version_of_record_pdf),
        "local_version_of_record_pdf_files": sum(
            row["version_of_record_pdf_count"] for row in inventory
        ),
        "pmc_media_files": sum(row["pmc_media_count"] for row in inventory),
        "pmc_image_files": sum(row["pmc_image_count"] for row in inventory),
        "invalid_artifacts": sum(row["invalid_artifact_count"] for row in inventory),
        "unverified_artifacts": sum(row["unverified_artifact_count"] for row in inventory),
        "papers_with_artifact_issues": sum(
            bool(row["invalid_artifact_count"] or row["unverified_artifact_count"])
            for row in inventory
        ),
        "papers_with_compatible_figures": len(compatible_rows),
        "compatible_papers_with_local_article_pdf": sum(
            row["has_local_article_pdf"] for row in compatible_rows
        ),
        "compatible_papers_with_verified_local_article_pdf": sum(
            row["has_verified_local_article_pdf"] for row in compatible_rows
        ),
        "compatible_papers_with_local_version_of_record_pdf": sum(
            row["has_local_version_of_record_pdf"] for row in compatible_rows
        ),
        "historical_counts_represented_by_local_pdf_papers": {
            field: sum(row[field] for row in rows_with_pdf) for field in HISTORICAL_COUNT_FIELDS
        },
        "historical_counts_represented_by_verified_local_pdf_papers": {
            field: sum(row[field] for row in rows_with_verified_pdf)
            for field in HISTORICAL_COUNT_FIELDS
        },
        "historical_counts_represented_by_local_version_of_record_pdf_papers": {
            field: sum(row[field] for row in rows_with_version_of_record_pdf)
            for field in HISTORICAL_COUNT_FIELDS
        },
        "papers_with_local_pdf_by_year": {
            str(year): sum(
                row["year"] == year and row["has_local_article_pdf"] for row in inventory
            )
            for year in YEARS
        },
        "papers_with_verified_local_pdf_by_year": {
            str(year): sum(
                row["year"] == year and row["has_verified_local_article_pdf"] for row in inventory
            )
            for year in YEARS
        },
        "papers_with_local_version_of_record_pdf_by_year": {
            str(year): sum(
                row["year"] == year and row["has_local_version_of_record_pdf"] for row in inventory
            )
            for year in YEARS
        },
        "orphan_root_pdfs": sorted(orphan_root_pdfs),
        "report_path": "data/reports/local_corpus_inventory.csv",
    }


def build_local_inventory(layout: Layout) -> dict[str, Any]:
    source_path = layout.processed / "papers.jsonl"
    if not source_path.exists():
        raise FileNotFoundError("processed manifest is missing; run `sbol-visual-data build` first")
    records = read_jsonl(source_path)

    inventory: list[dict[str, Any]] = []
    orphan_root_pdfs: list[str] = []
    for record in records:
        paper = PaperInventory(layout, record)
        paper.collect()
        inventory.append(paper.row(orphan_root_pdfs))

    write_csv(layout.reports / "local_corpus_inventory.csv", inventory, LOCAL_INVENTORY_FIELDS)
    summary = _summary(inventory, orphan_root_pdfs)
    write_json(layout.reports / "local_corpus_inventory.json", summary)
    print(
        f"Local corpus inventory: {summary['papers_with_local_article_pdf']:,}/"
        f"{summary['papers_total']:,} papers have a local PDF "
        f"({summary['papers_with_verified_local_article_pdf']:,} verified); "
        f"{summary['papers_with_any_local_source']:,} have local PDF or text"
    )
    return summary
