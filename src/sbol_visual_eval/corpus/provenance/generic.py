"""Identity and provenance checks for generic per-paper PDF manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..layout import Layout
from ..util.text import normalize_doi
from .biorxiv import (
    BIORXIV_PREPRINT_DIRECT_TRANSPORT,
    BIORXIV_PREPRINT_IMPORTED_LIMITATION,
    biorxiv_preprint_manifest_identity_errors,
    is_biorxiv_preprint_manifest,
)
from .manifests import normalized_manifest_doi
from .pmc_web import (
    PMC_WEB_TRANSPORT_LIMITATION,
    PMC_WEB_VARIANT_DIRECTORY,
    pmc_web_generic_manifest_identity_errors,
)


def generic_pdf_identity_record(
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
) -> dict[str, Any]:
    if not is_biorxiv_preprint_manifest(manifest, paper_dir, pdf_path):
        return record
    preprint_doi = manifest.get("preprint_doi")
    preprint_title = manifest.get("preprint_title")
    if not isinstance(preprint_doi, str) or not isinstance(preprint_title, str):
        return record
    return {
        **record,
        "record_id": f"preprint:{normalize_doi(preprint_doi)}",
        "doi": normalize_doi(preprint_doi),
        "title_source": preprint_title,
        "title_crossref": preprint_title,
        "pages": None,
    }


def generic_manifest_identity_errors(
    layout: Layout,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
) -> list[str]:
    errors: list[str] = []
    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        errors.append("generic manifest has an invalid schema_version")
    if manifest.get("record_id") != record["record_id"]:
        errors.append("generic manifest record_id mismatch")
    if normalized_manifest_doi(manifest.get("doi")) != normalize_doi(record.get("doi")):
        errors.append("generic manifest DOI mismatch")
    if manifest.get("year") != record["year"]:
        errors.append("generic manifest year mismatch")
    expected_title = record.get("title_crossref") or record.get("title_source")
    if manifest.get("title") != expected_title:
        errors.append("generic manifest title mismatch")
    if manifest.get("artifact_type") != "article_pdf":
        errors.append("generic manifest artifact_type is not article_pdf")
    if not isinstance(manifest.get("source_url"), str) or not manifest.get("source_url").strip():
        errors.append("generic manifest has no source_url")

    local_path = manifest.get("local_path")
    if not isinstance(local_path, str) or not local_path:
        errors.append("generic manifest has no local_path")
    else:
        declared_path = Path(local_path)
        if not declared_path.is_absolute():
            declared_path = layout.root / declared_path
        try:
            resolved_path = declared_path.resolve()
            resolved_path.relative_to(paper_dir.resolve())
        except ValueError:
            errors.append("generic manifest local_path escapes its paper directory")
        else:
            if resolved_path != pdf_path.resolve():
                errors.append("generic manifest local_path identifies the wrong PDF")

    source_version = manifest.get("source_version")
    expected_assertion = (
        "upstream_metadata"
        if isinstance(source_version, str) and source_version.strip()
        else "unknown"
    )
    if manifest.get("version_assertion_method") != expected_assertion:
        errors.append("generic manifest version assertion is inconsistent")
    if manifest.get("historical_evaluated_edition_relation") != "not_established":
        errors.append("generic manifest historical-edition relation is unrecognized")
    if pdf_path.is_file() and manifest.get("bytes") != pdf_path.stat().st_size:
        errors.append("generic manifest byte count mismatch")
    biorxiv_manifest = is_biorxiv_preprint_manifest(manifest, paper_dir, pdf_path)
    if biorxiv_manifest:
        errors.extend(
            biorxiv_preprint_manifest_identity_errors(
                layout,
                manifest,
                record,
                paper_dir,
                pdf_path,
            )
        )
    else:
        errors.extend(
            pmc_web_generic_manifest_identity_errors(
                layout,
                manifest,
                record,
                paper_dir,
                pdf_path,
            )
        )
    return errors


def generic_manifest_provenance(
    layout: Layout,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
) -> tuple[str, list[str]]:
    errors = generic_manifest_identity_errors(
        layout,
        manifest,
        record,
        paper_dir,
        pdf_path,
    )
    if errors:
        return "invalid", errors
    if is_biorxiv_preprint_manifest(manifest, paper_dir, pdf_path) and (
        manifest.get("transport_provenance_verification_status")
        != BIORXIV_PREPRINT_DIRECT_TRANSPORT
    ):
        return "unverified", [BIORXIV_PREPRINT_IMPORTED_LIMITATION]
    expected_variant_dir = paper_dir / "versions" / PMC_WEB_VARIANT_DIRECTORY
    if pdf_path.parent.resolve() == expected_variant_dir.resolve():
        return "unverified", [PMC_WEB_TRANSPORT_LIMITATION]
    return "verified", []
