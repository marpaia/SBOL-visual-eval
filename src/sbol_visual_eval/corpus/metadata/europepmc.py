"""Europe PMC core metadata: retrieval and external full-text candidate attachment."""

from __future__ import annotations

from typing import Any

import httpx

from ..config import USER_AGENT
from ..layout import Layout
from ..util.net import request_with_retry
from ..util.storage import utc_now, write_json
from ..util.text import normalize_doi
from .caching import _cache_payload
from .candidates import deduplicate_pdf_candidates, europepmc_pdf_candidates

EUROPE_PMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_QUERY = "ISSN:2161-5063 AND FIRST_PDATE:[2012-01-01 TO 2023-12-31]"


def fetch_europepmc(
    layout: Layout, *, offline: bool = False, refresh: bool = False
) -> dict[str, Any]:
    """Fetch core journal metadata, including Europe PMC's external full-text links."""

    path = layout.cache / "europepmc_acs_synthetic_biology_2012_2023.json"
    cached = _cache_payload(path, offline=offline, refresh=refresh)
    if cached is not None:
        return cached

    params: dict[str, Any] = {
        "query": EUROPE_PMC_QUERY,
        "format": "json",
        "resultType": "core",
        "pageSize": 1000,
        "cursorMark": "*",
    }
    items: list[dict[str, Any]] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        while True:
            response = request_with_retry(
                client,
                "GET",
                EUROPE_PMC_URL,
                params=params,
                timeout=120,
                follow_redirects=True,
            )
            response.raise_for_status()
            response_payload = response.json()
            batch = response_payload.get("resultList", {}).get("result", [])
            items.extend(batch)
            next_cursor = response_payload.get("nextCursorMark")
            if not batch or not next_cursor or next_cursor == params["cursorMark"]:
                break
            params["cursorMark"] = next_cursor

    payload = {
        "retrieved_at": utc_now(),
        "request_url": EUROPE_PMC_URL,
        "request_filter": EUROPE_PMC_QUERY,
        "journal_issn": "2161-5063",
        "result_type": "core",
        "items": items,
    }
    write_json(path, payload)
    print(f"Fetched {len(items):,} Europe PMC journal records")
    return payload


def attach_europepmc(
    records: list[dict[str, Any]], payload: dict[str, Any]
) -> list[dict[str, Any]]:
    by_doi = {
        normalize_doi(work.get("doi")): work
        for work in payload["items"]
        if normalize_doi(work.get("doi"))
    }
    for record in records:
        work = by_doi.get(record.get("doi"))
        if not work:
            record.update(
                {
                    "europepmc_id": None,
                    "europepmc_pmcid": None,
                    "europepmc_in_pmc": None,
                    "europepmc_in_epmc": None,
                    "europepmc_author_manuscript": None,
                    "europepmc_is_open_access": None,
                    "europepmc_external_pdf_candidates": 0,
                }
            )
            continue

        external_candidates = europepmc_pdf_candidates(work)
        record["pdf_candidates"] = deduplicate_pdf_candidates(
            [*record.get("pdf_candidates", []), *external_candidates]
        )
        record.update(
            {
                "europepmc_id": (
                    f"{work.get('source')}:{work.get('id')}"
                    if work.get("source") and work.get("id")
                    else work.get("id")
                ),
                "europepmc_pmcid": work.get("pmcid"),
                "europepmc_in_pmc": work.get("inPMC") == "Y",
                "europepmc_in_epmc": work.get("inEPMC") == "Y",
                "europepmc_author_manuscript": work.get("epmcAuthMan") == "Y",
                "europepmc_is_open_access": work.get("isOpenAccess") == "Y",
                "europepmc_external_pdf_candidates": len(external_candidates),
            }
        )
    return records
