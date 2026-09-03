"""Safe paths, evidence snapshots, and validation of archived PDF/manifest pairs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...layout import Layout
from ...provenance import pdf
from ...provenance.generic import generic_manifest_identity_errors
from ...provenance.pmc_web import PMC_WEB_VARIANT_DIRECTORY
from ...util.storage import sha256_file
from .manifests import (
    HISTORICAL_EDITION_RELATION,
    PMC_AUTHOR_MANUSCRIPT_VERSION,
    VERSION_ASSERTION_METHOD,
)
from .urls import _normalized_doi, _normalized_pmcid


def _safe_paper_directory(layout: Layout, record: dict[str, Any]) -> Path:
    year = record.get("year")
    if not isinstance(year, int) or isinstance(year, bool) or not 1900 <= year <= 2200:
        raise ValueError("processed paper record has an invalid year")
    path = layout.paper_directory(record)
    try:
        relative = path.resolve().relative_to(layout.papers.resolve())
    except ValueError as error:
        raise ValueError("processed paper directory escapes data/papers") from error
    if len(relative.parts) != 2:
        raise ValueError("processed paper directory does not have year/identifier shape")
    return path


def _safe_variant_directory(
    layout: Layout,
    record: dict[str, Any],
    *,
    create: bool,
) -> Path:
    paper_directory = _safe_paper_directory(layout, record)
    versions_directory = paper_directory / "versions"
    variant_directory = versions_directory / PMC_WEB_VARIANT_DIRECTORY
    for directory in (versions_directory, variant_directory):
        if directory.is_symlink():
            raise ValueError("PMC web variant path may not contain symlinked directories")
        if directory.exists() and not directory.is_dir():
            raise ValueError("PMC web variant path contains a non-directory component")
    if create:
        variant_directory.mkdir(parents=True, exist_ok=True)
    try:
        variant_directory.resolve().relative_to(paper_directory.resolve())
    except ValueError as error:
        raise ValueError("PMC web variant directory escapes its paper directory") from error
    return variant_directory


def _archived_paths(layout: Layout, record: dict[str, Any], digest: str) -> tuple[Path, Path]:
    directory = _safe_variant_directory(layout, record, create=False)
    suffix = digest[:12]
    return directory / f"paper__{suffix}.pdf", directory / f"metadata__{suffix}.json"


def _validate_archived_pair(
    layout: Layout,
    record: dict[str, Any],
    pdf_path: Path,
    metadata_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if pdf_path.is_symlink() or metadata_path.is_symlink():
        raise ValueError("archived PDF/manifest pair may not contain symlinks")
    if not pdf_path.is_file() or not metadata_path.is_file():
        raise ValueError("archived PDF/manifest pair is incomplete")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"archived manifest is unreadable: {error}") from error
    if not isinstance(metadata, dict):
        raise TypeError("archived manifest is not an object")
    schema_version = metadata.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        raise ValueError("archived manifest has an invalid schema_version")
    if metadata.get("record_id") != record.get("record_id"):
        raise ValueError("archived manifest record_id mismatch")
    if _normalized_doi(metadata.get("doi")) != _normalized_doi(record.get("doi")):
        raise ValueError("archived manifest DOI mismatch")
    if metadata.get("year") != record.get("year"):
        raise ValueError("archived manifest year mismatch")
    title = record.get("title_crossref") or record.get("title_source")
    if metadata.get("title") != title:
        raise ValueError("archived manifest title mismatch")
    if metadata.get("source_version") != PMC_AUTHOR_MANUSCRIPT_VERSION:
        raise ValueError("archived manifest is not an author manuscript")
    pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
    if pmcid is None or _normalized_pmcid(metadata.get("pmcid")) != pmcid:
        raise ValueError("archived manifest PMCID mismatch")
    if metadata.get("artifact_type") != "article_pdf":
        raise ValueError("archived manifest is not an article PDF")
    if metadata.get("version_assertion_method") != VERSION_ASSERTION_METHOD:
        raise ValueError("archived manifest has an invalid version assertion")
    if metadata.get("historical_evaluated_edition_relation") != HISTORICAL_EDITION_RELATION:
        raise ValueError("archived manifest has an invalid historical-edition relation")
    if metadata.get("historical_evaluated_edition_equivalence_asserted") is not False:
        raise ValueError("archived manifest does not explicitly withhold edition equivalence")
    local_path = metadata.get("local_path")
    if not isinstance(local_path, str):
        raise TypeError("archived manifest has no local_path")
    declared = Path(local_path)
    if not declared.is_absolute():
        declared = layout.root / declared
    if declared.resolve() != pdf_path.resolve():
        raise ValueError("archived manifest identifies the wrong PDF")
    digest = sha256_file(pdf_path)
    if metadata.get("sha256") != digest or metadata.get("bytes") != pdf_path.stat().st_size:
        raise ValueError("archived PDF size or checksum mismatch")
    if not pdf_path.stem.endswith(digest[:12]):
        raise ValueError("archived PDF filename does not match its checksum")
    identity_errors = generic_manifest_identity_errors(
        layout,
        metadata,
        record,
        _safe_paper_directory(layout, record),
        pdf_path,
    )
    if identity_errors:
        raise ValueError("; ".join(identity_errors))
    return metadata, pdf.verify_pdf(pdf_path, record)


def _preserve_invalid_metadata(metadata_path: Path) -> str | None:
    if not metadata_path.exists():
        return None
    rejected = metadata_path.parent / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    candidate = rejected / metadata_path.name
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = rejected / f"{metadata_path.stem}__{counter}{metadata_path.suffix}"
    metadata_path.replace(candidate)
    return str(candidate)
