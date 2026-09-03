"""Discovery of the one unambiguous main PDF link on a public PMC article page."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .urls import _is_supplement_link, _validated_article_url, _validated_pmc_pdf_url


def _discover_main_pdf_url(payload: bytes, article_url: str, pmcid: str) -> str:
    """Return one unambiguous main-article PDF URL from a public PMC HTML page."""

    _validated_article_url(article_url, pmcid, field_name="article_url")
    soup = BeautifulSoup(payload, "html.parser")
    ranked: dict[str, int] = {}

    for meta in soup.select("meta[content]"):
        name = str(meta.get("name") or meta.get("property") or "").casefold()
        if name != "citation_pdf_url":
            continue
        candidate = urljoin(article_url, str(meta.get("content") or "").strip())
        try:
            candidate = _validated_pmc_pdf_url(candidate, pmcid, field_name="citation_pdf_url")
        except ValueError:
            continue
        ranked[candidate] = min(ranked.get(candidate, 99), 0)

    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        label = " ".join(
            value
            for value in (
                anchor.get_text(" ", strip=True),
                str(anchor.get("title") or ""),
                str(anchor.get("aria-label") or ""),
            )
            if value
        )
        candidate = urljoin(article_url, href)
        if _is_supplement_link(candidate, label):
            continue
        try:
            candidate = _validated_pmc_pdf_url(candidate, pmcid, field_name="PDF link")
        except ValueError:
            continue
        normalized_label = label.casefold()
        rank = 1 if "pdf" in normalized_label or href.casefold().startswith("pdf/") else 2
        ranked[candidate] = min(ranked.get(candidate, 99), rank)

    if not ranked:
        raise ValueError("public PMC article page contains no eligible main PDF link")
    best_rank = min(ranked.values())
    best = sorted(url for url, rank in ranked.items() if rank == best_rank)
    if len(best) != 1:
        raise ValueError("public PMC article page contains ambiguous main PDF links")
    return best[0]


def _pmc_challenge_reason(payload: bytes) -> str | None:
    """Recognize PMC interstitials without treating incidental article prose as a challenge."""

    soup = BeautifulSoup(payload, "html.parser")
    prominent_text = " ".join(
        element.get_text(" ", strip=True)
        for element in soup.select("title, h1, h2, [role='heading']")
    ).casefold()
    if "preparing to download" in prominent_text:
        return "preparing_to_download"
    if "checking your browser" in prominent_text:
        return "checking_your_browser"
    if "recaptcha" in prominent_text:
        return "recaptcha"
    if soup.select_one(
        ".g-recaptcha, [data-sitekey], iframe[src*='recaptcha'], script[src*='recaptcha']"
    ):
        return "recaptcha"
    return None


def _page_author_manuscript_assertion(payload: bytes, pdf_url: str) -> dict[str, Any] | None:
    """Extract narrow, non-secret evidence that a public PMC PDF is a manuscript."""

    soup = BeautifulSoup(payload, "html.parser")
    for excluded in soup(["script", "style", "noscript"]):
        excluded.decompose()
    phrase_evidence = False
    for element in soup.find_all(True):
        marker = " ".join(
            value
            for value in (
                str(element.get("id") or ""),
                " ".join(str(value) for value in element.get("class") or []),
            )
            if value
        )
        if not re.search(r"\bauthors?[-_\s]+manuscript\b", marker, re.IGNORECASE):
            continue
        if re.search(
            r"\bauthor\s+manuscript\b",
            element.get_text(" ", strip=True),
            re.IGNORECASE,
        ):
            phrase_evidence = True
            break
    basename = Path(unquote(urlparse(pdf_url).path)).name
    filename_evidence = bool(
        re.fullmatch(
            r"nihms[-_]?\d+(?:[._-][a-z0-9]+)*\.pdf",
            basename,
            flags=re.IGNORECASE,
        )
    )
    evidence: list[dict[str, str]] = []
    if phrase_evidence:
        evidence.append(
            {
                "kind": "public_page_author_manuscript_banner",
                "value": "Author manuscript",
            }
        )
    if filename_evidence:
        evidence.append(
            {
                "kind": "pmc_pdf_basename_pattern",
                "value": basename,
            }
        )
    if not evidence:
        return None
    if phrase_evidence and filename_evidence:
        method = "pmc_public_page_banner_and_nihms_pdf_basename"
    elif phrase_evidence:
        method = "pmc_public_page_author_manuscript_banner"
    else:
        method = "pmc_nihms_pdf_basename"
    return {"method": method, "evidence": evidence}
