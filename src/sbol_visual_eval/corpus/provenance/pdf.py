"""Article-PDF identity verification against a processed paper record."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from rapidfuzz.fuzz import partial_ratio

from ..util.text import normalize_title


def _record_explicitly_single_page(record: dict[str, Any]) -> bool:
    pages = record.get("pages")
    if not isinstance(pages, str):
        return False
    match = re.fullmatch(
        r"\s*([A-Za-z]*)(\d+)\s*[-\u2010-\u2015\u2212]\s*([A-Za-z]*)(\d+)\s*",
        pages,
    )
    return bool(
        match
        and match.group(1).casefold() == match.group(3).casefold()
        and int(match.group(2)) == int(match.group(4))
    )


def verify_pdf(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    with path.open("rb") as handle:
        if b"%PDF" not in handle.read(1024):
            raise ValueError("response does not start with a PDF header")
    reader = PdfReader(path)
    if not reader.pages:
        raise ValueError("PDF has no pages")
    text_parts: list[str] = []
    for page in reader.pages[:5]:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001  # pragma: no cover - producer-specific
            text_parts.append("")
    text = "\n".join(text_parts)
    first_page_lead = normalize_title(text_parts[0][:1500]) if text_parts else ""
    if any(
        first_page_lead.startswith(marker)
        for marker in (
            "supplementaryinformation",
            "supplementalinformation",
            "supportinginformation",
        )
    ):
        raise ValueError("PDF identifies itself as supplementary/supporting information")
    metadata = reader.metadata
    metadata_title = str(getattr(metadata, "title", "") or "") if metadata else ""
    front_matter = "\n".join(text_parts[:2]).casefold()
    normalized_front_matter = normalize_title(front_matter)
    metadata_document_type = bool(
        re.search(r"\b(?:thesis|dissertation)\b", metadata_title, flags=re.IGNORECASE)
    )
    degree_submission = any(
        marker in normalized_front_matter
        for marker in (
            "inpartialfulfillmentoftherequirementsforthedegree",
            "submittedinpartialfulfillment",
            "doctorateofphilosophy",
            "doctorofphilosophy",
            "thesissupervisor",
            "doctoraldissertation",
        )
    )
    if metadata_document_type or degree_submission:
        raise ValueError("PDF identifies itself as a thesis or dissertation")
    first_page_text = normalize_title(text_parts[0]) if text_parts else ""
    repository_cover_markers = sum(
        marker in first_page_text
        for marker in (
            "downloadedfrom",
            "publishedin",
            "linktoarticledoi",
            "documentversion",
            "citationapa",
            "generalrights",
        )
    )
    second_page_text = text_parts[1] if len(text_parts) > 1 else ""
    second_page_words = re.findall(r"\b[a-zA-Z][a-zA-Z-]{2,}\b", second_page_text)
    figure_only_second_page = bool(
        re.search(r"\bfigure\s*(?:s?\d+|[ivx]+)\b", second_page_text, re.IGNORECASE)
        and len(second_page_words) < 80
        and not re.search(
            r"\b(?:abstract|introduction|methods?|results?|discussion|references)\b",
            second_page_text,
            re.IGNORECASE,
        )
    )
    if len(reader.pages) == 2 and repository_cover_markers >= 3 and figure_only_second_page:
        raise ValueError("PDF is a repository citation cover plus a figure-only partial file")
    compact_text = re.sub(r"\s+", "", text).casefold()
    doi_match = bool(record.get("doi") and record["doi"].casefold() in compact_text)
    title = record.get("title_crossref") or record["title_source"]
    normalized_title = normalize_title(title)
    normalized_text = normalize_title(text)
    title_score = partial_ratio(normalized_title, normalized_text) if normalized_text else 0.0
    if len(reader.pages) == 1 and (
        not _record_explicitly_single_page(record) or (not doi_match and title_score < 95)
    ):
        raise ValueError(
            "one-page PDF is not supported by explicit single-page pagination "
            "and strong DOI/title identity"
        )
    if not doi_match and title_score < 75:
        raise ValueError(f"first-page identity check failed (title similarity {title_score:.1f})")
    return {
        "artifact_type": "article_pdf",
        "page_count": len(reader.pages),
        "identity_check": "doi" if doi_match else "title",
        "title_similarity": round(title_score / 100, 6),
    }
