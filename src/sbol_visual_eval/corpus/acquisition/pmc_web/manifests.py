"""Manifest vocabulary and construction for bridge-acquired author manuscripts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...layout import Layout
from ...provenance.pmc_web import (
    PMC_WEB_SOURCE_NAME,
    PMC_WEB_TRANSPORT_LIMITATION,
    PMC_WEB_TRANSPORT_STATUS,
)
from ...util.storage import atomic_write_bytes, sha256_file, utc_now

PMC_AUTHOR_MANUSCRIPT_VERSION = "author_manuscript"
GENERIC_MANIFEST_SCHEMA_VERSION = 1
VERSION_ASSERTION_METHOD = "upstream_metadata"
HISTORICAL_EDITION_RELATION = "not_established"


def _store_evidence_snapshot(directory: Path, stem: str, suffix: str, payload: bytes) -> Path:
    digest = hashlib.sha256(payload).hexdigest()
    destination = directory / f"{stem}__{digest[:12]}{suffix}"
    if destination.is_symlink():
        raise ValueError(f"hash-named evidence snapshot may not be a symlink: {destination.name}")
    if destination.exists():
        if destination.read_bytes() != payload:
            raise ValueError(f"hash-named evidence snapshot collision at {destination.name}")
    else:
        atomic_write_bytes(destination, payload)
    return destination


def build_pdf_manifest(
    layout: Layout,
    record: dict[str, Any],
    *,
    variant_dir: Path,
    normalized_doi: str,
    pmcid: str,
    source_url: str,
    resolved_url: str,
    article_url: str,
    version_assertion: dict[str, Any],
    source_page_payload: bytes | None,
    article_xml_payload: bytes | None,
    pmc_manifest: dict[str, Any] | None,
    pmc_manifest_bytes: bytes | None,
    pmc_package_metadata_bytes: bytes | None,
    pmc_article_xml_original_path: Path | None,
    explicit_license: str | None,
    original_pmc_manifest_path: Path,
    destination: Path,
    digest: str,
    preserved_invalid_metadata: str | None,
    verification: dict[str, Any],
) -> dict[str, Any]:
    """Persist evidence snapshots and assemble the artifact's generic manifest."""

    source_page_snapshot_path: Path | None = None
    source_page_snapshot_sha256: str | None = None
    if source_page_payload is not None:
        source_page_snapshot_path = _store_evidence_snapshot(
            variant_dir,
            "source_page",
            ".html",
            source_page_payload,
        )
        source_page_snapshot_sha256 = sha256_file(source_page_snapshot_path)
    pmc_manifest_snapshot_path: Path | None = None
    pmc_manifest_sha256: str | None = None
    pmc_package_metadata_snapshot_path: Path | None = None
    pmc_package_metadata_sha256: str | None = None
    pmc_article_xml_snapshot_path: Path | None = None
    pmc_article_xml_snapshot_sha256: str | None = None
    if pmc_manifest is not None:
        pmc_manifest_snapshot_path = _store_evidence_snapshot(
            variant_dir,
            "pmc_manifest",
            ".json",
            pmc_manifest_bytes,
        )
        pmc_manifest_sha256 = sha256_file(pmc_manifest_snapshot_path)
        pmc_package_metadata_snapshot_path = _store_evidence_snapshot(
            variant_dir,
            "pmc_package_metadata",
            ".json",
            pmc_package_metadata_bytes,
        )
        pmc_package_metadata_sha256 = sha256_file(pmc_package_metadata_snapshot_path)
        if article_xml_payload is None:
            raise TypeError("manifest-backed queue item has no article-XML evidence")
        pmc_article_xml_snapshot_path = _store_evidence_snapshot(
            variant_dir,
            "pmc_article_xml",
            ".xml",
            article_xml_payload,
        )
        pmc_article_xml_snapshot_sha256 = sha256_file(pmc_article_xml_snapshot_path)
    return {
        "schema_version": GENERIC_MANIFEST_SCHEMA_VERSION,
        "status": "downloaded",
        "retrieved_at": utc_now(),
        "record_id": record["record_id"],
        "doi": normalized_doi,
        "year": record["year"],
        "title": record.get("title_crossref") or record["title_source"],
        "artifact_type": "article_pdf",
        "local_path": layout.display_path(destination),
        "source_url": source_url,
        "discovered_from_url": article_url,
        "resolved_url": resolved_url,
        "source_landing_page_url": article_url,
        "source_name": PMC_WEB_SOURCE_NAME,
        "source_type": "repository",
        "source_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
        "source_license": explicit_license,
        "source_access": "public PMC browser session with same-origin cookies",
        "source_metadata_source": (
            "PMC package metadata and verified article XML"
            if pmc_manifest
            else "PMC public article HTML"
        ),
        "source_intended_application": "full_text",
        "license_metadata_source": ("PMC AWS package metadata" if explicit_license else None),
        "upstream_license_code": (pmc_manifest.get("license_code") if pmc_manifest else None),
        "pmcid": pmcid,
        "versioned_pmcid": (pmc_manifest.get("versioned_pmcid") if pmc_manifest else None),
        "pmc_manifest_path": (
            layout.display_path(pmc_manifest_snapshot_path) if pmc_manifest_snapshot_path else None
        ),
        "pmc_manifest_original_path": (
            layout.display_path(original_pmc_manifest_path) if pmc_manifest else None
        ),
        "pmc_manifest_sha256": pmc_manifest_sha256,
        "pmc_package_metadata_url": (
            pmc_manifest.get("package_metadata_url") if pmc_manifest else None
        ),
        "pmc_package_metadata_snapshot_path": (
            layout.display_path(pmc_package_metadata_snapshot_path)
            if pmc_package_metadata_snapshot_path
            else None
        ),
        "pmc_package_metadata_snapshot_sha256": pmc_package_metadata_sha256,
        "pmc_package_metadata_snapshot_bytes": (
            pmc_package_metadata_snapshot_path.stat().st_size
            if pmc_package_metadata_snapshot_path
            else None
        ),
        "pmc_article_xml_original_path": (
            layout.display_path(pmc_article_xml_original_path)
            if pmc_article_xml_original_path
            else None
        ),
        "pmc_article_xml_snapshot_path": (
            layout.display_path(pmc_article_xml_snapshot_path)
            if pmc_article_xml_snapshot_path
            else None
        ),
        "pmc_article_xml_snapshot_sha256": pmc_article_xml_snapshot_sha256,
        "pmc_article_xml_snapshot_bytes": (
            pmc_article_xml_snapshot_path.stat().st_size if pmc_article_xml_snapshot_path else None
        ),
        "source_landing_page_snapshot_path": (
            layout.display_path(source_page_snapshot_path) if source_page_snapshot_path else None
        ),
        "source_landing_page_snapshot_sha256": source_page_snapshot_sha256,
        "source_landing_page_snapshot_bytes": (
            source_page_snapshot_path.stat().st_size if source_page_snapshot_path else None
        ),
        "version_assertion_method": VERSION_ASSERTION_METHOD,
        "source_version_assertion": "author_manuscript",
        "source_version_assertion_method": version_assertion["method"],
        "source_version_assertion_evidence": version_assertion["evidence"],
        "historical_evaluated_edition_relation": HISTORICAL_EDITION_RELATION,
        "historical_evaluated_edition_equivalence_asserted": False,
        "transport_provenance_verification_status": PMC_WEB_TRANSPORT_STATUS,
        "transport_provenance_limitation": PMC_WEB_TRANSPORT_LIMITATION,
        "preserved_invalid_metadata": (
            layout.display_path(Path(preserved_invalid_metadata))
            if preserved_invalid_metadata
            else None
        ),
        "bytes": destination.stat().st_size,
        "sha256": digest,
        **verification,
    }
