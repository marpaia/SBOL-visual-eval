"""DOI-to-PMCID resolution through the NCBI ID Converter."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator, Sequence
from typing import Any

import httpx

from ...util.text import normalize_doi
from .transport import _request_with_retry

ID_CONVERTER_URL = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
ID_CONVERTER_BATCH_SIZE = 200


def _batched(values: Sequence[str], size: int) -> Iterator[list[str]]:
    if size < 1:
        raise ValueError("batch size must be at least 1")
    for start in range(0, len(values), size):
        yield list(values[start : start + size])


def _version_number(versioned_pmcid: str) -> int | None:
    match = re.fullmatch(r"PMC\d+\.(\d+)", versioned_pmcid, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _select_current_version(converter_record: dict[str, Any]) -> str | None:
    versions = converter_record.get("versions") or []
    current = [version for version in versions if version.get("current")]
    candidates = current or versions
    ranked = sorted(
        (
            (number, str(version.get("pmcid", "")).upper())
            for version in candidates
            if (number := _version_number(str(version.get("pmcid", "")).upper())) is not None
        ),
        reverse=True,
    )
    return ranked[0][1] if ranked else None


def _resolve_dois(
    client: httpx.Client, dois: Sequence[str]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    resolved: dict[str, dict[str, Any]] = {}
    response_dates: list[str] = []
    email = os.environ.get("NCBI_EMAIL")
    api_key = os.environ.get("NCBI_API_KEY")

    for batch_index, batch in enumerate(_batched(dois, ID_CONVERTER_BATCH_SIZE)):
        params: dict[str, Any] = {
            "format": "json",
            "idtype": "doi",
            "ids": ",".join(batch),
            "tool": "sbol_visual_eval",
            "versions": "yes",
        }
        if email:
            params["email"] = email
        if api_key:
            params["api_key"] = api_key
        response = _request_with_retry(
            client,
            "GET",
            ID_CONVERTER_URL,
            params=params,
            follow_redirects=True,
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok":
            raise RuntimeError(f"PMC ID Converter returned status {payload.get('status')!r}")
        if payload.get("response-date"):
            response_dates.append(str(payload["response-date"]))

        seen: set[str] = set()
        for converter_record in payload.get("records", []):
            requested = normalize_doi(
                converter_record.get("requested-id") or converter_record.get("doi")
            )
            if not requested:
                continue
            seen.add(requested)
            if converter_record.get("status") == "error" or not converter_record.get("pmcid"):
                resolved[requested] = {
                    "status": "not_in_pmc",
                    "error": converter_record.get("errmsg") or "Identifier not found in PMC",
                }
                continue
            pmcid = str(converter_record["pmcid"]).split(".", 1)[0].upper()
            versioned_pmcid = _select_current_version(converter_record)
            resolved[requested] = {
                "status": "resolved",
                "pmcid": pmcid,
                "versioned_pmcid": versioned_pmcid,
                "pmid": converter_record.get("pmid"),
            }

        for requested in batch:
            if requested not in seen:
                resolved[requested] = {
                    "status": "id_converter_missing_response",
                    "error": "PMC ID Converter omitted the requested DOI",
                }

        # The ID Converter is an NCBI service. Keep anonymous traffic below three requests/s.
        if batch_index + 1 < (len(dois) + ID_CONVERTER_BATCH_SIZE - 1) // ID_CONVERTER_BATCH_SIZE:
            time.sleep(0.11 if api_key else 0.34)

    return resolved, response_dates
