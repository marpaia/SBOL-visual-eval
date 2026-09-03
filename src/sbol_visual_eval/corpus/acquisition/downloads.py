"""Policy-gated download of article PDFs from discovered candidate locations."""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..config import USER_AGENT
from ..layout import Layout
from ..metadata.candidates import (
    candidate_allowed,
    is_publisher_host,
    is_supplementary_artifact_url,
)
from ..provenance import pdf
from ..util.net import request_with_retry
from ..util.storage import read_jsonl, sha256_file, utc_now, write_csv, write_json
from ..util.text import normalize_doi


def _discover_pdf_links(payload: bytes, base_url: str) -> list[str]:
    soup = BeautifulSoup(payload, "html.parser")
    ranked: list[tuple[int, str]] = []
    for meta in soup.select("meta[content]"):
        name = (meta.get("name") or meta.get("property") or "").casefold()
        content = str(meta.get("content") or "").strip()
        if content and ("citation_pdf_url" in name or content.casefold().endswith(".pdf")):
            ranked.append((0, urljoin(base_url, content)))
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        absolute = urljoin(base_url, href)
        path = urlparse(absolute).path.casefold()
        text_value = anchor.get_text(" ", strip=True).casefold()
        if path.endswith(".pdf") or "/pdf/" in path:
            ranked.append((1, absolute))
        elif "pdf" in text_value and ("download" in path or "bitstream" in path):
            ranked.append((2, absolute))
    output: list[str] = []
    seen: set[str] = set()
    for _, url in sorted(ranked):
        if url != base_url and url not in seen:
            seen.add(url)
            output.append(url)
    return output[:5]


def _request_pdf_with_repository_accept_fallback(
    client: httpx.Client,
    url: str,
    candidate: dict[str, Any],
) -> tuple[httpx.Response, dict[str, Any] | None]:
    """Retry repository endpoints that reject PDF-specific content negotiation.

    Some EPrints installations return a false 404 when sent
    ``Accept: application/pdf`` even though the same URL returns the PDF under the
    client's default ``Accept`` header. Keep the PDF-specific request as the normal
    path and make one exact-URL fallback only for repository candidates whose first
    response is a 404 or a successful non-PDF response.
    """

    response = request_with_retry(
        client,
        "GET",
        url,
        attempts=3,
        follow_redirects=True,
        timeout=httpx.Timeout(30, connect=10),
        headers={"Accept": "application/pdf"},
    )
    if str(candidate.get("source_type") or "").casefold() != "repository":
        return response, None

    status_code = int(getattr(response, "status_code", 200))
    if status_code == 404:
        reason = "status_404"
    elif 200 <= status_code < 300 and b"%PDF" not in response.content[:1024]:
        reason = "non_pdf_response"
    else:
        return response, None

    initial_headers = getattr(response, "headers", {})
    provenance = {
        "initial_content_type": initial_headers.get("content-type", ""),
        "initial_explicit_accept_header": "application/pdf",
        "initial_resolved_url": str(response.url),
        "initial_status_code": status_code,
        "reason": reason,
        "retry_omitted_explicit_accept_header": True,
        "url": url,
    }
    retry_response = request_with_retry(
        client,
        "GET",
        url,
        attempts=3,
        follow_redirects=True,
        timeout=httpx.Timeout(30, connect=10),
    )
    return retry_response, provenance


def _download_one_paper(
    client: httpx.Client,
    layout: Layout,
    record: dict[str, Any],
    *,
    allow_license_unknown: bool,
    include_publisher: bool,
    force: bool,
) -> dict[str, Any]:
    destination_dir = layout.paper_directory(record)
    destination = destination_dir / "paper.pdf"
    metadata_path = destination_dir / "metadata.json"
    base_report = {
        "record_id": record["record_id"],
        "doi": record.get("doi"),
        "year": record["year"],
        "title": record.get("title_crossref") or record["title_source"],
        "local_path": str(destination.relative_to(layout.root)),
    }
    preexisting_attempts: list[dict[str, Any]] = []
    existing_metadata: dict[str, Any] | None = None
    existing_verification: dict[str, Any] | None = None
    seek_version_of_record = False

    if destination.exists() and metadata_path.exists() and not force:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("sha256") == sha256_file(destination):
                verification = pdf.verify_pdf(destination, record)
                existing_metadata = metadata
                existing_verification = verification
                seek_version_of_record = (
                    include_publisher
                    and metadata.get("source_version") != "publishedVersion"
                    and any(
                        candidate.get("version") == "publishedVersion"
                        and candidate_allowed(
                            candidate,
                            allow_license_unknown=allow_license_unknown,
                            include_publisher=include_publisher,
                        )
                        for candidate in record.get("pdf_candidates", [])
                    )
                )
                if not seek_version_of_record:
                    return {
                        **base_report,
                        **metadata,
                        **verification,
                        "status": "already_present",
                    }
        except Exception as error:  # noqa: BLE001 - quarantine invalid prior artifact
            rejected_dir = destination_dir / "rejected"
            rejected_dir.mkdir(parents=True, exist_ok=True)
            suffix = sha256_file(destination)[:12] if destination.exists() else "unknown"
            if destination.exists():
                destination.rename(rejected_dir / f"paper__{suffix}.pdf")
            if metadata_path.exists():
                metadata_path.rename(rejected_dir / f"metadata__{suffix}.json")
            preexisting_attempts.append(
                {
                    "url": metadata.get("source_url") if "metadata" in locals() else None,
                    "error": f"Rejected existing artifact: {type(error).__name__}: {error}",
                }
            )
    if destination.exists() and not force and existing_metadata is None:
        try:
            verification = pdf.verify_pdf(destination, record)
        except Exception as error:  # noqa: BLE001 - report, but do not mutate another source
            return {
                **base_report,
                "status": "existing_other_source_failed_validation",
                "bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
                "attempts": [
                    {"error": f"Existing other-source PDF: {type(error).__name__}: {error}"}
                ],
            }
        return {
            **base_report,
            **verification,
            "status": "already_present_other_source",
            "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "attempts": [],
        }

    candidates = [
        candidate
        for candidate in record.get("pdf_candidates", [])
        if candidate_allowed(
            candidate,
            allow_license_unknown=allow_license_unknown,
            include_publisher=include_publisher,
        )
    ]
    if seek_version_of_record:
        candidates = [
            candidate for candidate in candidates if candidate.get("version") == "publishedVersion"
        ]
    elif include_publisher:
        candidates.sort(
            key=lambda candidate: (
                candidate.get("version") != "publishedVersion",
                not is_publisher_host(candidate["pdf_url"]),
                candidate["pdf_url"],
            )
        )
    if not candidates:
        return {**base_report, "status": "no_eligible_candidate", "attempts": []}

    attempts: list[dict[str, Any]] = preexisting_attempts
    for candidate in candidates:
        attempt = {
            "url": candidate["pdf_url"],
            "license": candidate.get("license"),
            "version": candidate.get("version"),
            "source_name": candidate.get("source_name"),
        }
        temp_path: Path | None = None
        try:
            if is_supplementary_artifact_url(candidate["pdf_url"]):
                raise ValueError("candidate URL identifies a supplementary-information file")
            response, request_retry = _request_pdf_with_repository_accept_fallback(
                client,
                candidate["pdf_url"],
                candidate,
            )
            repository_request_retries = [request_retry] if request_retry else []
            if repository_request_retries:
                attempt["repository_request_retries"] = repository_request_retries
            response.raise_for_status()
            discovered_from = None
            if b"%PDF" not in response.content[:1024]:
                discovered_urls = _discover_pdf_links(response.content, str(response.url))
                discovered_urls = [
                    url for url in discovered_urls if not is_supplementary_artifact_url(url)
                ]
                attempt["discovered_pdf_urls"] = discovered_urls
                for discovered_url in discovered_urls:
                    discovered_response, discovered_retry = (
                        _request_pdf_with_repository_accept_fallback(
                            client,
                            discovered_url,
                            candidate,
                        )
                    )
                    if discovered_retry:
                        repository_request_retries.append(discovered_retry)
                        attempt["repository_request_retries"] = repository_request_retries
                    if (
                        discovered_response.is_success
                        and b"%PDF" in discovered_response.content[:1024]
                    ):
                        discovered_from = candidate["pdf_url"]
                        response = discovered_response
                        break
            if len(response.content) > 120 * 1024 * 1024:
                raise ValueError("PDF exceeds the 120 MiB safety limit")
            destination_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=destination_dir, suffix=".pdf.part", delete=False
            ) as handle:
                handle.write(response.content)
                temp_path = Path(handle.name)
            verification = pdf.verify_pdf(temp_path, record)
            preserved_prior_variant = None
            if destination.exists() and existing_metadata is not None:
                prior_hash = existing_metadata["sha256"]
                version = existing_metadata.get("source_version") or "unknown_version"
                variant_dir = (
                    destination_dir / "versions" / re.sub(r"[^A-Za-z0-9._-]+", "_", version)
                )
                variant_dir.mkdir(parents=True, exist_ok=True)
                prior_destination = variant_dir / f"paper__{prior_hash[:12]}.pdf"
                prior_metadata = variant_dir / f"metadata__{prior_hash[:12]}.json"
                destination.rename(prior_destination)
                metadata_path.rename(prior_metadata)
                archived_metadata = {
                    **existing_metadata,
                    "local_path": str(prior_destination.relative_to(layout.root)),
                }
                write_json(prior_metadata, archived_metadata)
                preserved_prior_variant = str(prior_destination.relative_to(layout.root))
            temp_path.replace(destination)
            source_version = candidate.get("version")
            metadata = {
                "schema_version": 1,
                "status": "downloaded",
                "retrieved_at": utc_now(),
                "record_id": record["record_id"],
                "doi": normalize_doi(record.get("doi")),
                "year": record["year"],
                "title": record.get("title_crossref") or record["title_source"],
                "local_path": str(destination.relative_to(layout.root)),
                "source_url": candidate["pdf_url"],
                "discovered_from_url": discovered_from,
                "resolved_url": str(response.url),
                "source_landing_page_url": candidate.get("landing_page_url"),
                "source_name": candidate.get("source_name"),
                "source_type": candidate.get("source_type"),
                "source_version": source_version,
                "source_license": candidate.get("license"),
                "source_metadata_source": candidate.get("metadata_source"),
                "source_intended_application": candidate.get("intended_application"),
                "repository_request_retries": repository_request_retries,
                "license_metadata_source": (
                    "OpenAlex location metadata"
                    if candidate.get("metadata_source") == "OpenAlex"
                    else None
                ),
                "bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
                "version_assertion_method": (
                    "upstream_metadata"
                    if isinstance(source_version, str) and source_version.strip()
                    else "unknown"
                ),
                "historical_evaluated_edition_relation": "not_established",
                "preserved_prior_variant": preserved_prior_variant,
                **verification,
            }
            write_json(metadata_path, metadata)
            return {**base_report, **metadata, "attempts": attempts}
        except Exception as error:  # noqa: BLE001 - isolate failures to one candidate
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            attempt["error"] = f"{type(error).__name__}: {error}"
            attempts.append(attempt)

    if existing_metadata is not None and destination.exists():
        return {
            **base_report,
            **existing_metadata,
            **(existing_verification or {}),
            "status": "already_present_no_vor_upgrade",
            "attempts": attempts,
        }
    if destination_dir.exists() and not any(destination_dir.iterdir()):
        destination_dir.rmdir()
    return {**base_report, "status": "failed_all_candidates", "attempts": attempts}


DOWNLOAD_REPORT_FIELDS = (
    "record_id",
    "doi",
    "year",
    "title",
    "status",
    "local_path",
    "retrieved_at",
    "source_url",
    "discovered_from_url",
    "resolved_url",
    "repository_request_retries_json",
    "source_landing_page_url",
    "source_name",
    "source_type",
    "source_version",
    "source_license",
    "source_metadata_source",
    "source_intended_application",
    "license_metadata_source",
    "bytes",
    "sha256",
    "preserved_prior_variant",
    "page_count",
    "artifact_type",
    "identity_check",
    "title_similarity",
    "attempts_json",
)


def download_papers(
    layout: Layout,
    *,
    allow_license_unknown: bool = False,
    include_publisher: bool = False,
    workers: int = 6,
    limit: int | None = None,
    force: bool = False,
    report_stem: str = "downloads",
) -> list[dict[str, Any]]:
    source_path = layout.processed / "papers.jsonl"
    if not source_path.exists():
        raise FileNotFoundError("processed manifest is missing; run `sbol-visual-data build` first")
    records = read_jsonl(source_path)
    if limit is not None:
        eligible_records = [
            record
            for record in records
            if any(
                candidate_allowed(
                    candidate,
                    allow_license_unknown=allow_license_unknown,
                    include_publisher=include_publisher,
                )
                for candidate in record.get("pdf_candidates", [])
            )
        ]
        records = eligible_records[:limit]

    results: list[dict[str, Any]] = []
    limits = httpx.Limits(max_connections=max(workers * 2, 10), max_keepalive_connections=workers)
    with (
        httpx.Client(headers={"User-Agent": USER_AGENT}, limits=limits) as client,
        ThreadPoolExecutor(max_workers=workers) as executor,
    ):
        futures = {
            executor.submit(
                _download_one_paper,
                client,
                layout,
                record,
                allow_license_unknown=allow_license_unknown,
                include_publisher=include_publisher,
                force=force,
            ): record
            for record in records
        }
        for index, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if index % 25 == 0 or index == len(futures):
                counts = Counter(result["status"] for result in results)
                print(
                    f"Paper acquisition {index:,}/{len(futures):,}: {dict(counts)}",
                    flush=True,
                )

    results.sort(key=lambda row: (row["year"], row.get("doi") or row["record_id"]))
    report_rows = []
    for result in results:
        flat = dict(result)
        flat["repository_request_retries_json"] = json.dumps(
            result.get("repository_request_retries", []), ensure_ascii=False
        )
        flat["attempts_json"] = json.dumps(result.get("attempts", []), ensure_ascii=False)
        report_rows.append(flat)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", report_stem):
        raise ValueError("report_stem may contain only letters, digits, dot, underscore, and dash")
    write_csv(layout.reports / f"{report_stem}.csv", report_rows, DOWNLOAD_REPORT_FIELDS)
    write_json(
        layout.reports / f"{report_stem}_summary.json",
        {
            "generated_at": utc_now(),
            "policy": {
                "allow_license_unknown": allow_license_unknown,
                "include_publisher": include_publisher,
                "license_note": (
                    "OpenAlex metadata is for discovery; verify original terms before reuse."
                ),
            },
            "papers_considered": len(results),
            "status_counts": dict(Counter(result["status"] for result in results)),
            "downloaded_or_present": sum(
                result["status"]
                in {
                    "downloaded",
                    "already_present",
                    "already_present_no_vor_upgrade",
                    "already_present_other_source",
                }
                for result in results
            ),
        },
    )
    return results
