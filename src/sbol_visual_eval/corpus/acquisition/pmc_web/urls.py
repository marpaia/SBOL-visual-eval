"""Strict URL validation for PMC article pages and main-article PDF links."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ...util.text import normalize_doi

PMC_ORIGIN = "https://pmc.ncbi.nlm.nih.gov"


def _normalized_pmcid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper().split(".", 1)[0]
    return normalized if re.fullmatch(r"PMC\d+", normalized) else None


def _normalized_doi(value: Any) -> str | None:
    return normalize_doi(value) if isinstance(value, str) else None


def _article_url(pmcid: str) -> str:
    normalized = _normalized_pmcid(pmcid)
    if normalized is None:
        raise ValueError(f"invalid PMCID: {pmcid!r}")
    return f"{PMC_ORIGIN}/articles/{normalized}/"


def _is_supplement_link(url: str, label: str = "") -> bool:
    parsed = urlparse(url)
    decoded_path = unquote(parsed.path).casefold()
    basename = Path(decoded_path).name
    normalized_label = re.sub(r"[^a-z]+", " ", label.casefold()).strip()
    return bool(
        re.search(r"(?:^|[/_.-])supp(?:l|lement(?:al|ary)?)?(?:[/_.-]|$)", decoded_path)
        or re.search(r"(?:^|[/_.-])supporting(?:[/_.-]|$)", decoded_path)
        or re.search(r"(?:^|[_\-.])s\d{1,3}\.pdf$", basename)
        or re.search(
            r"\b(?:supplement|supplemental|supplementary|supporting information)\b",
            normalized_label,
        )
    )


def _validated_pmc_pdf_url(value: str, pmcid: str, *, field_name: str) -> str:
    normalized_pmcid = _normalized_pmcid(pmcid)
    if normalized_pmcid is None:
        raise ValueError("record has an invalid PMCID")
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} is not a valid URL") from error
    expected_prefix = f"/articles/{normalized_pmcid}/pdf/".casefold()
    decoded_path = unquote(parsed.path)
    relative_pdf_path = decoded_path[len(expected_prefix) :]
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "pmc.ncbi.nlm.nih.gov"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or "%" in parsed.path
        or not decoded_path.casefold().startswith(expected_prefix)
        or not decoded_path.casefold().endswith(".pdf")
        or not relative_pdf_path
        or "/" in relative_pdf_path
        or "\\" in decoded_path
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        or parsed.query
        or parsed.fragment
        or _is_supplement_link(value)
    ):
        raise ValueError(
            f"{field_name} must be a non-supplement PDF beneath the record's HTTPS PMC article path"
        )
    return value


def _validated_article_url(value: str, pmcid: str, *, field_name: str) -> str:
    expected = _article_url(pmcid)
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} is not a valid URL") from error
    normalized = f"{parsed.scheme.casefold()}://{(parsed.hostname or '').casefold()}{parsed.path}"
    if (
        parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or normalized.casefold() != expected.casefold()
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} is not the expected HTTPS PMC article URL")
    return expected
