"""Corpus integrity checks: raw checksums, processed rebuilds, and local artifacts."""

from __future__ import annotations

import csv
import json

from ..build import aggregate_years, invariant_violations
from ..config import YEARS
from ..layout import Layout
from ..schema import COUNT_FIELDS
from ..sources import parse_rubric, parse_website
from ..util.storage import read_jsonl, sha256_file
from .artifacts import validate_local_article_pdfs
from .pmc import validate_pmc_manifests
from .processed import (
    EXPECTED_OVERALL,
    _manifest_checksum_entries,
    _validate_file_checksum,
    _validate_processed_outputs,
)

__all__ = [
    "validate_corpus",
    "validate_local_article_pdfs",
    "validate_pmc_manifests",
]


def validate_corpus(layout: Layout) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    qc_path = layout.reports / "quality_control.json"
    if not qc_path.exists():
        return ["quality-control report is missing; run `sbol-visual-data build`"], warnings
    qc = json.loads(qc_path.read_text(encoding="utf-8"))

    sources_path = layout.raw / "SOURCES.json"
    if not sources_path.exists():
        return ["source manifest is missing; run `sbol-visual-data acquire`"], warnings
    source_manifest = json.loads(sources_path.read_text(encoding="utf-8"))
    for entry in _manifest_checksum_entries(source_manifest):
        if entry.get("path"):
            _validate_file_checksum(layout, entry["path"], entry.get("sha256"), errors)
    errors.extend(_validate_processed_outputs(layout, qc, source_manifest))

    papers_path = layout.processed / "papers.jsonl"
    if not papers_path.exists():
        return [*errors, "processed paper manifest is missing"], warnings
    records = read_jsonl(papers_path)
    record_ids = [record.get("record_id") for record in records]
    dois = [record.get("doi") for record in records if record.get("doi")]
    totals_by_year = aggregate_years(records)
    totals = {field: sum(year[field] for year in totals_by_year.values()) for field in COUNT_FIELDS}
    for field, expected in EXPECTED_OVERALL.items():
        actual = len(records) if field == "papers_total" else totals.get(field)
        if actual != expected:
            errors.append(f"{field}: expected {expected}, found {actual}")
    if len(dois) != len(records):
        errors.append(f"only {len(dois)} of {len(records)} paper records contain a DOI")
    if len(set(dois)) != len(dois):
        errors.append("resolved DOIs are not unique")
    if len(set(record_ids)) != len(record_ids):
        errors.append("record IDs are not unique")
    rubric_count = len(parse_rubric(layout))
    if rubric_count != 31:
        errors.append(f"expected 31 historical rubric rules, found {rubric_count}")
    website_rows, website_totals = parse_website(layout)
    if len(website_rows) != 1008:
        errors.append(f"expected 1,008 website table rows, found {len(website_rows)}")
    violations = invariant_violations([dict(record) for record in records])
    if len(violations) != 1:
        errors.append(
            f"expected one preserved source count-invariant violation, found {len(violations)}"
        )
    papers_csv = layout.processed / "papers.csv"
    if not papers_csv.exists():
        errors.append("processed papers.csv is missing")
    else:
        with papers_csv.open(encoding="utf-8", newline="") as handle:
            csv_rows = sum(1 for _ in csv.DictReader(handle))
        if csv_rows != len(records):
            errors.append(f"papers.csv contains {csv_rows} rows; expected {len(records)}")

    shape = qc["dataset_shape"]
    if shape.get("papers") != len(records):
        errors.append("quality_control.json paper count does not match papers.jsonl")
    if qc.get("dataset_release", {}).get("source_manifest_sha256") != sha256_file(sources_path):
        errors.append("quality_control.json does not reference the current source manifest hash")
    projection = qc["website_projection"]
    for field in (
        "matched_rows",
        "rows_with_count_mismatch",
        "rows_violating_filter",
        "rows_with_star_mismatch",
    ):
        expected = len(website_rows) if field == "matched_rows" else 0
        if projection[field] != expected:
            errors.append(
                f"website projection {field}: expected {expected}, found {projection[field]}"
            )
    mismatched_years = [
        year
        for year in YEARS
        if any(website_totals[year][field] != totals_by_year[year][field] for field in COUNT_FIELDS)
    ]
    if mismatched_years:
        warnings.append(
            "website headline totals differ from yearly workbook rows for: "
            + ", ".join(str(year) for year in mismatched_years)
        )
    if violations:
        warnings.append("one historical workbook row requires adjudication and was not repaired")

    errors.extend(validate_local_article_pdfs(layout, records))
    pmc_errors, pmc_warnings = validate_pmc_manifests(layout, records)
    errors.extend(pmc_errors)
    warnings.extend(pmc_warnings)
    return errors, warnings
