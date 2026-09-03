"""URL-shape rules for PMC article pages and main-article PDFs."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse


def _normalized_pmcid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper().split(".", 1)[0]
    return normalized if re.fullmatch(r"PMC\d+", normalized) else None


def _pmc_web_article_url_error(value: Any, pmcid: str, field_name: str) -> str | None:
    if not isinstance(value, str):
        return f"PMC web {field_name} is not a URL"
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return f"PMC web {field_name} is not a valid URL"
    expected_path = f"/articles/{pmcid}/"
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "pmc.ncbi.nlm.nih.gov"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or unquote(parsed.path).casefold() != expected_path.casefold()
        or parsed.query
        or parsed.fragment
    ):
        return f"PMC web {field_name} is not the processed paper's PMC article URL"
    return None


def _pmc_web_pdf_url_error(value: Any, pmcid: str, field_name: str) -> str | None:
    if not isinstance(value, str):
        return f"PMC web {field_name} is not a URL"
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return f"PMC web {field_name} is not a valid URL"
    decoded_paths = [parsed.path]
    for _ in range(8):
        decoded_path = unquote(decoded_paths[-1])
        if decoded_path == decoded_paths[-1]:
            break
        decoded_paths.append(decoded_path)
    else:
        return f"PMC web {field_name} has excessive nested URL encoding"
    path_segments = decoded_path.split("/")
    expected_prefix = f"/articles/{pmcid}/pdf/"
    normalized_path = decoded_path.casefold()
    relative_pdf_path = decoded_path[len(expected_prefix) :]
    basename = path_segments[-1].casefold()
    supplementary = bool(
        re.search(
            r"(?:^|[/_.-])supp(?:l|lement(?:al|ary)?)?(?:[/_.-]|$)",
            normalized_path,
        )
        or re.search(r"(?:^|[/_.-])supporting(?:[/_.-]|$)", normalized_path)
        or re.search(r"(?:^|[_\-.])s\d{1,3}\.pdf$", basename)
    )
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "pmc.ncbi.nlm.nih.gov"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or "%" in parsed.path
        or not normalized_path.startswith(expected_prefix.casefold())
        or not normalized_path.endswith(".pdf")
        or not relative_pdf_path
        or "/" in relative_pdf_path
        or "\\" in decoded_path
        or any(segment in {".", ".."} for segment in path_segments)
        or any(re.search(r"%(?:2e|2f|5c)", path, flags=re.IGNORECASE) for path in decoded_paths)
        or parsed.query
        or parsed.fragment
        or supplementary
    ):
        return f"PMC web {field_name} is not a main PDF beneath the processed paper's PMC path"
    return None
