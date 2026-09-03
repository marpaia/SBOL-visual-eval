"""Deterministic construction of the processed corpus outputs and QC report."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rapidfuzz.fuzz import ratio

from .config import RETROSPECTIVE_DOI, SBOL_VISUAL_3_URL, YEARS
from .layout import Layout
from .metadata import (
    attach_europepmc,
    attach_openalex,
    fetch_crossref,
    fetch_europepmc,
    fetch_openalex,
    match_crossref,
)
from .schema import (
    COUNT_FIELDS,
    PAPER_CSV_FIELDS,
    PROCESSED_OUTPUT_FILENAMES,
    RUBRIC_CSV_FIELDS,
    WEBSITE_CSV_FIELDS,
)
from .sources import (
    extract_yearly_records,
    parse_overview_workbook,
    parse_rubric,
    parse_website,
    parse_yearly_cached_summaries,
)
from .util.storage import (
    atomic_write_bytes,
    serialize_csv,
    serialize_jsonl,
    sha256_file,
    utc_now,
    write_csv,
    write_gzip_json,
    write_json,
)
from .util.text import normalize_title


def aggregate_years(records: Sequence[dict[str, Any]]) -> dict[int, dict[str, int]]:
    fields = (
        "figures_total",
        "figures_sbol_visual_compatible",
        "figures_sbol_visual_compliant",
        "figures_best_practices",
    )
    output: dict[int, dict[str, int]] = {}
    for year in YEARS:
        rows = [record for record in records if record["year"] == year]
        output[year] = {
            "papers_total": len(rows),
            **{field: sum(int(record[field]) for record in rows) for field in fields},
            "papers_with_compatible_figures": sum(
                record["has_compatible_figures"] for record in rows
            ),
            "papers_with_compliant_figures": sum(
                record["has_compliant_figures"] for record in rows
            ),
            "papers_all_compatible_compliant": sum(
                record["all_compatible_figures_compliant"] for record in rows
            ),
            "papers_with_best_practice_figures": sum(
                record["has_best_practice_figures"] for record in rows
            ),
            "papers_all_compatible_best_practice": sum(
                record["all_compatible_figures_best_practice"] for record in rows
            ),
        }
    return output


def match_website_rows(
    website_records: list[dict[str, Any]], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_year[record["year"]].append(record)
    for website_record in website_records:
        candidates = by_year[website_record["year"]]
        source_normalized = normalize_title(website_record["title_website"])
        ranked = sorted(
            [
                (
                    ratio(source_normalized, normalize_title(candidate["title_source"])),
                    candidate,
                )
                for candidate in candidates
            ],
            key=lambda pair: pair[0],
            reverse=True,
        )
        score, selected = ranked[0]
        margin = score - ranked[1][0]
        if score < 85 or margin < 15:
            website_record.update(
                {
                    "matched_record_id": None,
                    "matched_doi": None,
                    "title_match_score": round(score / 100, 6),
                    "matches_yearly_workbook_counts": False,
                    "matches_fully_compliant_filter": False,
                    "matches_star_semantics": False,
                }
            )
            continue
        website_record.update(
            {
                "matched_record_id": selected["record_id"],
                "matched_doi": selected.get("doi"),
                "title_match_score": round(score / 100, 6),
                "matches_yearly_workbook_counts": (
                    website_record["figures_sbol_visual_compliant"]
                    == selected["figures_sbol_visual_compliant"]
                    and website_record["figures_best_practices"]
                    == selected["figures_best_practices"]
                ),
                "matches_fully_compliant_filter": selected["all_compatible_figures_compliant"],
                "matches_star_semantics": website_record["star"]
                == selected["all_compatible_figures_best_practice"],
            }
        )
    return website_records


def _flat_paper_record(record: dict[str, Any]) -> dict[str, Any]:
    flat = dict(record)
    flat["authors_json"] = json.dumps(record.get("authors", []), ensure_ascii=False)
    flat["annotation_comments_json"] = json.dumps(
        record.get("annotation_comments", []), ensure_ascii=False
    )
    flat["crossref_license_urls_json"] = json.dumps(
        record.get("crossref_license_urls", []), ensure_ascii=False
    )
    flat["crossref_full_text_links_json"] = json.dumps(
        record.get("crossref_full_text_links", []), ensure_ascii=False
    )
    flat["pdf_candidate_count"] = len(record.get("pdf_candidates", []))
    return flat


def _rubric_csv_rows(rubric: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for rule in rubric:
        row = dict(rule)
        row["notes_json"] = json.dumps(rule.get("notes", []), ensure_ascii=False)
        rows.append(row)
    return rows


def processed_output_payloads(
    records: list[dict[str, Any]],
    rubric: list[dict[str, Any]],
    website_records: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
) -> dict[str, tuple[bytes, int]]:
    if not comparison_rows:
        raise ValueError("yearly comparison table cannot be empty")
    return {
        "papers.jsonl": (serialize_jsonl(records), len(records)),
        "papers.csv": (
            serialize_csv([_flat_paper_record(record) for record in records], PAPER_CSV_FIELDS),
            len(records),
        ),
        "rubric.csv": (
            serialize_csv(_rubric_csv_rows(rubric), RUBRIC_CSV_FIELDS),
            len(rubric),
        ),
        "website_tables.csv": (
            serialize_csv(website_records, WEBSITE_CSV_FIELDS),
            len(website_records),
        ),
        "yearly_totals.csv": (
            serialize_csv(comparison_rows, tuple(comparison_rows[0])),
            len(comparison_rows),
        ),
    }


def _write_processed_outputs(
    layout: Layout,
    records: list[dict[str, Any]],
    rubric: list[dict[str, Any]],
    website_records: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    source_manifest_sha256: str,
) -> dict[str, Any]:
    payloads = processed_output_payloads(records, rubric, website_records, comparison_rows)
    outputs: dict[str, dict[str, Any]] = {}
    for filename in PROCESSED_OUTPUT_FILENAMES:
        payload, row_count = payloads[filename]
        path = layout.processed / filename
        atomic_write_bytes(path, payload)
        outputs[filename] = {
            "path": str(path.relative_to(layout.root)),
            "format": path.suffix.removeprefix("."),
            "rows": row_count,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    manifest = {
        "schema_version": 1,
        "source_manifest_path": "data/raw/SOURCES.json",
        "source_manifest_sha256": source_manifest_sha256,
        "outputs": outputs,
    }
    manifest_path = layout.processed / "MANIFEST.json"
    write_json(manifest_path, manifest)
    return {
        "path": str(manifest_path.relative_to(layout.root)),
        "sha256": sha256_file(manifest_path),
        "schema_version": manifest["schema_version"],
    }


def invariant_violations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for record in records:
        counts = [
            record["figures_total"],
            record["figures_sbol_visual_compatible"],
            record["figures_sbol_visual_compliant"],
            record["figures_best_practices"],
        ]
        valid = all(value >= 0 for value in counts) and (
            counts[0] >= counts[1] >= counts[2] >= counts[3]
        )
        record["historical_count_invariant_valid"] = valid
        record["requires_adjudication"] = not valid
        if not valid:
            violations.append(
                {
                    "record_id": record["record_id"],
                    "doi": record.get("doi"),
                    "title": record["title_source"],
                    "year": record["year"],
                    "issue_month": record["issue_month"],
                    "annotation_sheet": record["annotation_sheet"],
                    "annotation_row": record["annotation_row"],
                    "figures_total": counts[0],
                    "figures_sbol_visual_compatible": counts[1],
                    "figures_sbol_visual_compliant": counts[2],
                    "figures_best_practices": counts[3],
                    "violation": "expected total >= compatible >= compliant >= best >= 0",
                }
            )
    return violations


def source_comparison_rows(
    workbook_totals: dict[int, dict[str, int]],
    cached_summary_totals: dict[int, dict[str, int]],
    website_totals: dict[int, dict[str, int]],
    overview_totals: dict[int, dict[str, int]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for year in YEARS:
        row: dict[str, Any] = {"year": year}
        for source_name, source in (
            ("yearly_workbook_rows", workbook_totals),
            ("yearly_workbook_cached_summary", cached_summary_totals),
            ("website_headline", website_totals),
            ("overview_workbook", overview_totals),
        ):
            values = source.get(year, {})
            for field in COUNT_FIELDS:
                row[f"{source_name}_{field}"] = values.get(field)
        for comparison_name, source in (
            ("cached_summary_minus_yearly", cached_summary_totals),
            ("website_minus_yearly", website_totals),
            ("overview_minus_yearly", overview_totals),
        ):
            for field in COUNT_FIELDS:
                value = source.get(year, {}).get(field)
                baseline = workbook_totals[year][field]
                row[f"{comparison_name}_{field}"] = value - baseline if value is not None else None
        row["website_matches_yearly_workbook"] = all(
            website_totals.get(year, {}).get(field) == workbook_totals[year][field]
            for field in COUNT_FIELDS
        )
        row["cached_summary_matches_yearly_workbook"] = all(
            cached_summary_totals.get(year, {}).get(field) == workbook_totals[year][field]
            for field in COUNT_FIELDS
        )
        row["overview_matches_yearly_workbook"] = all(
            overview_totals.get(year, {}).get(field) == workbook_totals[year][field]
            for field in COUNT_FIELDS
        )
        output.append(row)
    return output


def _snapshot_metadata_payloads(
    layout: Layout,
    crossref_payload: dict[str, Any],
    openalex_payload: dict[str, Any],
    europepmc_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for name, payload in (
        ("crossref", crossref_payload),
        ("openalex", openalex_payload),
        ("europepmc", europepmc_payload),
    ):
        path = layout.metadata / f"{name}_response.json.gz"
        write_gzip_json(path, payload)
        snapshots[name] = {
            "retrieved_at": payload.get("retrieved_at"),
            "request_url": payload.get("request_url"),
            "request_filter": payload.get("request_filter"),
            "journal_issn": payload.get("journal_issn"),
            "records": len(payload.get("items", [])),
            "path": str(path.relative_to(layout.root)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    sources_path = layout.raw / "SOURCES.json"
    source_manifest = json.loads(sources_path.read_text(encoding="utf-8"))
    source_manifest["metadata_snapshots"] = snapshots
    retrieved_at_values: list[tuple[datetime, str]] = []

    def collect_retrieved_at(payload: Any) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key == "retrieved_at" and isinstance(value, str):
                    try:
                        parsed = datetime.fromisoformat(value)
                    except ValueError:
                        continue
                    if parsed.tzinfo is not None:
                        retrieved_at_values.append((parsed, value))
                else:
                    collect_retrieved_at(value)
        elif isinstance(payload, list):
            for value in payload:
                collect_retrieved_at(value)

    collect_retrieved_at(source_manifest)
    if retrieved_at_values:
        source_manifest["manifest_updated_at"] = max(retrieved_at_values)[1]
    write_json(sources_path, source_manifest)
    return snapshots


def build_corpus(
    layout: Layout, *, offline: bool = False, refresh_metadata: bool = False
) -> dict[str, Any]:
    layout.ensure_directories()
    sources_path = layout.raw / "SOURCES.json"
    if not sources_path.exists():
        raise FileNotFoundError("raw sources are missing; run `sbol-visual-data acquire` first")

    records = extract_yearly_records(layout)
    rubric = parse_rubric(layout)
    website_records, website_totals = parse_website(layout)
    overview_totals = parse_overview_workbook(layout)
    cached_summary_totals = parse_yearly_cached_summaries(layout)

    crossref_payload = fetch_crossref(layout, offline=offline, refresh=refresh_metadata)
    records, match_review = match_crossref(records, crossref_payload)
    openalex_payload = fetch_openalex(layout, offline=offline, refresh=refresh_metadata)
    records = attach_openalex(records, openalex_payload)
    europepmc_payload = fetch_europepmc(layout, offline=offline, refresh=refresh_metadata)
    records = attach_europepmc(records, europepmc_payload)
    metadata_snapshots = _snapshot_metadata_payloads(
        layout, crossref_payload, openalex_payload, europepmc_payload
    )
    website_records = match_website_rows(website_records, records)
    count_violations = invariant_violations(records)

    records.sort(key=lambda row: (row["year"], row["issue_month_number"], row["annotation_row"]))
    workbook_totals = aggregate_years(records)
    comparison_rows = source_comparison_rows(
        workbook_totals, cached_summary_totals, website_totals, overview_totals
    )

    source_manifest_sha256 = sha256_file(sources_path)
    processed_manifest = _write_processed_outputs(
        layout,
        records,
        rubric,
        website_records,
        comparison_rows,
        source_manifest_sha256,
    )
    write_csv(
        layout.reports / "metadata_match_review.csv",
        match_review,
        (
            "year",
            "issue_month",
            "title_source",
            "match_method",
            "title_score",
            "runner_up_margin",
            "matched_doi",
            "matched_title",
            "candidate_volume",
            "candidate_issue",
            "review_status",
            "review_rationale",
        ),
    )

    record_ids = [record["record_id"] for record in records]
    dois = [record["doi"] for record in records if record.get("doi")]
    website_matched_ids = {
        row["matched_record_id"] for row in website_records if row.get("matched_record_id")
    }
    expected_website_ids = {
        record["record_id"] for record in records if record["all_compatible_figures_compliant"]
    }
    website_missing = sorted(expected_website_ids - website_matched_ids)
    website_extra = sorted(website_matched_ids - expected_website_ids)
    overall = {
        field: sum(year[field] for year in workbook_totals.values()) for field in COUNT_FIELDS
    }
    qc = {
        "generated_at": utc_now(),
        "dataset_release": {
            "retrospective_doi": RETROSPECTIVE_DOI,
            "annotation_source_precedence": "monthly rows in supplements s002-s013",
            "label_granularity": "paper_aggregate_counts",
            "figure_identities_available": False,
            "historical_rubric_id": "historical_2025_rubric_v1",
            "strict_specification_id": "sbol_visual_3_0_strict",
            "strict_specification_url": SBOL_VISUAL_3_URL,
            "metadata_snapshots": metadata_snapshots,
            "source_manifest_path": str(sources_path.relative_to(layout.root)),
            "source_manifest_sha256": source_manifest_sha256,
            "processed_manifest": processed_manifest,
        },
        "dataset_shape": {
            "papers": len(records),
            "unique_record_ids": len(set(record_ids)),
            "resolved_dois": len(dois),
            "unique_resolved_dois": len(set(dois)),
            "rubric_rules": len(rubric),
            "website_table_rows": len(website_records),
        },
        "yearly_workbook_row_totals": overall,
        "metadata_resolution": {
            "crossref_match_methods": dict(
                Counter(record["crossref_match_method"] for record in records)
            ),
            "records_requiring_match_review": len(match_review),
            "openalex_records_matched": sum(
                record.get("openalex_id") is not None for record in records
            ),
            "europepmc_records_matched": sum(
                record.get("europepmc_id") is not None for record in records
            ),
            "europepmc_records_with_pmcid": sum(
                record.get("europepmc_pmcid") is not None for record in records
            ),
            "europepmc_external_pdf_candidates": sum(
                record.get("europepmc_external_pdf_candidates", 0) for record in records
            ),
            "papers_open_access": sum(record.get("is_oa") is True for record in records),
            "papers_with_any_pdf_candidate": sum(
                bool(record.get("pdf_candidates")) for record in records
            ),
            "papers_download_eligible_default": sum(
                record["download_eligible"] for record in records
            ),
            "open_access_statuses": dict(
                Counter(record.get("oa_status") or "unmatched" for record in records)
            ),
        },
        "count_invariant_violations": count_violations,
        "source_layer_comparison": comparison_rows,
        "website_projection": {
            "description": "website tables project papers with compatible = compliant > 0",
            "matched_rows": sum(
                row.get("matched_record_id") is not None for row in website_records
            ),
            "rows_with_count_mismatch": sum(
                not row.get("matches_yearly_workbook_counts") for row in website_records
            ),
            "rows_violating_filter": sum(
                not row.get("matches_fully_compliant_filter") for row in website_records
            ),
            "rows_with_star_mismatch": sum(
                not row.get("matches_star_semantics") for row in website_records
            ),
            "expected_rows_from_yearly_workbooks": len(expected_website_ids),
            "missing_from_website": website_missing,
            "unexpected_on_website": website_extra,
        },
        "known_limitations": [
            "The released labels are paper-level figure counts; figure identities are absent.",
            "Rule-level outcomes and rationales are absent.",
            "The 2020 website, overview workbook, and yearly raw rows disagree.",
            "OpenAlex license metadata is a discovery signal and must be verified at source.",
            (
                "Europe PMC external links are Unpaywall discovery signals without license "
                "metadata; every downloaded artifact still requires article-identity verification."
            ),
            "Accepted and submitted manuscripts can differ from the evaluated Version of Record.",
        ],
    }
    write_json(layout.reports / "quality_control.json", qc)
    print(
        "Built corpus: "
        f"{len(records):,} papers, {overall['figures_total']:,} figures, "
        f"{len(dois):,} DOI matches"
    )
    return qc
