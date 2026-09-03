from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..provenance import pdf
from ..provenance.generic import generic_pdf_identity_record
from ..util.storage import read_jsonl, sha256_file, write_json
from ..util.text import normalize_doi

GENERIC_MANIFEST_SCHEMA_VERSION = 1
UPSTREAM_VERSION_ASSERTION_METHOD = "upstream_metadata"
UNKNOWN_VERSION_ASSERTION_METHOD = "unknown"
HISTORICAL_EDITION_RELATION = "not_established"
VERIFICATION_FIELDS = (
    "artifact_type",
    "page_count",
    "identity_check",
    "title_similarity",
)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"cannot read generic manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise TypeError("generic manifest is not an object")
    return manifest


def _manifest_pairs(layout: Layout) -> list[tuple[Path, Path, Path]]:
    pairs: list[tuple[Path, Path, Path]] = []
    for manifest_path in sorted(layout.papers.glob("*/*/metadata.json")):
        paper_dir = manifest_path.parent
        pairs.append((paper_dir, manifest_path, paper_dir / "paper.pdf"))
    for manifest_path in sorted(layout.papers.glob("*/*/versions/*/metadata__*.json")):
        paper_dir = manifest_path.parents[2]
        pdf_path = manifest_path.with_name(
            manifest_path.name.replace("metadata__", "paper__", 1)
        ).with_suffix(".pdf")
        pairs.append((paper_dir, manifest_path, pdf_path))
    return pairs


def _records_by_directory(layout: Layout) -> dict[Path, dict[str, Any]]:
    records_path = layout.processed / "papers.jsonl"
    records = read_jsonl(records_path)
    records_by_directory: dict[Path, dict[str, Any]] = {}
    for record in records:
        paper_dir = layout.paper_directory(record).resolve()
        if paper_dir in records_by_directory:
            raise ValueError(
                "multiple processed paper records map to "
                f"{layout.display_path(layout.paper_directory(record))}"
            )
        records_by_directory[paper_dir] = record
    return records_by_directory


def _generic_manifest_payload(
    layout: Layout,
    manifest: dict[str, Any],
    pdf_path: Path,
    record: dict[str, Any],
) -> dict[str, Any]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"generic article PDF is missing: {layout.display_path(pdf_path)}")

    verification = pdf.verify_pdf(
        pdf_path,
        generic_pdf_identity_record(
            manifest,
            record,
            layout.paper_directory(record),
            pdf_path,
        ),
    )
    missing_fields = [field for field in VERIFICATION_FIELDS if field not in verification]
    if missing_fields:
        raise ValueError(
            "PDF verification did not return required fields: " + ", ".join(missing_fields)
        )
    if verification["artifact_type"] != "article_pdf":
        raise ValueError("PDF verification did not identify an article PDF")

    schema_version = manifest.get("schema_version", GENERIC_MANIFEST_SCHEMA_VERSION)
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        raise ValueError("generic manifest has an invalid schema_version")

    title = record.get("title_crossref") or record.get("title_source")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("processed paper record has no title")

    source_version = manifest.get("source_version")
    version_assertion_method = (
        UPSTREAM_VERSION_ASSERTION_METHOD
        if isinstance(source_version, str) and source_version.strip()
        else UNKNOWN_VERSION_ASSERTION_METHOD
    )
    upgraded = dict(manifest)
    upgraded.update(
        {
            "schema_version": max(schema_version, GENERIC_MANIFEST_SCHEMA_VERSION),
            "record_id": record["record_id"],
            "doi": normalize_doi(record.get("doi")),
            "year": record["year"],
            "title": title,
            "local_path": layout.display_path(pdf_path),
            "bytes": pdf_path.stat().st_size,
            "sha256": sha256_file(pdf_path),
            "version_assertion_method": version_assertion_method,
            "historical_evaluated_edition_relation": HISTORICAL_EDITION_RELATION,
            **{field: verification[field] for field in VERIFICATION_FIELDS},
        }
    )
    return upgraded


def backfill_generic_manifests(layout: Layout) -> dict[str, Any]:
    """Verify and enrich current and archived generic article manifests.

    A failure is isolated to its manifest so that one malformed historical artifact does
    not prevent valid pairs from being upgraded. Callers should treat a nonzero ``failed``
    count as an unsuccessful corpus migration.
    """

    records_by_directory = _records_by_directory(layout)
    results: list[dict[str, Any]] = []
    updated = 0
    unchanged = 0
    failed = 0

    for paper_dir, manifest_path, pdf_path in _manifest_pairs(layout):
        record = records_by_directory.get(paper_dir.resolve())
        base_result = {
            "manifest_path": layout.display_path(manifest_path),
            "local_path": layout.display_path(pdf_path),
        }
        if record is None:
            failed += 1
            results.append(
                {
                    **base_result,
                    "status": "failed",
                    "error": "generic manifest is not associated with a processed paper record",
                }
            )
            continue

        base_result.update(
            {
                "record_id": record.get("record_id"),
                "doi": normalize_doi(record.get("doi")),
            }
        )
        try:
            manifest = _load_manifest(manifest_path)
            upgraded = _generic_manifest_payload(layout, manifest, pdf_path, record)
            if upgraded == manifest:
                unchanged += 1
                status = "unchanged"
            else:
                write_json(manifest_path, upgraded)
                updated += 1
                status = "updated"
            results.append({**base_result, "status": status})
        except (KeyError, OSError, TypeError, ValueError) as error:
            failed += 1
            results.append(
                {
                    **base_result,
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    return {
        "manifests_considered": len(results),
        "updated": updated,
        "unchanged": unchanged,
        "failed": failed,
        "results": results,
    }
