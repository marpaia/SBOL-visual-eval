"""Per-paper package acquisition, manifest writing, and the acquisition report."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx

from ...layout import Layout
from ...util.storage import (
    atomic_write_bytes,
    canonical_json_sha256,
    read_jsonl,
    utc_now,
    write_csv,
    write_json,
)
from ...util.text import normalize_doi
from .ids import ID_CONVERTER_BATCH_SIZE, ID_CONVERTER_URL, _resolve_dois, _version_number
from .opendata import (
    PMC_AUTHOR_MANUSCRIPT_DOCUMENTATION,
    PMC_BUCKET,
    PMC_DATASET_DOCUMENTATION,
    PMC_DATASET_README,
    _article_pdf_destination,
    _discover_latest_version,
    _download_asset,
    _metadata_url,
    _s3_https_url,
)
from .transport import _request_with_retry

DEFAULT_WORKERS = 6

PMC_REPORT_FIELDS = (
    "record_id",
    "doi",
    "year",
    "title",
    "status",
    "pmcid",
    "versioned_pmcid",
    "pmc_version",
    "pmid",
    "is_pmc_openaccess",
    "is_manuscript",
    "is_retracted",
    "source_version",
    "license_code",
    "package_metadata_url",
    "package_metadata_sha256",
    "pdf_available",
    "xml_available",
    "text_available",
    "media_available_count",
    "image_media_available_count",
    "artifact_downloaded_count",
    "artifact_already_present_count",
    "artifact_failed_count",
    "pdf_local_path",
    "xml_local_path",
    "text_local_path",
    "media_local_paths_json",
    "artifact_checksums_json",
    "xml_url",
    "text_url",
    "limitation",
    "errors_json",
    "manifest_path",
    "retrieved_at",
)


def _base_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record["record_id"],
        "doi": record.get("doi"),
        "year": record["year"],
        "title": record.get("title_crossref") or record["title_source"],
    }


def _source_version(metadata: dict[str, Any]) -> str:
    return "author_manuscript" if metadata.get("is_manuscript") else "published_version"


def _acquire_one(
    client: httpx.Client,
    layout: Layout,
    record: dict[str, Any],
    resolution: dict[str, Any],
) -> dict[str, Any]:
    base = _base_result(record)
    pmcid = resolution["pmcid"]
    versioned_pmcid = resolution.get("versioned_pmcid")
    errors: list[str] = []
    try:
        if not versioned_pmcid:
            versioned_pmcid = _discover_latest_version(client, pmcid)
        package_url = _metadata_url(versioned_pmcid)
        response = _request_with_retry(
            client, "GET", package_url, follow_redirects=True, timeout=90
        )
        response.raise_for_status()
        package_bytes = response.content
        metadata = response.json()
        if not isinstance(metadata, dict):
            raise TypeError("PMC package metadata is not a JSON object")
    except (httpx.HTTPError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        return {
            **base,
            "status": "pmc_metadata_failed",
            "pmcid": pmcid,
            "versioned_pmcid": versioned_pmcid,
            "pmid": resolution.get("pmid"),
            "errors": [f"{type(error).__name__}: {error}"],
        }

    metadata_pmcid = str(metadata.get("pmcid", "")).upper()
    metadata_doi = normalize_doi(metadata.get("doi"))
    expected_doi = normalize_doi(record.get("doi"))
    if metadata_pmcid != pmcid or (metadata_doi and expected_doi and metadata_doi != expected_doi):
        return {
            **base,
            "status": "pmc_metadata_identity_mismatch",
            "pmcid": pmcid,
            "versioned_pmcid": versioned_pmcid,
            "pmid": resolution.get("pmid"),
            "package_metadata_url": package_url,
            "errors": [
                "PMC package metadata did not match the DOI/PMCID returned by the ID Converter"
            ],
        }

    paper_dir = layout.paper_directory(record)
    paper_dir.mkdir(parents=True, exist_ok=True)
    package_metadata_path = paper_dir / "pmc" / "package_metadata.json"
    atomic_write_bytes(package_metadata_path, package_bytes)
    manifest_path = paper_dir / "pmc.json"
    artifacts: list[dict[str, Any]] = []

    pdf_url = metadata.get("pdf_url")
    if pdf_url:
        destination = _article_pdf_destination(paper_dir, str(pdf_url))
        artifacts.append(
            _download_asset(
                client,
                source_url=str(pdf_url),
                destination=destination,
                role="article_pdf",
            )
        )

    for role, field in (("article_xml", "xml_url"), ("article_text", "text_url")):
        source_url = metadata.get(field)
        if not source_url:
            continue
        try:
            _, _, filename = _s3_https_url(str(source_url))
            artifacts.append(
                _download_asset(
                    client,
                    source_url=str(source_url),
                    destination=paper_dir / "pmc" / filename,
                    role=role,
                )
            )
        except ValueError as error:
            artifacts.append(
                {
                    "role": role,
                    "status": "failed",
                    "source_url": source_url,
                    "error": f"ValueError: {error}",
                    "is_image_asset": False,
                }
            )

    used_media_paths: set[Path] = set()
    for index, media_url in enumerate(metadata.get("media_urls") or [], start=1):
        try:
            _, _, filename = _s3_https_url(str(media_url))
            destination = paper_dir / "media" / filename
            if destination in used_media_paths:
                stem, suffix = destination.stem, destination.suffix
                destination = destination.with_name(f"{stem}__{index}{suffix}")
            used_media_paths.add(destination)
            artifacts.append(
                _download_asset(
                    client,
                    source_url=str(media_url),
                    destination=destination,
                    role="media",
                )
            )
        except ValueError as error:
            artifacts.append(
                {
                    "role": "media",
                    "status": "failed",
                    "source_url": media_url,
                    "error": f"ValueError: {error}",
                    "is_image_asset": False,
                }
            )

    for artifact in artifacts:
        if artifact.get("local_path"):
            artifact["local_path"] = str(Path(artifact["local_path"]).relative_to(layout.root))

    failed = [artifact for artifact in artifacts if artifact["status"] == "failed"]
    successful = [artifact for artifact in artifacts if artifact["status"] != "failed"]
    downloaded = [artifact for artifact in artifacts if artifact["status"] == "downloaded"]
    visual_artifacts = [
        artifact for artifact in artifacts if artifact["role"] in {"article_pdf", "media"}
    ]
    text_artifacts = [
        artifact for artifact in artifacts if artifact["role"] in {"article_xml", "article_text"}
    ]
    limitation = None
    if (
        metadata.get("is_manuscript")
        and not metadata.get("is_pmc_openaccess")
        and not visual_artifacts
    ):
        if text_artifacts and all(artifact["status"] != "failed" for artifact in text_artifacts):
            status = (
                "author_manuscript_text_downloaded"
                if any(artifact["status"] == "downloaded" for artifact in text_artifacts)
                else "author_manuscript_text_already_present"
            )
        else:
            status = "author_manuscript_text_download_failed"
        limitation = (
            "PMC does not distribute PDF, media, or supplementary files for this non-open "
            "author manuscript; the available XML and plain text were acquired instead."
        )
    elif not artifacts:
        status = "no_pdf_or_media"
    elif failed and successful:
        status = "partial_download"
    elif failed:
        status = "download_failed"
    elif downloaded:
        status = "downloaded"
    else:
        status = "already_present"

    errors.extend(str(artifact["error"]) for artifact in failed)
    retrieved_at = utc_now()
    version_number = metadata.get("version") or _version_number(versioned_pmcid)
    manifest = {
        "schema_version": 2,
        "status": status,
        "retrieved_at": retrieved_at,
        "record_id": record["record_id"],
        "doi": record.get("doi"),
        "pmcid": pmcid,
        "versioned_pmcid": versioned_pmcid,
        "pmc_version": version_number,
        "pmid": metadata.get("pmid") or resolution.get("pmid"),
        "source_name": "NIH NLM PubMed Central Article Datasets",
        "source_version": _source_version(metadata),
        "source_documentation_url": PMC_DATASET_DOCUMENTATION,
        "package_metadata_url": package_url,
        "package_metadata_sha256": hashlib.sha256(package_bytes).hexdigest(),
        "package_metadata_sha256_scope": "raw_http_response_bytes",
        "package_metadata_local_path": str(package_metadata_path.relative_to(layout.root)),
        "package_metadata_canonical_sha256": canonical_json_sha256(metadata),
        "package_metadata_canonicalization": "json_utf8_sorted_keys_compact_v1",
        "license_code": metadata.get("license_code"),
        "license_metadata_source": "PMC AWS package metadata",
        "is_pmc_openaccess": metadata.get("is_pmc_openaccess"),
        "is_manuscript": metadata.get("is_manuscript"),
        "is_retracted": metadata.get("is_retracted"),
        "limitation": limitation,
        "package_metadata": metadata,
        "artifacts": artifacts,
        "errors": errors,
    }
    write_json(manifest_path, manifest)

    return {
        **base,
        "status": status,
        "pmcid": pmcid,
        "versioned_pmcid": versioned_pmcid,
        "pmc_version": version_number,
        "pmid": manifest["pmid"],
        "is_pmc_openaccess": metadata.get("is_pmc_openaccess"),
        "is_manuscript": metadata.get("is_manuscript"),
        "is_retracted": metadata.get("is_retracted"),
        "source_version": manifest["source_version"],
        "license_code": metadata.get("license_code"),
        "package_metadata_url": package_url,
        "package_metadata_sha256": manifest["package_metadata_sha256"],
        "pdf_available": bool(pdf_url),
        "xml_available": bool(metadata.get("xml_url")),
        "text_available": bool(metadata.get("text_url")),
        "media_available_count": len(metadata.get("media_urls") or []),
        "image_media_available_count": sum(
            bool(artifact.get("is_image_asset"))
            for artifact in artifacts
            if artifact["role"] == "media"
        ),
        "artifact_downloaded_count": len(downloaded),
        "artifact_already_present_count": sum(
            artifact["status"] == "already_present" for artifact in artifacts
        ),
        "artifact_failed_count": len(failed),
        "pdf_local_path": next(
            (
                artifact.get("local_path")
                for artifact in artifacts
                if artifact["role"] == "article_pdf" and artifact["status"] != "failed"
            ),
            None,
        ),
        "xml_local_path": next(
            (
                artifact.get("local_path")
                for artifact in artifacts
                if artifact["role"] == "article_xml" and artifact["status"] != "failed"
            ),
            None,
        ),
        "text_local_path": next(
            (
                artifact.get("local_path")
                for artifact in artifacts
                if artifact["role"] == "article_text" and artifact["status"] != "failed"
            ),
            None,
        ),
        "media_local_paths": [
            artifact.get("local_path")
            for artifact in artifacts
            if artifact["role"] == "media" and artifact["status"] != "failed"
        ],
        "artifact_checksums": [
            {
                "local_path": artifact.get("local_path"),
                "md5": artifact.get("md5"),
                "sha256": artifact.get("sha256"),
            }
            for artifact in successful
        ],
        "xml_url": metadata.get("xml_url"),
        "text_url": metadata.get("text_url"),
        "limitation": limitation,
        "errors": errors,
        "manifest_path": str(manifest_path.relative_to(layout.root)),
        "retrieved_at": retrieved_at,
        "artifacts": artifacts,
    }


def _csv_rows(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        row = dict(result)
        row["media_local_paths_json"] = json.dumps(
            result.get("media_local_paths", []), ensure_ascii=False
        )
        row["artifact_checksums_json"] = json.dumps(
            result.get("artifact_checksums", []), ensure_ascii=False, sort_keys=True
        )
        row["errors_json"] = json.dumps(result.get("errors", []), ensure_ascii=False)
        rows.append(row)
    return rows


def acquire_pmc(layout: Layout, workers: int = DEFAULT_WORKERS) -> list[dict[str, Any]]:
    """Acquire the current official PMC package for every DOI in ``papers.jsonl``.

    DOI resolution uses the NCBI ID Converter in batches of at most 200. Package
    metadata and downloadable objects come only from the official PMC Article Datasets
    S3 bucket. Non-open author manuscripts remain represented in the report and their
    per-paper ``pmc.json``, even though PMC does not distribute their PDF/media objects.
    """

    if workers < 1:
        raise ValueError("workers must be at least 1")
    source_path = layout.processed / "papers.jsonl"
    if not source_path.exists():
        raise FileNotFoundError("processed manifest is missing; run `sbol-visual-data build` first")
    records = read_jsonl(source_path)
    dois = sorted(
        {
            normalized
            for record in records
            if (normalized := normalize_doi(record.get("doi"))) is not None
        }
    )

    email = os.environ.get("NCBI_EMAIL")
    user_agent = "SBOL-visual-eval/0.1 (PMC corpus acquisition)"
    if email:
        user_agent = f"SBOL-visual-eval/0.1 (PMC corpus acquisition; {email})"
    limits = httpx.Limits(
        max_connections=max(workers * 2, 10), max_keepalive_connections=max(workers, 5)
    )
    with httpx.Client(headers={"User-Agent": user_agent}, limits=limits) as client:
        resolutions, response_dates = _resolve_dois(client, dois)

        results: list[dict[str, Any]] = []
        futures: dict[Any, tuple[dict[str, Any], dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for record in records:
                doi = normalize_doi(record.get("doi"))
                if not doi:
                    results.append(
                        {
                            **_base_result(record),
                            "status": "missing_doi",
                            "errors": ["paper record has no DOI"],
                        }
                    )
                    continue
                resolution = resolutions.get(
                    doi,
                    {
                        "status": "id_converter_missing_response",
                        "error": "no ID Converter result was recorded for this DOI",
                    },
                )
                if resolution["status"] != "resolved":
                    results.append(
                        {
                            **_base_result(record),
                            "status": resolution["status"],
                            "errors": [resolution.get("error")],
                        }
                    )
                    continue
                future = executor.submit(_acquire_one, client, layout, record, resolution)
                futures[future] = (record, resolution)

            for completed, future in enumerate(as_completed(futures), start=1):
                record, resolution = futures[future]
                try:
                    result = future.result()
                except Exception as error:  # noqa: BLE001 - preserve progress across papers
                    result = {
                        **_base_result(record),
                        "status": "internal_error",
                        "pmcid": resolution.get("pmcid"),
                        "versioned_pmcid": resolution.get("versioned_pmcid"),
                        "pmid": resolution.get("pmid"),
                        "errors": [f"{type(error).__name__}: {error}"],
                    }
                results.append(result)
                if completed % 25 == 0 or completed == len(futures):
                    counts = Counter(result["status"] for result in results)
                    print(
                        f"PMC acquisition {completed:,}/{len(futures):,} PMC matches: {dict(counts)}"
                    )

    results.sort(key=lambda result: (result["year"], result.get("doi") or result["record_id"]))
    layout.reports.mkdir(parents=True, exist_ok=True)
    report_path = layout.reports / "pmc_acquisition.csv"
    summary_path = layout.reports / "pmc_acquisition.json"
    write_csv(report_path, _csv_rows(results), PMC_REPORT_FIELDS)

    status_counts = Counter(result["status"] for result in results)
    artifact_status_counts = Counter(
        artifact["status"] for result in results for artifact in result.get("artifacts", [])
    )
    summary = {
        "generated_at": utc_now(),
        "sources": {
            "id_converter_url": ID_CONVERTER_URL,
            "pmc_dataset_documentation": PMC_DATASET_DOCUMENTATION,
            "pmc_dataset_readme": PMC_DATASET_README,
            "pmc_bucket": f"s3://{PMC_BUCKET}",
            "author_manuscript_documentation": PMC_AUTHOR_MANUSCRIPT_DOCUMENTATION,
        },
        "id_converter": {
            "requested_unique_dois": len(dois),
            "batch_size": ID_CONVERTER_BATCH_SIZE,
            "batches": (len(dois) + ID_CONVERTER_BATCH_SIZE - 1) // ID_CONVERTER_BATCH_SIZE,
            "response_dates": response_dates,
            "ncbi_email_supplied": bool(email),
        },
        "papers_considered": len(records),
        "status_counts": dict(sorted(status_counts.items())),
        "pmc_matches": sum(bool(result.get("pmcid")) for result in results),
        "packages_with_pdf": sum(bool(result.get("pdf_available")) for result in results),
        "packages_with_xml": sum(bool(result.get("xml_available")) for result in results),
        "packages_with_text": sum(bool(result.get("text_available")) for result in results),
        "packages_with_media": sum(
            int(result.get("media_available_count", 0)) > 0 for result in results
        ),
        "media_assets_listed": sum(
            int(result.get("media_available_count", 0)) for result in results
        ),
        "image_media_assets_listed": sum(
            int(result.get("image_media_available_count", 0)) for result in results
        ),
        "artifact_status_counts": dict(sorted(artifact_status_counts.items())),
        "artifact_bytes_downloaded_this_run": sum(
            int(artifact.get("bytes", 0))
            for result in results
            for artifact in result.get("artifacts", [])
            if artifact["status"] == "downloaded"
        ),
        "non_open_author_manuscripts_text_only": sum(
            count
            for status, count in status_counts.items()
            if status.startswith("author_manuscript_text_")
        ),
        "limitations": [
            (
                "PMC omits PDF, media, and supplementary objects for non-open author "
                "manuscripts; their available XML and plain-text objects are retained instead."
            ),
            (
                "PMC media_urls can include supplementary files as well as article images; "
                "is_image_asset identifies image files but does not assign historical figure IDs."
            ),
            "License codes are copied from current PMC package metadata and are not legal advice.",
        ],
        "report_path": str(report_path.relative_to(layout.root)),
    }
    write_json(summary_path, summary)
    print(
        f"PMC acquisition finished: {summary['pmc_matches']:,}/{len(records):,} papers matched; "
        f"{summary['packages_with_pdf']:,} packages expose a PDF and "
        f"{summary['media_assets_listed']:,} media objects were listed"
    )
    return results
