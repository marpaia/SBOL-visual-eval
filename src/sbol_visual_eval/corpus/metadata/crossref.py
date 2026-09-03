"""Crossref journal metadata: retrieval and title-based record matching."""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import quote

import httpx
from rapidfuzz.fuzz import ratio

from ..config import USER_AGENT
from ..layout import Layout
from ..util.net import request_with_retry
from ..util.storage import utc_now, write_json
from ..util.text import clean_markup, normalize_doi, normalize_title
from .caching import _cache_payload

CROSSREF_URL = "https://api.crossref.org/journals/2161-5063/works"


CROSSREF_FUZZY_ADJUDICATIONS = {
    "10.1021/acssynbio.7b00338": "workbook title has author names appended",
    "10.1021/acssynbio.8b00188": (
        "source and Crossref titles differ only in alpha-character encoding"
    ),
    "10.1021/acssynbio.9b00080": "workbook title is missing the leading 'As'",
    "10.1021/acssynbio.9b00210": "workbook title is missing the leading 'S'",
}


def fetch_crossref(
    layout: Layout, *, offline: bool = False, refresh: bool = False
) -> dict[str, Any]:
    path = layout.cache / "crossref_acs_synthetic_biology_2011_2024.json"
    cached = _cache_payload(path, offline=offline, refresh=refresh)
    if cached is not None:
        return cached

    items: list[dict[str, Any]] = []
    params: dict[str, Any] = {
        "filter": "from-pub-date:2011-01-01,until-pub-date:2024-12-31,type:journal-article",
        "rows": 1000,
        "cursor": "*",
        "select": (
            "DOI,title,author,published,published-print,published-online,created,URL,link,"
            "license,type,container-title,volume,issue,page,publisher"
        ),
    }
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        while True:
            response = request_with_retry(
                client, "GET", CROSSREF_URL, params=params, timeout=90, follow_redirects=True
            )
            response.raise_for_status()
            message = response.json()["message"]
            batch = message["items"]
            items.extend(batch)
            if len(batch) < params["rows"]:
                break
            params["cursor"] = message["next-cursor"]

    payload = {
        "retrieved_at": utc_now(),
        "request_url": CROSSREF_URL,
        "request_filter": params["filter"],
        "journal_issn": "2161-5063",
        "items": items,
    }
    write_json(path, payload)
    print(f"Fetched {len(items):,} Crossref journal records")
    return payload


def _date_parts(item: dict[str, Any]) -> str | None:
    for field in ("published-print", "published", "published-online", "created"):
        parts = item.get(field, {}).get("date-parts", [])
        if not parts or not parts[0]:
            continue
        values = [int(value) for value in parts[0]][:3]
        values += [1] * (3 - len(values))
        try:
            return f"{values[0]:04d}-{values[1]:02d}-{values[2]:02d}"
        except (TypeError, ValueError):
            continue
    return None


def _author_names(item: dict[str, Any]) -> list[str]:
    names = []
    for author in item.get("author", []):
        name = " ".join(part for part in (author.get("given"), author.get("family")) if part)
        if name:
            names.append(name)
    return names


def match_crossref(
    records: list[dict[str, Any]], payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_volume: dict[int, list[dict[str, Any]]] = defaultdict(list)
    all_items = payload["items"]
    for item in all_items:
        volume = str(item.get("volume", ""))
        if volume.isdigit():
            by_volume[int(volume)].append(item)

    review: list[dict[str, Any]] = []
    for record in records:
        expected_volume = record["year"] - 2011
        candidates = by_volume.get(expected_volume, all_items)
        source_normalized = normalize_title(record["title_source"])
        exact_candidates = [
            item
            for item in candidates
            if normalize_title((item.get("title") or [""])[0]) == source_normalized
        ]

        ranked = sorted(
            [
                (
                    ratio(source_normalized, normalize_title((item.get("title") or [""])[0])),
                    item,
                )
                for item in candidates
            ],
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best_item = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        margin = best_score - second_score

        if len(exact_candidates) == 1:
            selected = exact_candidates[0]
            match_method = "normalized_exact"
            best_score = 100.0
        elif best_score >= 85.0 and margin >= 15.0:
            selected = best_item
            match_method = "fuzzy_unique"
        else:
            selected = None
            match_method = "unresolved"

        if selected is None:
            doi = None
            crossref_title = None
            authors: list[str] = []
            publication_date = None
            record.update(
                {
                    "doi": None,
                    "doi_url": None,
                    "publisher_url": None,
                    "title_crossref": None,
                    "authors": [],
                    "publication_date": None,
                    "volume": None,
                    "issue": None,
                    "pages": None,
                    "crossref_license_urls": [],
                    "crossref_full_text_links": [],
                    "crossref_match_method": match_method,
                    "crossref_title_score": round(best_score / 100, 6),
                    "crossref_match_margin": round(margin / 100, 6),
                }
            )
        else:
            doi = normalize_doi(selected.get("DOI"))
            crossref_title = clean_markup((selected.get("title") or [""])[0])
            authors = _author_names(selected)
            publication_date = _date_parts(selected)
            record.update(
                {
                    "record_id": f"doi:{doi}",
                    "doi": doi,
                    "doi_url": f"https://doi.org/{quote(doi, safe='/')}",
                    "publisher_url": selected.get("URL"),
                    "title_crossref": crossref_title,
                    "authors": authors,
                    "publication_date": publication_date,
                    "volume": selected.get("volume"),
                    "issue": selected.get("issue"),
                    "pages": selected.get("page"),
                    "crossref_license_urls": [
                        license_info.get("URL")
                        for license_info in selected.get("license", [])
                        if license_info.get("URL")
                    ],
                    "crossref_full_text_links": [
                        {
                            "url": link.get("URL"),
                            "content_type": link.get("content-type"),
                            "content_version": link.get("content-version"),
                            "intended_application": link.get("intended-application"),
                        }
                        for link in selected.get("link", [])
                        if link.get("URL")
                    ],
                    "crossref_match_method": match_method,
                    "crossref_title_score": round(best_score / 100, 6),
                    "crossref_match_margin": round(margin / 100, 6),
                }
            )

        if match_method != "normalized_exact":
            review_rationale = CROSSREF_FUZZY_ADJUDICATIONS.get(doi or "")
            review.append(
                {
                    "year": record["year"],
                    "issue_month": record["issue_month"],
                    "title_source": record["title_source"],
                    "match_method": match_method,
                    "title_score": round(best_score / 100, 6),
                    "runner_up_margin": round(margin / 100, 6),
                    "matched_doi": doi,
                    "matched_title": crossref_title,
                    "candidate_volume": best_item.get("volume"),
                    "candidate_issue": best_item.get("issue"),
                    "review_status": (
                        "accepted_manual_review" if review_rationale else "pending_review"
                    ),
                    "review_rationale": review_rationale,
                }
            )
    return records, review
