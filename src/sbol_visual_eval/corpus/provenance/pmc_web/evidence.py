"""Reading and hash-checking the immutable evidence snapshots a manifest cites."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ...layout import Layout
from ..manifests import is_hex_digest

PMC_WEB_VARIANT_DIRECTORY = "pmc_author_manuscript"
PMC_WEB_MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


def _pmc_web_evidence_snapshot(
    layout: Layout,
    manifest: dict[str, Any],
    paper_dir: Path,
    *,
    path_field: str,
    sha256_field: str,
    bytes_field: str | None,
    filename_stem: str,
    filename_suffix: str,
    errors: list[str],
) -> tuple[Path | None, bytes | None]:
    raw_path = manifest.get(path_field)
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"PMC web manifest has no {path_field}")
        return None, None
    declared_relative_path = Path(raw_path)
    if declared_relative_path.is_absolute() or raw_path != declared_relative_path.as_posix():
        errors.append(f"PMC web {path_field} is not a canonical repository-relative path")
        return None, None
    expected_directory = paper_dir / "versions" / PMC_WEB_VARIANT_DIRECTORY
    try:
        expected_relative_directory = expected_directory.relative_to(layout.root)
    except ValueError:
        errors.append(f"PMC web {path_field} has no repository-relative variant directory")
        return None, None
    if declared_relative_path.parent != expected_relative_directory:
        errors.append(f"PMC web {path_field} is not directly in its variant directory")
        return None, None

    expected_hash = manifest.get(sha256_field)
    if not is_hex_digest(expected_hash, 64):
        errors.append(f"PMC web manifest has no valid {sha256_field}")
        expected_hash = None
    if not re.fullmatch(
        rf"{re.escape(filename_stem)}__[0-9a-f]{{12}}{re.escape(filename_suffix)}",
        declared_relative_path.name,
    ):
        errors.append(f"PMC web {path_field} has an invalid content-addressed filename")
    elif expected_hash is not None and declared_relative_path.name != (
        f"{filename_stem}__{expected_hash[:12].casefold()}{filename_suffix}"
    ):
        errors.append(f"PMC web {path_field} filename does not match its SHA-256")

    path = layout.root / declared_relative_path
    candidate = path
    while candidate != layout.root:
        if candidate.is_symlink():
            errors.append(f"PMC web {path_field} path may not contain symlinks")
            return path, None
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    try:
        path = path.resolve()
        path.relative_to(expected_directory.resolve())
    except ValueError:
        errors.append(f"PMC web {path_field} escapes its variant directory")
        return None, None
    if not path.is_file():
        errors.append(f"PMC web {path_field} file is missing")
        return path, None
    if bytes_field is not None:
        declared_bytes = manifest.get(bytes_field)
        if (
            not isinstance(declared_bytes, int)
            or isinstance(declared_bytes, bool)
            or not 0 < declared_bytes <= PMC_WEB_MAX_EVIDENCE_BYTES
        ):
            errors.append(f"PMC web {path_field} has an invalid declared byte count")
    try:
        stat_size = path.stat().st_size
    except OSError as error:
        errors.append(f"PMC web {path_field} cannot be read: {type(error).__name__}: {error}")
        return path, None
    if not 0 < stat_size <= PMC_WEB_MAX_EVIDENCE_BYTES:
        errors.append(f"PMC web {path_field} exceeds the evidence safety limit")
        return path, None
    try:
        with path.open("rb") as handle:
            payload = handle.read(PMC_WEB_MAX_EVIDENCE_BYTES + 1)
    except OSError as error:
        errors.append(f"PMC web {path_field} cannot be read: {type(error).__name__}: {error}")
        return path, None
    if len(payload) > PMC_WEB_MAX_EVIDENCE_BYTES:
        errors.append(f"PMC web {path_field} exceeds the evidence safety limit")
        return path, None
    actual_hash = hashlib.sha256(payload).hexdigest()
    if expected_hash is not None and actual_hash != expected_hash.casefold():
        errors.append(f"PMC web {path_field} SHA-256 mismatch")
    if bytes_field is not None and manifest.get(bytes_field) != len(payload):
        errors.append(f"PMC web {path_field} byte count mismatch")
    return path, payload


def _pmc_web_page_evidence(
    payload: bytes,
    *,
    article_url: str,
    source_url: str,
) -> tuple[bool, bool]:
    soup = BeautifulSoup(payload, "html.parser")
    for excluded in soup(["script", "style", "noscript"]):
        excluded.decompose()
    banner = False
    for element in soup.find_all(True):
        marker = " ".join(
            value
            for value in (
                str(element.get("id") or ""),
                " ".join(str(value) for value in element.get("class") or []),
            )
            if value
        )
        if re.search(r"\bauthors?[-_\s]+manuscript\b", marker, re.IGNORECASE) and (
            re.search(
                r"\bauthor\s+manuscript\b",
                element.get_text(" ", strip=True),
                re.IGNORECASE,
            )
        ):
            banner = True
            break

    linked_pdf = False
    for meta in soup.select("meta[content]"):
        name = str(meta.get("name") or meta.get("property") or "").casefold()
        if (
            name == "citation_pdf_url"
            and urljoin(article_url, str(meta.get("content") or "").strip()) == source_url
        ):
            linked_pdf = True
            break
    if not linked_pdf:
        linked_pdf = any(
            urljoin(article_url, str(anchor.get("href") or "").strip()) == source_url
            for anchor in soup.select("a[href]")
        )
    return banner, linked_pdf
