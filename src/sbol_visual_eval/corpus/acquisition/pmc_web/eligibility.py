"""Which processed records qualify for the public-page fallback, and why."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...layout import Layout
from ...provenance import pdf
from ...provenance.pmc import pmc_manifest_provenance
from ...util.storage import sha256_file
from .archive import _safe_paper_directory
from .manifests import PMC_AUTHOR_MANUSCRIPT_VERSION
from .urls import _normalized_doi, _normalized_pmcid


def _load_pmc_manifest(layout: Layout, record: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = _safe_paper_directory(layout, record) / "pmc.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _verified_article_pdf(layout: Layout, record: dict[str, Any], manifest: dict[str, Any]) -> bool:
    paper_dir = _safe_paper_directory(layout, record).resolve()
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict) or artifact.get("role") != "article_pdf":
            continue
        if artifact.get("status") not in {"downloaded", "already_present"}:
            continue
        local_path = artifact.get("local_path")
        expected_hash = artifact.get("sha256")
        if not isinstance(local_path, str) or not isinstance(expected_hash, str):
            continue
        path = Path(local_path)
        if not path.is_absolute():
            path = layout.root / path
        try:
            path.resolve().relative_to(paper_dir)
        except ValueError:
            continue
        if not path.is_file() or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
            continue
        if sha256_file(path) != expected_hash.casefold():
            continue
        try:
            pdf.verify_pdf(path, record)
        except Exception:  # noqa: BLE001, S112 - invalid artifacts do not block fallback
            continue
        return True
    return False


def _eligible_record(layout: Layout, record: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
    if pmcid is None:
        return None, "no_europepmc_pmcid"
    manifest_path = _safe_paper_directory(layout, record) / "pmc.json"
    if not manifest_path.exists():
        return None, "eligible_requires_page_author_manuscript_evidence"
    manifest = _load_pmc_manifest(layout, record)
    if manifest is None:
        return None, "invalid_pmc_manifest"
    if manifest.get("record_id") != record.get("record_id"):
        return None, "pmc_manifest_record_id_mismatch"
    if _normalized_doi(manifest.get("doi")) != _normalized_doi(record.get("doi")):
        return None, "pmc_manifest_doi_mismatch"
    if _normalized_pmcid(manifest.get("pmcid")) != pmcid:
        return None, "pmc_manifest_pmcid_mismatch"
    provenance_status, _, _ = pmc_manifest_provenance(
        layout,
        record,
        manifest_path.parent,
        manifest,
    )
    if provenance_status != "verified":
        return None, f"{provenance_status}_pmc_manifest"
    if not (
        manifest.get("is_manuscript") is True
        and manifest.get("source_version") == PMC_AUTHOR_MANUSCRIPT_VERSION
    ):
        return None, "not_an_author_manuscript"
    if _verified_article_pdf(layout, record, manifest):
        return None, "verified_pmc_article_pdf_present"
    return manifest, "eligible"


def _explicit_license(manifest: dict[str, Any]) -> str | None:
    license_code = manifest.get("license_code")
    if not isinstance(license_code, str):
        return None
    normalized = re.sub(r"\s+", " ", license_code.strip()).upper()
    if normalized in {
        "CC0",
        "CC BY",
        "CC BY-SA",
        "CC BY-ND",
        "CC BY-NC",
        "CC BY-NC-SA",
        "CC BY-NC-ND",
        "PUBLIC DOMAIN",
    }:
        return license_code.strip()
    return None
