"""Shared manifest-reading and artifact-verification helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..util.storage import sha256_file
from ..util.text import normalize_doi
from . import pdf


def is_version_of_record(version: Any) -> bool:
    if not isinstance(version, str):
        return False
    normalized = re.sub(r"[^a-z0-9]+", "", version.casefold())
    return normalized in {"publishedversion", "versionofrecord", "vor"}


def read_artifact_metadata(
    layout: Layout, metadata_path: Path, errors: list[str]
) -> dict[str, Any] | None:
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        errors.append(
            f"cannot read paper artifact metadata {layout.display_path(metadata_path)}: "
            f"{type(error).__name__}: {error}"
        )
        return None
    if not isinstance(payload, dict):
        errors.append(
            f"paper artifact metadata is not an object: {layout.display_path(metadata_path)}"
        )
        return None
    return payload


def validate_article_pdf_artifact(
    layout: Layout,
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
    metadata_path: Path,
    metadata: dict[str, Any],
    errors: list[str],
    *,
    identity_record: dict[str, Any] | None = None,
) -> None:
    display_pdf_path = layout.display_path(pdf_path)
    try:
        pdf_path.resolve().relative_to(paper_dir.resolve())
        metadata_path.resolve().relative_to(paper_dir.resolve())
    except ValueError:
        errors.append(f"paper artifact path escapes its paper directory: {display_pdf_path}")
        return
    if not pdf_path.exists():
        errors.append(f"paper artifact metadata has no PDF: {layout.display_path(metadata_path)}")
        return
    expected_sha256 = metadata.get("sha256")
    if not isinstance(expected_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_sha256
    ):
        errors.append(f"paper artifact has no valid SHA-256: {display_pdf_path}")
        return
    if expected_sha256.casefold() != sha256_file(pdf_path):
        errors.append(f"downloaded paper hash mismatch: {display_pdf_path}")
        return
    try:
        pdf.verify_pdf(pdf_path, identity_record or record)
    except Exception as error:  # noqa: BLE001 - collect all local artifact failures
        errors.append(
            f"downloaded paper validation failed: {display_pdf_path}: "
            f"{type(error).__name__}: {error}"
        )


def normalized_manifest_doi(value: Any) -> str | None:
    return normalize_doi(value) if isinstance(value, str) else None


def is_hex_digest(value: Any, length: int) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value))


def confined_manifest_path(
    layout: Layout,
    paper_dir: Path,
    raw_path: Any,
    *,
    label: str,
    errors: list[str],
) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"{label} has no local_path")
        return None
    declared_path = Path(raw_path)
    if not declared_path.is_absolute():
        declared_path = layout.root / declared_path
    try:
        resolved_path = declared_path.resolve()
        resolved_path.relative_to(paper_dir.resolve())
    except ValueError:
        errors.append(f"{label} local_path escapes its paper directory: {raw_path}")
        return None
    return resolved_path
