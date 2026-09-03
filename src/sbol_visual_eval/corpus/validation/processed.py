"""Validation of raw-source checksums and deterministic processed-output rebuilds."""

from __future__ import annotations

import csv
import hashlib
import json
from typing import Any

from ..build import (
    aggregate_years,
    invariant_violations,
    match_website_rows,
    processed_output_payloads,
    source_comparison_rows,
)
from ..config import YEARS
from ..layout import Layout
from ..metadata import attach_europepmc, attach_openalex, match_crossref
from ..schema import PROCESSED_OUTPUT_FILENAMES
from ..sources import (
    extract_yearly_records,
    parse_overview_workbook,
    parse_rubric,
    parse_website,
    parse_yearly_cached_summaries,
)
from ..util.storage import read_gzip_json, read_jsonl, sha256_file

EXPECTED_OVERALL = {
    "papers_total": 2802,
    "figures_total": 14638,
    "figures_sbol_visual_compatible": 3710,
    "figures_sbol_visual_compliant": 2519,
    "figures_best_practices": 1342,
}


def _manifest_checksum_entries(source_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = list(source_manifest.get("annotations", []))
    for key in ("rubric", "specification"):
        if source_manifest.get(key):
            entries.append(source_manifest[key])
    entries.extend(source_manifest.get("website", {}).get("files", []))
    repository_license = source_manifest.get("website", {}).get("repository_license")
    if repository_license:
        entries.append(repository_license)
    entries.extend(source_manifest.get("metadata_snapshots", {}).values())
    return entries


def _validate_file_checksum(
    layout: Layout, relative_path: str, expected_sha256: str | None, errors: list[str]
) -> None:
    path = (layout.root / relative_path).resolve()
    if not path.is_relative_to(layout.root.resolve()):
        errors.append(f"manifest path escapes the repository root: {relative_path}")
    elif not path.exists():
        errors.append(f"manifest file is missing: {relative_path}")
    elif expected_sha256 and sha256_file(path) != expected_sha256:
        errors.append(f"SHA-256 mismatch: {relative_path}")


def _metadata_snapshot(
    layout: Layout, source_manifest: dict[str, Any], name: str
) -> dict[str, Any]:
    entry = source_manifest.get("metadata_snapshots", {}).get(name)
    if not entry or not entry.get("path"):
        raise FileNotFoundError(f"source manifest has no {name} metadata snapshot")
    path = (layout.root / entry["path"]).resolve()
    if not path.is_relative_to(layout.root.resolve()):
        raise ValueError(f"{name} metadata snapshot path escapes the repository root")
    return read_gzip_json(path)


def _rebuild_processed_payloads(
    layout: Layout, source_manifest: dict[str, Any]
) -> dict[str, tuple[bytes, int]]:
    records = extract_yearly_records(layout)
    rubric = parse_rubric(layout)
    website_records, website_totals = parse_website(layout)
    overview_totals = parse_overview_workbook(layout)
    cached_summary_totals = parse_yearly_cached_summaries(layout)

    records, _ = match_crossref(records, _metadata_snapshot(layout, source_manifest, "crossref"))
    records = attach_openalex(records, _metadata_snapshot(layout, source_manifest, "openalex"))
    records = attach_europepmc(records, _metadata_snapshot(layout, source_manifest, "europepmc"))
    website_records = match_website_rows(website_records, records)
    invariant_violations(records)
    records.sort(
        key=lambda row: (
            row["year"],
            row["issue_month_number"],
            row["annotation_row"],
        )
    )
    comparison_rows = source_comparison_rows(
        aggregate_years(records),
        cached_summary_totals,
        website_totals,
        overview_totals,
    )
    return processed_output_payloads(records, rubric, website_records, comparison_rows)


def _validate_processed_outputs(
    layout: Layout, qc: dict[str, Any], source_manifest: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    source_manifest_path = layout.raw / "SOURCES.json"
    source_manifest_sha256 = sha256_file(source_manifest_path)
    expected_manifest_path = layout.processed / "MANIFEST.json"
    manifest_reference = qc.get("dataset_release", {}).get("processed_manifest", {})
    expected_relative_manifest_path = str(expected_manifest_path.relative_to(layout.root))

    if manifest_reference.get("path") != expected_relative_manifest_path:
        errors.append("quality_control.json does not reference data/processed/MANIFEST.json")
    if not expected_manifest_path.exists():
        errors.append("processed output manifest is missing; run `sbol-visual-data build`")
        return errors
    actual_manifest_sha256 = sha256_file(expected_manifest_path)
    if manifest_reference.get("sha256") != actual_manifest_sha256:
        errors.append("quality_control.json does not reference the current processed manifest hash")

    try:
        manifest = json.loads(expected_manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        errors.append(f"cannot read processed output manifest: {error}")
        return errors
    if manifest.get("schema_version") != 1:
        errors.append(
            f"processed output manifest schema: expected 1, found "
            f"{manifest.get('schema_version')!r}"
        )
    if manifest.get("source_manifest_path") != "data/raw/SOURCES.json":
        errors.append("processed output manifest references an unexpected source manifest")
    if manifest.get("source_manifest_sha256") != source_manifest_sha256:
        errors.append("processed outputs were not built from the current source manifest")

    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        errors.append("processed output manifest has no output entries")
        return errors
    missing = sorted(set(PROCESSED_OUTPUT_FILENAMES) - set(outputs))
    unexpected = sorted(set(outputs) - set(PROCESSED_OUTPUT_FILENAMES))
    if missing:
        errors.append("processed output manifest is missing: " + ", ".join(missing))
    if unexpected:
        errors.append("processed output manifest has unexpected entries: " + ", ".join(unexpected))

    actual_row_counts: dict[str, int] = {}
    for filename in PROCESSED_OUTPUT_FILENAMES:
        entry = outputs.get(filename)
        if not isinstance(entry, dict):
            continue
        expected_path = layout.processed / filename
        relative_path = str(expected_path.relative_to(layout.root))
        if entry.get("path") != relative_path:
            errors.append(f"processed manifest path mismatch for {filename}")
        _validate_file_checksum(layout, relative_path, entry.get("sha256"), errors)
        if not expected_path.exists():
            continue
        if entry.get("bytes") != expected_path.stat().st_size:
            errors.append(f"processed manifest byte count mismatch for {filename}")
        try:
            if filename.endswith(".jsonl"):
                row_count = len(read_jsonl(expected_path))
            else:
                with expected_path.open(encoding="utf-8", newline="") as handle:
                    row_count = sum(1 for _ in csv.DictReader(handle))
        except (csv.Error, json.JSONDecodeError, OSError) as error:
            errors.append(f"cannot count processed rows in {filename}: {error}")
            continue
        actual_row_counts[filename] = row_count
        if entry.get("rows") != row_count:
            errors.append(f"processed manifest row count mismatch for {filename}")

    semantic_row_counts = {
        "papers.jsonl": EXPECTED_OVERALL["papers_total"],
        "papers.csv": EXPECTED_OVERALL["papers_total"],
        "rubric.csv": 31,
        "website_tables.csv": 1008,
        "yearly_totals.csv": len(YEARS),
    }
    for filename, expected in semantic_row_counts.items():
        actual = actual_row_counts.get(filename)
        if actual is not None and actual != expected:
            errors.append(f"{filename} contains {actual} rows; expected semantic count {expected}")

    try:
        expected_payloads = _rebuild_processed_payloads(layout, source_manifest)
    except (KeyError, OSError, TypeError, ValueError) as error:
        errors.append(
            "could not deterministically rebuild processed outputs from raw sources: "
            f"{type(error).__name__}: {error}"
        )
        return errors
    for filename in PROCESSED_OUTPUT_FILENAMES:
        path = layout.processed / filename
        if not path.exists():
            continue
        expected_payload, expected_rows = expected_payloads[filename]
        if sha256_file(path) != hashlib.sha256(expected_payload).hexdigest():
            errors.append(
                f"processed output does not match a deterministic rebuild from raw sources: "
                f"{filename}"
            )
        if actual_row_counts.get(filename) != expected_rows:
            errors.append(f"processed output row count does not match raw sources: {filename}")
    return errors
