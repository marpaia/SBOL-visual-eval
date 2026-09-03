"""Publisher PDF/manifest pairs: validation, archiving, and replacement audit."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...layout import Layout
from ...provenance import pdf
from ...util.storage import sha256_file, utc_now, write_json
from .urls import _validated_publisher_url

PUBLISHER_SOURCE = "ACS Publications via CU Boulder EZproxy"
LEGACY_PUBLISHER_SOURCE = "ACS Publications via CU Boulder EZproxy authenticated browser"


PUBLISHER_MANIFEST_SCHEMA_VERSION = 2
VERSION_ASSERTION_METHOD = "authenticated_acs_version_of_record_pdf_endpoint"
RETRIEVED_VERSION_SCOPE = "current_version_of_record_at_retrieval"
HISTORICAL_EDITION_RELATION = "not_established"
MAX_PDF_BYTES = 120 * 1024 * 1024


def _publisher_paths(layout: Layout, record: dict[str, Any]) -> tuple[Path, Path]:
    paper_dir = layout.paper_directory(record)
    return paper_dir / "publisher" / "paper.pdf", paper_dir / "publisher.json"


def _with_current_vor_provenance(manifest: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "retrieved_version_scope": RETRIEVED_VERSION_SCOPE,
        "version_assertion_method": VERSION_ASSERTION_METHOD,
        "historical_evaluated_edition_relation": HISTORICAL_EDITION_RELATION,
    }
    upgraded = dict(manifest)
    source_name = upgraded.get("source_name")
    if source_name in {None, LEGACY_PUBLISHER_SOURCE}:
        upgraded["source_name"] = PUBLISHER_SOURCE
    elif source_name != PUBLISHER_SOURCE:
        raise ValueError("publisher manifest has conflicting source_name")
    for field, value in expected.items():
        existing = upgraded.get(field)
        if existing is not None and existing != value:
            raise ValueError(f"publisher manifest has conflicting {field}")
        upgraded[field] = value
    schema_version = upgraded.get("schema_version", 1)
    if not isinstance(schema_version, int) or schema_version < 1:
        raise ValueError("publisher manifest has an invalid schema_version")
    upgraded["schema_version"] = max(schema_version, PUBLISHER_MANIFEST_SCHEMA_VERSION)
    return upgraded


def _validated_publisher_pair(
    layout: Layout,
    record: dict[str, Any],
    destination: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not destination.is_file() or not manifest_path.is_file():
        raise ValueError("publisher PDF/manifest pair is incomplete")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"publisher manifest is unreadable: {error}") from error
    if not isinstance(manifest, dict):
        raise TypeError("publisher manifest is not an object")
    if manifest.get("record_id") != record["record_id"]:
        raise ValueError("publisher manifest record_id mismatch")
    manifest_doi = manifest.get("doi")
    if (
        not isinstance(manifest_doi, str)
        or manifest_doi.casefold() != str(record.get("doi") or "").casefold()
    ):
        raise ValueError("publisher manifest DOI mismatch")
    if manifest.get("artifact_type") != "article_pdf":
        raise ValueError("publisher manifest artifact_type is not article_pdf")
    if manifest.get("source_version") != "publishedVersion":
        raise ValueError("publisher manifest is not an explicit publishedVersion")
    _validated_publisher_url(manifest.get("source_url"), field_name="source_url")
    _validated_publisher_url(
        manifest.get("resolved_url"), field_name="resolved_url", allow_none=True
    )
    _with_current_vor_provenance(manifest)

    local_path = manifest.get("local_path")
    if not isinstance(local_path, str) or not local_path:
        raise ValueError("publisher manifest has no local_path")
    declared_path = Path(local_path)
    if not declared_path.is_absolute():
        declared_path = layout.root / declared_path
    paper_dir = layout.paper_directory(record).resolve()
    try:
        resolved_path = declared_path.resolve()
        resolved_path.relative_to(paper_dir)
    except ValueError as error:
        raise ValueError("publisher manifest local_path escapes its paper directory") from error
    if resolved_path != destination.resolve():
        raise ValueError("publisher manifest local_path does not identify publisher/paper.pdf")

    expected_hash = manifest.get("sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ValueError("publisher manifest has no valid SHA-256")
    actual_hash = sha256_file(destination)
    if expected_hash.casefold() != actual_hash:
        raise ValueError("publisher artifact checksum mismatch")
    if manifest.get("bytes") != destination.stat().st_size:
        raise ValueError("publisher artifact byte count mismatch")
    return manifest, pdf.verify_pdf(destination, record)


def _archive_publisher_pair(
    layout: Layout,
    destination: Path,
    manifest_path: Path,
    archive_name: str,
    *,
    replacement_sha256: str | None = None,
) -> dict[str, str] | None:
    archive_dir = destination.parent / archive_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        identity_hash = sha256_file(destination)
    elif manifest_path.exists():
        identity_hash = sha256_file(manifest_path)
    else:
        return None
    suffix = identity_hash[:12]
    counter = 1
    while (archive_dir / f"paper__{suffix}.pdf").exists() or (
        archive_dir / f"publisher__{suffix}.json"
    ).exists():
        counter += 1
        suffix = f"{identity_hash[:12]}_{counter}"
    archived_pdf = archive_dir / f"paper__{suffix}.pdf"
    archived_manifest = archive_dir / f"publisher__{suffix}.json"
    if destination.exists():
        destination.replace(archived_pdf)
    if manifest_path.exists():
        manifest_path.replace(archived_manifest)

    if archive_name == "replaced":
        if replacement_sha256 is None:
            raise ValueError("a replaced publisher artifact needs its replacement hash")
        if not archived_pdf.is_file() or not archived_manifest.is_file():
            raise ValueError("a replaced publisher archive must contain a PDF/manifest pair")
        prior_manifest = json.loads(archived_manifest.read_text(encoding="utf-8"))
        if not isinstance(prior_manifest, dict):
            raise TypeError("archived publisher manifest is not an object")
        prior_manifest = _with_current_vor_provenance(prior_manifest)
        prior_manifest.update(
            {
                "status": "replaced",
                "local_path": str(archived_pdf.relative_to(layout.root)),
                "archived_at": utc_now(),
                "archive_disposition": "replaced",
                "replaced_by_sha256": replacement_sha256,
            }
        )
        write_json(archived_manifest, prior_manifest)
        return {
            "replaces_local_path": str(archived_pdf.relative_to(layout.root)),
            "replaces_sha256": identity_hash,
        }
    return None


def _audit_replaced_archives(
    layout: Layout,
    record: dict[str, Any],
    current_manifest: dict[str, Any] | None,
) -> list[str]:
    destination, _ = _publisher_paths(layout, record)
    archive_dir = destination.parent / "replaced"

    errors: list[str] = []
    pdfs = {
        path.name.removeprefix("paper__").removesuffix(".pdf"): path
        for path in archive_dir.glob("paper__*.pdf")
    }
    manifests = {
        path.name.removeprefix("publisher__").removesuffix(".json"): path
        for path in archive_dir.glob("publisher__*.json")
    }
    for suffix in sorted(pdfs.keys() - manifests.keys()):
        errors.append(f"replaced publisher PDF has no manifest: {pdfs[suffix]}")
    for suffix in sorted(manifests.keys() - pdfs.keys()):
        errors.append(f"replaced publisher manifest has no PDF: {manifests[suffix]}")

    artifacts_by_hash: dict[str, dict[str, Any]] = {}
    if current_manifest is not None:
        artifacts_by_hash[str(current_manifest["sha256"]).casefold()] = current_manifest
    for suffix in sorted(pdfs.keys() & manifests.keys()):
        pdf_path = pdfs[suffix]
        manifest_path = manifests[suffix]
        try:
            archived = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(archived, dict):
                raise TypeError("manifest is not an object")
            actual_hash = sha256_file(pdf_path)
            if str(archived.get("sha256") or "").casefold() != actual_hash:
                raise ValueError("checksum mismatch")
            if not re.fullmatch(rf"{actual_hash[:12]}(?:_\d+)?", suffix):
                raise ValueError("filename does not match checksum")
            expected_local_path = str(pdf_path.relative_to(layout.root))
            if archived.get("local_path") != expected_local_path:
                raise ValueError("local_path does not identify the archived PDF")
            if archived.get("bytes") != pdf_path.stat().st_size:
                raise ValueError("byte count mismatch")
            if actual_hash in artifacts_by_hash:
                raise ValueError("duplicate artifact hash in replacement chain")
            artifacts_by_hash[actual_hash] = archived
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as error:
            errors.append(f"invalid replaced publisher pair {suffix}: {error}")

    for artifact_hash, manifest in artifacts_by_hash.items():
        previous_hash = manifest.get("replaces_sha256")
        if previous_hash is not None:
            previous = artifacts_by_hash.get(str(previous_hash).casefold())
            if previous is None:
                errors.append(
                    f"publisher replacement {artifact_hash[:12]} points to a missing predecessor"
                )
            elif str(previous.get("replaced_by_sha256") or "").casefold() != artifact_hash:
                errors.append(
                    f"publisher replacement {artifact_hash[:12]} lacks a reciprocal predecessor link"
                )
            elif manifest.get("replaces_local_path") != previous.get("local_path"):
                errors.append(
                    f"publisher replacement {artifact_hash[:12]} has the wrong predecessor path"
                )
        successor_hash = manifest.get("replaced_by_sha256")
        if successor_hash is not None:
            successor = artifacts_by_hash.get(str(successor_hash).casefold())
            if successor is None:
                errors.append(
                    f"publisher replacement {artifact_hash[:12]} points to a missing successor"
                )
            elif str(successor.get("replaces_sha256") or "").casefold() != artifact_hash:
                errors.append(
                    f"publisher replacement {artifact_hash[:12]} lacks a reciprocal successor link"
                )
    return errors
