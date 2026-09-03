"""OpenAlex works metadata: retrieval and open-access candidate attachment."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import USER_AGENT
from ..layout import Layout
from ..util.net import request_with_retry
from ..util.storage import utc_now, write_json
from ..util.text import normalize_doi
from .caching import _cache_payload
from .candidates import (
    crossref_pdf_candidates,
    deduplicate_pdf_candidates,
    openalex_pdf_candidates,
)

OPENALEX_URL = "https://api.openalex.org/works"


def fetch_openalex(
    layout: Layout, *, offline: bool = False, refresh: bool = False
) -> dict[str, Any]:
    path = layout.cache / "openalex_acs_synthetic_biology_2012_2023.json"
    cached = _cache_payload(path, offline=offline, refresh=refresh)
    if cached is not None:
        return cached

    params: dict[str, Any] = {
        "filter": (
            "primary_location.source.issn:2161-5063,"
            "from_publication_date:2012-01-01,to_publication_date:2023-12-31"
        ),
        "per-page": 200,
        "cursor": "*",
        "select": (
            "id,doi,title,publication_year,open_access,best_oa_location,primary_location,"
            "ids,locations"
        ),
    }
    polite_email = os.environ.get("OPENALEX_POLITE_EMAIL")
    if polite_email:
        params["mailto"] = polite_email
    works: list[dict[str, Any]] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        while True:
            response = request_with_retry(
                client, "GET", OPENALEX_URL, params=params, timeout=90, follow_redirects=True
            )
            response.raise_for_status()
            payload = response.json()
            works.extend(payload["results"])
            next_cursor = payload["meta"].get("next_cursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor

    payload = {
        "retrieved_at": utc_now(),
        "request_url": OPENALEX_URL,
        "request_filter": params["filter"],
        "journal_issn": "2161-5063",
        "items": works,
    }
    write_json(path, payload)
    print(f"Fetched {len(works):,} OpenAlex journal records")
    return payload


def attach_openalex(records: list[dict[str, Any]], payload: dict[str, Any]) -> list[dict[str, Any]]:
    by_doi = {
        normalize_doi(work.get("doi")): work
        for work in payload["items"]
        if normalize_doi(work.get("doi"))
    }
    for record in records:
        work = by_doi.get(record.get("doi"))
        crossref_candidates = crossref_pdf_candidates(record)
        if not work:
            candidates = crossref_candidates
            record.update(
                {
                    "openalex_id": None,
                    "oa_status": None,
                    "is_oa": None,
                    "pdf_candidates": candidates,
                    "download_eligible": False,
                    "selected_pdf_url": None,
                    "selected_pdf_license": None,
                    "selected_pdf_version": None,
                    "selected_pdf_source": None,
                }
            )
            continue
        candidates = openalex_pdf_candidates(work)
        seen_urls = {candidate["pdf_url"] for candidate in candidates}
        candidates.extend(
            candidate for candidate in crossref_candidates if candidate["pdf_url"] not in seen_urls
        )
        candidates = deduplicate_pdf_candidates(candidates)
        eligible = [
            candidate
            for candidate in candidates
            if candidate["has_explicit_open_license"]
            and not urlparse(candidate["pdf_url"]).netloc.lower().endswith("acs.org")
        ]
        selected = eligible[0] if eligible else None
        open_access = work.get("open_access") or {}
        record.update(
            {
                "openalex_id": work.get("id"),
                "oa_status": open_access.get("oa_status"),
                "is_oa": open_access.get("is_oa"),
                "pdf_candidates": candidates,
                "download_eligible": bool(eligible),
                "selected_pdf_url": selected.get("pdf_url") if selected else None,
                "selected_pdf_license": selected.get("license") if selected else None,
                "selected_pdf_version": selected.get("version") if selected else None,
                "selected_pdf_source": selected.get("source_name") if selected else None,
            }
        )
    return records
