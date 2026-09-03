"""PMC acquisition-manifest vocabulary and provenance verification."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..util.storage import canonical_json_sha256, sha256_file
from ..util.text import normalize_doi
from .manifests import is_hex_digest, normalized_manifest_doi

PMC_MANIFEST_STATUSES = frozenset(
    {
        "already_present",
        "author_manuscript_text_already_present",
        "author_manuscript_text_download_failed",
        "author_manuscript_text_downloaded",
        "download_failed",
        "downloaded",
        "no_pdf_or_media",
        "partial_download",
    }
)
PMC_ARTIFACT_ROLES = frozenset({"article_pdf", "article_text", "article_xml", "media"})
PMC_ARTIFACT_STATUSES = frozenset({"already_present", "downloaded", "failed"})
PMC_SOURCE_VERSIONS = frozenset({"author_manuscript", "published_version"})
PMC_PACKAGE_HASH_SCOPE = "raw_http_response_bytes"
PMC_PACKAGE_CANONICALIZATION = "json_utf8_sorted_keys_compact_v1"


def pmc_manifest_provenance(
    layout: Layout,
    record: dict[str, Any],
    paper_dir: Path,
    manifest: dict[str, Any],
    *,
    package_metadata_override_path: Path | None = None,
) -> tuple[str, list[str], Path | None]:
    status = "verified"
    errors: list[str] = []
    package_path: Path | None = None

    def invalidate(message: str) -> None:
        nonlocal status
        status = "invalid"
        errors.append(message)

    def mark_unverified(message: str) -> None:
        nonlocal status
        if status == "verified":
            status = "unverified"
        errors.append(message)

    schema_version = manifest.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version not in {1, 2}
    ):
        invalidate("PMC manifest has an unsupported schema_version")
    if manifest.get("record_id") != record["record_id"]:
        invalidate("PMC manifest record_id does not match the processed paper")
    if normalized_manifest_doi(manifest.get("doi")) != normalize_doi(record.get("doi")):
        invalidate("PMC manifest DOI does not match the processed paper")
    if manifest.get("status") not in PMC_MANIFEST_STATUSES:
        invalidate("PMC manifest has an unrecognized status")
    source_version = manifest.get("source_version")
    if source_version not in PMC_SOURCE_VERSIONS:
        invalidate("PMC manifest has an unrecognized source_version")

    pmcid = manifest.get("pmcid")
    if not isinstance(pmcid, str) or not re.fullmatch(r"PMC\d+", pmcid, flags=re.IGNORECASE):
        invalidate("PMC manifest has an invalid PMCID")
    expected_pmcid = record.get("europepmc_pmcid")
    if (
        isinstance(expected_pmcid, str)
        and isinstance(pmcid, str)
        and expected_pmcid.casefold() != pmcid.casefold()
    ):
        invalidate("PMC manifest PMCID differs from the processed paper")

    versioned_pmcid = manifest.get("versioned_pmcid")
    version_match = (
        re.fullmatch(r"(PMC\d+)\.(\d+)", versioned_pmcid, flags=re.IGNORECASE)
        if isinstance(versioned_pmcid, str)
        else None
    )
    pmc_version = manifest.get("pmc_version")
    if version_match is None:
        invalidate("PMC manifest has an invalid versioned PMCID")
    else:
        if isinstance(pmcid, str) and version_match.group(1).casefold() != pmcid.casefold():
            invalidate("PMC manifest versioned PMCID disagrees with its PMCID")
        if (
            not isinstance(pmc_version, int)
            or isinstance(pmc_version, bool)
            or pmc_version < 1
            or pmc_version != int(version_match.group(2))
        ):
            invalidate("PMC manifest version number is inconsistent")

    package_metadata = manifest.get("package_metadata")
    if not isinstance(package_metadata, dict):
        invalidate("PMC package_metadata is not an object")
        package_metadata = None
    else:
        if normalized_manifest_doi(package_metadata.get("doi")) != normalize_doi(record.get("doi")):
            invalidate("PMC package metadata DOI does not match the processed paper")
        metadata_pmcid = package_metadata.get("pmcid")
        if (
            not isinstance(metadata_pmcid, str)
            or not isinstance(pmcid, str)
            or metadata_pmcid.casefold() != pmcid.casefold()
        ):
            invalidate("PMC package metadata PMCID is inconsistent")
        if package_metadata.get("version") != pmc_version:
            invalidate("PMC package metadata version is inconsistent")
        is_manuscript = package_metadata.get("is_manuscript")
        if not isinstance(is_manuscript, bool):
            invalidate("PMC package metadata has an invalid is_manuscript value")
        else:
            expected_version = "author_manuscript" if is_manuscript else "published_version"
            if source_version != expected_version:
                invalidate("PMC source_version disagrees with package metadata")
            if manifest.get("is_manuscript") != is_manuscript:
                invalidate("PMC top-level is_manuscript disagrees with package metadata")
        if manifest.get("is_pmc_openaccess") != package_metadata.get("is_pmc_openaccess"):
            invalidate("PMC top-level open-access status disagrees with package metadata")

    package_sha256 = manifest.get("package_metadata_sha256")
    if not is_hex_digest(package_sha256, 64):
        invalidate("PMC manifest has no valid package metadata SHA-256")

    if schema_version == 1:
        mark_unverified(
            "schema-v1 package metadata hashes an unstored raw response; rerun PMC acquisition"
        )
    elif schema_version == 2:
        if manifest.get("package_metadata_sha256_scope") != PMC_PACKAGE_HASH_SCOPE:
            invalidate("PMC package metadata SHA-256 scope is unrecognized")
        if manifest.get("package_metadata_canonicalization") != PMC_PACKAGE_CANONICALIZATION:
            invalidate("PMC package metadata canonicalization is unrecognized")
        canonical_sha256 = manifest.get("package_metadata_canonical_sha256")
        if not is_hex_digest(canonical_sha256, 64):
            invalidate("PMC manifest has no valid canonical package metadata SHA-256")
        elif package_metadata is not None:
            try:
                expected_canonical_sha256 = canonical_json_sha256(package_metadata)
            except (TypeError, ValueError) as error:
                invalidate(f"PMC package metadata is not canonicalizable: {error}")
            else:
                if canonical_sha256.casefold() != expected_canonical_sha256:
                    invalidate("PMC canonical package metadata hash does not match")

        package_local_path = manifest.get("package_metadata_local_path")
        if not isinstance(package_local_path, str) or not package_local_path:
            invalidate("PMC manifest has no package metadata local_path")
        else:
            declared_path = Path(package_local_path)
            if not declared_path.is_absolute():
                declared_path = layout.root / declared_path
            try:
                package_path = declared_path.resolve()
                package_path.relative_to(paper_dir.resolve())
            except ValueError:
                package_path = None
                invalidate("PMC package metadata path escapes its paper directory")
            if package_metadata_override_path is not None:
                try:
                    package_path = package_metadata_override_path.resolve()
                    package_path.relative_to(paper_dir.resolve())
                except ValueError:
                    package_path = None
                    invalidate("PMC package metadata override path escapes its paper directory")
            if package_path is not None:
                if not package_path.is_file():
                    invalidate("PMC raw package metadata file is missing")
                else:
                    if (
                        is_hex_digest(package_sha256, 64)
                        and sha256_file(package_path) != package_sha256.casefold()
                    ):
                        invalidate("PMC raw package metadata hash does not match")
                    try:
                        stored_package = json.loads(package_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
                        invalidate(
                            "PMC raw package metadata cannot be read: "
                            f"{type(error).__name__}: {error}"
                        )
                    else:
                        if package_metadata is not None and stored_package != package_metadata:
                            invalidate("PMC raw and embedded package metadata differ")
    return status, errors, package_path
