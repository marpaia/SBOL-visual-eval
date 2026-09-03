"""Validation of PMC acquisition manifests and their referenced artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..provenance import pdf
from ..provenance.manifests import (
    confined_manifest_path,
    is_hex_digest,
    normalized_manifest_doi,
    read_artifact_metadata,
)
from ..provenance.pmc import (
    PMC_ARTIFACT_ROLES,
    PMC_ARTIFACT_STATUSES,
    PMC_MANIFEST_STATUSES,
    PMC_PACKAGE_CANONICALIZATION,
    PMC_PACKAGE_HASH_SCOPE,
    PMC_SOURCE_VERSIONS,
)
from ..util.storage import canonical_json_sha256, file_digests, sha256_file
from ..util.text import normalize_doi


def validate_pmc_manifests(
    layout: Layout, records: Sequence[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    referenced_paths: set[Path] = set()
    legacy_package_hashes = 0
    records_by_directory = {layout.paper_directory(record).resolve(): record for record in records}

    for manifest_path in sorted(layout.papers.glob("*/*/pmc.json")):
        paper_dir = manifest_path.parent
        display_manifest = layout.display_path(manifest_path)
        record = records_by_directory.get(paper_dir.resolve())
        if record is None:
            errors.append(
                f"PMC manifest is not associated with a processed record: {display_manifest}"
            )
            continue
        manifest = read_artifact_metadata(layout, manifest_path, errors)
        if manifest is None:
            continue

        schema_version = manifest.get("schema_version")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version not in {1, 2}
        ):
            errors.append(f"PMC manifest has unsupported schema_version: {display_manifest}")
        if manifest.get("record_id") != record["record_id"]:
            errors.append(f"PMC record_id mismatch: {display_manifest}")
        if normalized_manifest_doi(manifest.get("doi")) != normalize_doi(record.get("doi")):
            errors.append(f"PMC DOI mismatch: {display_manifest}")
        if manifest.get("status") not in PMC_MANIFEST_STATUSES:
            errors.append(f"PMC manifest has unrecognized status: {display_manifest}")

        source_version = manifest.get("source_version")
        if source_version not in PMC_SOURCE_VERSIONS:
            errors.append(f"PMC manifest has unrecognized source_version: {display_manifest}")
        pmcid = manifest.get("pmcid")
        if not isinstance(pmcid, str) or not re.fullmatch(r"PMC\d+", pmcid, flags=re.IGNORECASE):
            errors.append(f"PMC manifest has invalid PMCID: {display_manifest}")
        expected_record_pmcid = record.get("europepmc_pmcid")
        if (
            isinstance(expected_record_pmcid, str)
            and isinstance(pmcid, str)
            and expected_record_pmcid.casefold() != pmcid.casefold()
        ):
            errors.append(f"PMC manifest PMCID differs from processed record: {display_manifest}")
        versioned_pmcid = manifest.get("versioned_pmcid")
        version_match = (
            re.fullmatch(r"(PMC\d+)\.(\d+)", versioned_pmcid, flags=re.IGNORECASE)
            if isinstance(versioned_pmcid, str)
            else None
        )
        if version_match is None:
            errors.append(f"PMC manifest has invalid versioned PMCID: {display_manifest}")
        else:
            if isinstance(pmcid, str) and version_match.group(1).casefold() != pmcid.casefold():
                errors.append(f"PMC versioned PMCID mismatch: {display_manifest}")
            pmc_version = manifest.get("pmc_version")
            if (
                not isinstance(pmc_version, int)
                or isinstance(pmc_version, bool)
                or pmc_version < 1
                or pmc_version != int(version_match.group(2))
            ):
                errors.append(f"PMC version number mismatch: {display_manifest}")

        package_metadata = manifest.get("package_metadata")
        if not isinstance(package_metadata, dict):
            errors.append(f"PMC package_metadata is not an object: {display_manifest}")
            package_metadata = None
        else:
            if normalized_manifest_doi(package_metadata.get("doi")) != normalize_doi(
                record.get("doi")
            ):
                errors.append(f"PMC package metadata DOI mismatch: {display_manifest}")
            metadata_pmcid = package_metadata.get("pmcid")
            if (
                not isinstance(metadata_pmcid, str)
                or not isinstance(pmcid, str)
                or metadata_pmcid.casefold() != pmcid.casefold()
            ):
                errors.append(f"PMC package metadata PMCID mismatch: {display_manifest}")
            if package_metadata.get("version") != manifest.get("pmc_version"):
                errors.append(f"PMC package metadata version mismatch: {display_manifest}")
            is_manuscript = package_metadata.get("is_manuscript")
            if not isinstance(is_manuscript, bool):
                errors.append(f"PMC package metadata has invalid is_manuscript: {display_manifest}")
            else:
                expected_source_version = (
                    "author_manuscript" if is_manuscript else "published_version"
                )
                if source_version != expected_source_version:
                    errors.append(
                        f"PMC source_version disagrees with package metadata: {display_manifest}"
                    )
                if manifest.get("is_manuscript") != is_manuscript:
                    errors.append(
                        f"PMC top-level is_manuscript disagrees with package metadata: "
                        f"{display_manifest}"
                    )
            if manifest.get("is_pmc_openaccess") != package_metadata.get("is_pmc_openaccess"):
                errors.append(
                    f"PMC top-level open-access status disagrees with package metadata: "
                    f"{display_manifest}"
                )

        package_sha256 = manifest.get("package_metadata_sha256")
        if not is_hex_digest(package_sha256, 64):
            errors.append(f"PMC manifest has no valid package metadata SHA-256: {display_manifest}")
        if schema_version == 2:
            if manifest.get("package_metadata_sha256_scope") != PMC_PACKAGE_HASH_SCOPE:
                errors.append(
                    f"PMC package metadata SHA-256 has unrecognized scope: {display_manifest}"
                )
            if manifest.get("package_metadata_canonicalization") != PMC_PACKAGE_CANONICALIZATION:
                errors.append(
                    f"PMC package metadata canonicalization is unrecognized: {display_manifest}"
                )
            canonical_sha256 = manifest.get("package_metadata_canonical_sha256")
            if not is_hex_digest(canonical_sha256, 64):
                errors.append(
                    f"PMC manifest has no valid canonical package metadata SHA-256: "
                    f"{display_manifest}"
                )
            elif package_metadata is not None:
                try:
                    expected_canonical_sha256 = canonical_json_sha256(package_metadata)
                except (TypeError, ValueError) as error:
                    errors.append(
                        f"PMC package metadata is not canonicalizable: "
                        f"{display_manifest}: {type(error).__name__}: {error}"
                    )
                else:
                    if canonical_sha256.casefold() != expected_canonical_sha256:
                        errors.append(
                            f"PMC canonical package metadata hash mismatch: {display_manifest}"
                        )

            package_path = confined_manifest_path(
                layout,
                paper_dir,
                manifest.get("package_metadata_local_path"),
                label=f"PMC package metadata in {display_manifest}",
                errors=errors,
            )
            if package_path is not None:
                referenced_paths.add(package_path)
                if not package_path.is_file():
                    errors.append(
                        f"PMC package metadata file is missing: {layout.display_path(package_path)}"
                    )
                else:
                    if (
                        is_hex_digest(package_sha256, 64)
                        and sha256_file(package_path) != package_sha256.casefold()
                    ):
                        errors.append(
                            f"PMC raw package metadata hash mismatch: "
                            f"{layout.display_path(package_path)}"
                        )
                    try:
                        stored_package = json.loads(package_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
                        errors.append(
                            f"cannot read PMC raw package metadata "
                            f"{layout.display_path(package_path)}: "
                            f"{type(error).__name__}: {error}"
                        )
                    else:
                        if package_metadata is not None and stored_package != package_metadata:
                            errors.append(
                                f"PMC raw and embedded package metadata differ: {display_manifest}"
                            )
        elif schema_version == 1:
            legacy_package_hashes += 1

        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            errors.append(f"PMC artifacts is not an array: {display_manifest}")
            continue
        seen_artifact_paths: set[Path] = set()
        for index, artifact in enumerate(artifacts):
            artifact_label = f"PMC artifact {index} in {display_manifest}"
            if not isinstance(artifact, dict):
                errors.append(f"{artifact_label} is not an object")
                continue
            role = artifact.get("role")
            status = artifact.get("status")
            if role not in PMC_ARTIFACT_ROLES:
                errors.append(f"{artifact_label} has unrecognized role")
            if status not in PMC_ARTIFACT_STATUSES:
                errors.append(f"{artifact_label} has unrecognized status")

            artifact_path = None
            if artifact.get("local_path"):
                artifact_path = confined_manifest_path(
                    layout,
                    paper_dir,
                    artifact.get("local_path"),
                    label=artifact_label,
                    errors=errors,
                )
                if artifact_path is not None:
                    referenced_paths.add(artifact_path)
                    if artifact_path in seen_artifact_paths:
                        errors.append(f"{artifact_label} duplicates another local_path")
                    seen_artifact_paths.add(artifact_path)
            elif status != "failed":
                errors.append(f"{artifact_label} has no local_path")

            if status == "failed":
                if artifact.get("sha256") is not None and not is_hex_digest(
                    artifact.get("sha256"), 64
                ):
                    errors.append(f"{artifact_label} has an invalid SHA-256")
                continue
            if artifact_path is None:
                continue
            if not artifact_path.is_file():
                errors.append(
                    f"{artifact_label} file is missing: {layout.display_path(artifact_path)}"
                )
                continue
            expected_sha256 = artifact.get("sha256")
            expected_md5 = artifact.get("md5")
            valid_sha256 = is_hex_digest(expected_sha256, 64)
            valid_md5 = is_hex_digest(expected_md5, 32)
            if not valid_sha256:
                errors.append(f"{artifact_label} has no valid SHA-256")
            if not valid_md5:
                errors.append(f"{artifact_label} has no valid MD5")
            actual_md5, actual_sha256, actual_bytes = file_digests(artifact_path)
            if valid_sha256 and actual_sha256 != expected_sha256.casefold():
                errors.append(
                    f"PMC artifact SHA-256 mismatch: {layout.display_path(artifact_path)}"
                )
            if valid_md5 and actual_md5 != expected_md5.casefold():
                errors.append(f"PMC artifact MD5 mismatch: {layout.display_path(artifact_path)}")
            if artifact.get("bytes") != actual_bytes:
                errors.append(
                    f"PMC artifact byte count mismatch: {layout.display_path(artifact_path)}"
                )
            if (
                role == "article_pdf"
                and valid_sha256
                and actual_sha256 == (expected_sha256.casefold())
            ):
                try:
                    pdf.verify_pdf(artifact_path, record)
                except Exception as error:  # noqa: BLE001 - collect every PMC failure
                    errors.append(
                        f"PMC article PDF identity failed: "
                        f"{layout.display_path(artifact_path)}: "
                        f"{type(error).__name__}: {error}"
                    )

    for pattern in ("*/*/pmc/**/*", "*/*/media/**/*"):
        for artifact_path in layout.papers.glob(pattern):
            if artifact_path.is_file() and artifact_path.resolve() not in referenced_paths:
                errors.append(
                    f"orphan PMC file is not referenced by a manifest: "
                    f"{layout.display_path(artifact_path)}"
                )
    for artifact_path in layout.papers.glob("*/*/paper.pdf"):
        if (
            not (artifact_path.parent / "metadata.json").exists()
            and artifact_path.resolve() not in referenced_paths
        ):
            errors.append(
                f"orphan article PDF is not referenced by generic or PMC metadata: "
                f"{layout.display_path(artifact_path)}"
            )
    if legacy_package_hashes:
        warnings.append(
            f"{legacy_package_hashes} schema-v1 PMC manifests hash raw package metadata "
            "responses that were not retained; rerun PMC acquisition to upgrade them to "
            "offline-verifiable schema v2"
        )
    return errors, warnings
