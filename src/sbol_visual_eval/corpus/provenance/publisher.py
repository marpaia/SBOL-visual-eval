"""Identity checks for authenticated publisher (ACS VOR) manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..layout import Layout
from ..util.text import normalize_doi
from .manifests import is_version_of_record, normalized_manifest_doi


def publisher_manifest_identity_errors(
    layout: Layout,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("record_id") != record["record_id"]:
        errors.append("publisher record_id mismatch")
    if normalized_manifest_doi(manifest.get("doi")) != normalize_doi(record.get("doi")):
        errors.append("publisher DOI mismatch")
    if manifest.get("artifact_type") != "article_pdf":
        errors.append("publisher artifact type is not article_pdf")
    if not is_version_of_record(manifest.get("source_version")):
        errors.append("publisher artifact is not explicitly identified as a Version of Record")
    local_path = manifest.get("local_path")
    if not isinstance(local_path, str) or not local_path:
        errors.append("publisher manifest has no local_path")
    else:
        declared_path = Path(local_path)
        if not declared_path.is_absolute():
            declared_path = layout.root / declared_path
        try:
            resolved_path = declared_path.resolve()
            resolved_path.relative_to(paper_dir.resolve())
        except ValueError:
            errors.append("publisher local_path escapes its paper directory")
        else:
            if resolved_path != pdf_path.resolve():
                errors.append("publisher local_path identifies the wrong PDF")
    if pdf_path.is_file() and manifest.get("bytes") != pdf_path.stat().st_size:
        errors.append("publisher artifact byte count mismatch")
    return errors
