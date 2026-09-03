"""Verification of individual local artifacts against their provenance manifests."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..provenance import pdf
from ..provenance.manifests import is_hex_digest, is_version_of_record
from ..util.storage import file_digests


def _add_inventory_article_artifact(
    layout: Layout,
    record: dict[str, Any],
    paper_dir: Path,
    artifacts_by_path: dict[tuple[str, str], dict[str, Any]],
    local_file: Path,
    *,
    source: str | None,
    version: str | None,
    kind: str,
    expected_sha256: Any,
    expected_md5: Any = None,
    expected_bytes: Any = None,
    require_md5: bool = False,
    require_bytes: bool = False,
    source_url: str | None,
    provenance_path: Path,
    provenance_status: str = "verified",
    provenance_errors: Sequence[str] = (),
    identity_record: dict[str, Any] | None = None,
) -> None:
    if not isinstance(kind, str) or not kind:
        kind = "unknown"
    verification_errors = list(provenance_errors)
    verification_status = (
        provenance_status
        if provenance_status in {"verified", "unverified", "invalid"}
        else "invalid"
    )
    if verification_status != provenance_status:
        verification_errors.append(
            f"unrecognized provenance verification status: {provenance_status!r}"
        )
    actual_sha256 = None
    actual_md5 = None
    actual_bytes = None
    if kind not in {"article_pdf", "article_text", "article_xml"}:
        verification_status = "invalid"
        verification_errors.append(f"unrecognized article artifact kind: {kind}")
    path_confined = False
    try:
        resolved_local_file = local_file.resolve()
        resolved_local_file.relative_to(paper_dir.resolve())
        path_confined = True
        relative_path = layout.display_path(resolved_local_file)
        resolved_provenance_path = provenance_path.resolve()
        resolved_provenance_path.relative_to(paper_dir.resolve())
        relative_provenance_path = str(resolved_provenance_path.relative_to(layout.root.resolve()))
    except ValueError:
        relative_path = layout.display_path(local_file)
        relative_provenance_path = layout.display_path(provenance_path)
        verification_status = "invalid"
        verification_errors.append("artifact or provenance path escapes its paper directory")
    if not local_file.is_file():
        verification_status = "invalid"
        verification_errors.append("artifact file is missing")
    else:
        actual_md5, actual_sha256, actual_bytes = file_digests(local_file)
        if expected_sha256 is None:
            if verification_status == "verified":
                verification_status = "unverified"
            verification_errors.append("provenance manifest has no SHA-256")
        elif not is_hex_digest(expected_sha256, 64):
            verification_status = "invalid"
            verification_errors.append("provenance manifest has an invalid SHA-256")
        elif expected_sha256.casefold() != actual_sha256:
            verification_status = "invalid"
            verification_errors.append("artifact SHA-256 does not match its provenance manifest")
        if expected_md5 is None:
            if require_md5:
                if verification_status == "verified":
                    verification_status = "unverified"
                verification_errors.append("provenance manifest has no MD5")
        elif not is_hex_digest(expected_md5, 32):
            verification_status = "invalid"
            verification_errors.append("provenance manifest has an invalid MD5")
        elif expected_md5.casefold() != actual_md5:
            verification_status = "invalid"
            verification_errors.append("artifact MD5 does not match its provenance manifest")
        if expected_bytes is None:
            if require_bytes:
                if verification_status == "verified":
                    verification_status = "unverified"
                verification_errors.append("provenance manifest has no byte count")
        elif (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes != actual_bytes
        ):
            verification_status = "invalid"
            verification_errors.append("artifact byte count does not match its provenance manifest")
        if kind == "article_pdf":
            try:
                pdf.verify_pdf(local_file, identity_record or record)
            except Exception as error:  # noqa: BLE001 - inventory records invalid PDFs
                verification_status = "invalid"
                verification_errors.append(
                    f"article PDF identity failed: {type(error).__name__}: {error}"
                )
    artifact = {
        "path": relative_path,
        "source": source or source_url or "unknown",
        "version": version,
        "hash": actual_sha256,
        "hash_algorithm": "sha256",
        "md5": actual_md5,
        "bytes": actual_bytes,
        "kind": kind,
        "is_version_of_record": is_version_of_record(version),
        "source_url": source_url,
        "provenance_path": relative_provenance_path,
        "verification_status": verification_status,
        "verification_errors": verification_errors,
        "local_file_present": path_confined and local_file.is_file(),
    }
    key = (kind, relative_path)
    existing = artifacts_by_path.get(key)
    verification_rank = {"invalid": 0, "unverified": 1, "verified": 2}
    if existing is None or (
        verification_rank[artifact["verification_status"]],
        artifact["is_version_of_record"],
    ) > (
        verification_rank[existing["verification_status"]],
        existing["is_version_of_record"],
    ):
        artifacts_by_path[key] = artifact


def _inventory_issue(
    layout: Layout,
    path: Path,
    *,
    kind: str,
    status: str,
    errors: Sequence[str],
) -> dict[str, Any]:
    return {
        "path": layout.display_path(path),
        "kind": kind,
        "verification_status": status,
        "verification_errors": list(errors),
    }


def _read_inventory_manifest(
    layout: Layout,
    path: Path,
    issues: list[dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
        issues.append(
            _inventory_issue(
                layout,
                path,
                kind="provenance_manifest",
                status="invalid",
                errors=[f"cannot read manifest: {type(error).__name__}: {error}"],
            )
        )
        return None
    if not isinstance(manifest, dict):
        issues.append(
            _inventory_issue(
                layout,
                path,
                kind="provenance_manifest",
                status="invalid",
                errors=["manifest is not a JSON object"],
            )
        )
        return None
    return manifest


def _inventory_nonarticle_file(
    layout: Layout,
    paper_dir: Path,
    local_file: Path,
    *,
    kind: str,
    expected_sha256: Any,
    expected_md5: Any = None,
    expected_bytes: Any = None,
    require_md5: bool = False,
    require_bytes: bool = False,
    provenance_path: Path,
    provenance_status: str = "verified",
    provenance_errors: Sequence[str] = (),
) -> tuple[str, dict[str, Any] | None]:
    errors = list(provenance_errors)
    status = (
        provenance_status
        if provenance_status in {"verified", "unverified", "invalid"}
        else "invalid"
    )
    if status != provenance_status:
        errors.append(f"unrecognized provenance verification status: {provenance_status!r}")
    try:
        resolved_path = local_file.resolve()
        resolved_path.relative_to(paper_dir.resolve())
    except ValueError:
        status = "invalid"
        errors.append("artifact path escapes its paper directory")
    if not local_file.is_file():
        status = "invalid"
        errors.append("artifact file is missing")
    else:
        actual_md5, actual_sha256, actual_bytes = file_digests(local_file)
        if expected_sha256 is None:
            if status == "verified":
                status = "unverified"
            errors.append("provenance manifest has no SHA-256")
        elif not is_hex_digest(expected_sha256, 64):
            status = "invalid"
            errors.append("provenance manifest has an invalid SHA-256")
        elif actual_sha256 != expected_sha256.casefold():
            status = "invalid"
            errors.append("artifact SHA-256 does not match its provenance manifest")
        if expected_md5 is None:
            if require_md5:
                if status == "verified":
                    status = "unverified"
                errors.append("provenance manifest has no MD5")
        elif not is_hex_digest(expected_md5, 32):
            status = "invalid"
            errors.append("provenance manifest has an invalid MD5")
        elif actual_md5 != expected_md5.casefold():
            status = "invalid"
            errors.append("artifact MD5 does not match its provenance manifest")
        if expected_bytes is None:
            if require_bytes:
                if status == "verified":
                    status = "unverified"
                errors.append("provenance manifest has no byte count")
        elif (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or actual_bytes != expected_bytes
        ):
            status = "invalid"
            errors.append("artifact byte count does not match its provenance manifest")
    issue = None
    if status != "verified":
        issue = _inventory_issue(
            layout,
            local_file,
            kind=kind,
            status=status,
            errors=[*errors, f"provenance: {layout.display_path(provenance_path)}"],
        )
    return status, issue
