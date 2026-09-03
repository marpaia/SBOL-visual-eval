from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from ..config import USER_AGENT
from ..layout import Layout
from ..provenance.biorxiv import (
    BIORXIV_PREPRINT_API_SOURCE,
    BIORXIV_PREPRINT_CANDIDATES,
    BIORXIV_PREPRINT_DIRECT_TRANSPORT,
    BIORXIV_PREPRINT_IMPORTED_LIMITATION,
    BIORXIV_PREPRINT_IMPORTED_TRANSPORT,
    BIORXIV_PREPRINT_MAX_API_BYTES,
    BIORXIV_PREPRINT_SOURCE_NAME,
    BIORXIV_PREPRINT_SOURCE_VERSION,
    BIORXIV_PREPRINT_VARIANT_DIRECTORY,
    biorxiv_api_url,
    biorxiv_preprint_manifest_identity_errors,
    parse_biorxiv_api_payload,
    verify_biorxiv_preprint_pdf,
)
from ..util.net import request_with_retry
from ..util.storage import atomic_write_bytes, read_jsonl, utc_now, write_csv, write_json
from ..util.text import normalize_doi

MAX_PDF_BYTES = 120 * 1024 * 1024
REPORT_FIELDS = (
    "record_id",
    "doi",
    "year",
    "title",
    "status",
    "preprint_doi",
    "preprint_title",
    "preprint_version",
    "biorxiv_published_doi",
    "source_url",
    "resolved_url",
    "biorxiv_api_url",
    "biorxiv_api_snapshot_path",
    "local_path",
    "transport_provenance_verification_status",
    "bytes",
    "sha256",
    "page_count",
    "identity_check",
    "title_similarity",
    "error",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_bounded(path: Path, maximum: int, label: str) -> bytes:
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise ValueError(f"{label} has an invalid byte size")
    with path.open("rb") as handle:
        payload = handle.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds its safety limit")
    return payload


def _verify_pdf_payload(payload: bytes, api_row: dict[str, Any]) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(payload)
    try:
        return verify_biorxiv_preprint_pdf(temp_path, api_row)
    finally:
        temp_path.unlink(missing_ok=True)


def _download(
    client: httpx.Client | Any,
    url: str,
    *,
    accept: str,
    maximum: int,
) -> tuple[bytes, str, int, str]:
    response = request_with_retry(
        client,
        "GET",
        url,
        attempts=3,
        follow_redirects=True,
        timeout=httpx.Timeout(60, connect=10),
        headers={"Accept": accept},
    )
    response.raise_for_status()
    resolved_url = str(response.url)
    if resolved_url != url:
        raise ValueError(f"bioRxiv response resolved to an unexpected URL: {resolved_url}")
    payload = response.content
    if not 0 < len(payload) <= maximum:
        raise ValueError("bioRxiv response has an invalid byte size")
    return payload, resolved_url, response.status_code, response.headers.get("content-type", "")


def _validate_api_row(
    record: dict[str, Any], expected: dict[str, str], api_row: dict[str, Any]
) -> None:
    target_doi = normalize_doi(record.get("doi"))
    preprint_doi = normalize_doi(api_row.get("doi"))
    published_doi = normalize_doi(api_row.get("published"))
    version = str(api_row.get("version")) if api_row.get("version") is not None else None
    title = api_row.get("title")
    posted_date = api_row.get("date")
    if preprint_doi != expected["preprint_doi"]:
        raise ValueError("bioRxiv API preprint DOI differs from the reviewed mapping")
    if published_doi != target_doi:
        raise ValueError("bioRxiv API published field does not identify the target ACS DOI")
    if version != expected["preprint_version"]:
        raise ValueError("bioRxiv API version differs from the reviewed mapping")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("bioRxiv API preprint title is missing")
    if str(api_row.get("server") or "").casefold() != "biorxiv":
        raise ValueError("bioRxiv API record has an unrecognized server")
    if not isinstance(posted_date, str):
        raise TypeError("bioRxiv API posting date is missing")
    suffix = expected["preprint_doi"].removeprefix("10.1101/")
    date_path = posted_date.replace("-", "/")
    derived_pdf_url = f"https://www.biorxiv.org/content/biorxiv/early/{date_path}/{suffix}.full.pdf"
    if derived_pdf_url != expected["pdf_url"]:
        raise ValueError("bioRxiv API DOI/date do not rederive the reviewed PDF candidate")
    expected_jats_url = f"https://www.biorxiv.org/content/early/{date_path}/{suffix}.source.xml"
    if api_row.get("jatsxml") != expected_jats_url:
        raise ValueError("bioRxiv API JATS URL is not canonical for the reviewed mapping")
    reviewed_candidate_url = expected.get("processed_candidate_url", expected["pdf_url"])
    candidates = [
        candidate
        for candidate in record.get("pdf_candidates", [])
        if isinstance(candidate, dict) and candidate.get("pdf_url") == reviewed_candidate_url
    ]
    if len(candidates) != 1:
        raise ValueError(
            "processed record does not contain exactly one reviewed discovery candidate"
        )


def _existing_manifest(
    layout: Layout,
    record: dict[str, Any],
    *,
    require_direct_transport: bool,
) -> dict[str, Any] | None:
    paper_dir = layout.paper_directory(record)
    variant_dir = paper_dir / "versions" / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    for manifest_path in sorted(variant_dir.glob("metadata__*.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        local_path = manifest.get("local_path")
        if not isinstance(local_path, str):
            continue
        pdf_path = layout.root / local_path
        if biorxiv_preprint_manifest_identity_errors(layout, manifest, record, paper_dir, pdf_path):
            continue
        if (
            require_direct_transport
            and manifest.get("transport_provenance_verification_status")
            != BIORXIV_PREPRINT_DIRECT_TRANSPORT
        ):
            continue
        return manifest
    return None


def _acquire_one(
    layout: Layout,
    record: dict[str, Any],
    expected: dict[str, str],
    *,
    source_dir: Path | None,
    client: httpx.Client | Any | None,
) -> dict[str, Any]:
    require_direct = source_dir is None
    existing = _existing_manifest(
        layout,
        record,
        require_direct_transport=require_direct,
    )
    if existing is not None:
        return {**existing, "status": "already_present"}

    api_url = biorxiv_api_url(expected["preprint_doi"])
    if source_dir is None:
        assert client is not None
        api_payload, api_resolved_url, api_status, api_content_type = _download(
            client,
            api_url,
            accept="application/json",
            maximum=BIORXIV_PREPRINT_MAX_API_BYTES,
        )
        pdf_payload, resolved_url, pdf_status, pdf_content_type = _download(
            client,
            expected["pdf_url"],
            accept="application/pdf",
            maximum=MAX_PDF_BYTES,
        )
        transport_status = BIORXIV_PREPRINT_DIRECT_TRANSPORT
        transport_limitation = None
        acquisition_method = "direct_https_download"
    else:
        api_path = source_dir / f"{expected['input_stem']}.api.json"
        pdf_path = source_dir / f"{expected['input_stem']}.pdf"
        api_payload = _read_bounded(
            api_path,
            BIORXIV_PREPRINT_MAX_API_BYTES,
            "staged bioRxiv API response",
        )
        pdf_payload = _read_bounded(pdf_path, MAX_PDF_BYTES, "staged bioRxiv PDF")
        api_resolved_url = None
        resolved_url = None
        api_status = None
        pdf_status = None
        api_content_type = None
        pdf_content_type = None
        transport_status = BIORXIV_PREPRINT_IMPORTED_TRANSPORT
        transport_limitation = BIORXIV_PREPRINT_IMPORTED_LIMITATION
        acquisition_method = "verified_local_import"

    if b"%PDF" not in pdf_payload[:1024]:
        raise ValueError("bioRxiv PDF response does not begin with a PDF header")
    api_row = parse_biorxiv_api_payload(api_payload)
    _validate_api_row(record, expected, api_row)
    verification = _verify_pdf_payload(pdf_payload, api_row)

    paper_dir = layout.paper_directory(record)
    variant_dir = paper_dir / "versions" / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    variant_dir.mkdir(parents=True, exist_ok=True)
    pdf_hash = _sha256(pdf_payload)
    api_hash = _sha256(api_payload)
    pdf_path = variant_dir / f"paper__{pdf_hash[:12]}.pdf"
    manifest_path = variant_dir / f"metadata__{pdf_hash[:12]}.json"
    api_snapshot_path = variant_dir / f"biorxiv_api__{api_hash[:12]}.json"

    if manifest_path.exists():
        raise ValueError("content-addressed bioRxiv manifest already exists but is not valid")
    for path, payload in ((pdf_path, pdf_payload), (api_snapshot_path, api_payload)):
        if path.exists():
            if _sha256(path.read_bytes()) != _sha256(payload):
                raise ValueError(f"content-addressed path contains different bytes: {path.name}")
        else:
            atomic_write_bytes(path, payload)

    target_doi = normalize_doi(record.get("doi"))
    source_license = "CC-BY-NC-ND-4.0" if api_row.get("license") == "cc_by_nc_nd" else None
    metadata = {
        "schema_version": 1,
        "status": "downloaded",
        "retrieved_at": utc_now(),
        "record_id": record["record_id"],
        "doi": target_doi,
        "year": record["year"],
        "title": record.get("title_crossref") or record["title_source"],
        "artifact_type": "article_pdf",
        "local_path": layout.display_path(pdf_path),
        "bytes": len(pdf_payload),
        "sha256": pdf_hash,
        "source_name": BIORXIV_PREPRINT_SOURCE_NAME,
        "source_type": "preprint_repository",
        "source_url": expected["pdf_url"],
        "resolved_url": resolved_url,
        "source_access": "public bioRxiv full-text PDF",
        "source_version": BIORXIV_PREPRINT_SOURCE_VERSION,
        "source_version_detail": f"bioRxiv preprint version {expected['preprint_version']}",
        "source_version_assertion_method": "biorxiv_details_api_version",
        "source_version_assertion_evidence": [
            {
                "kind": "biorxiv_details_fields",
                "value": (
                    f"doi={expected['preprint_doi']}; version={expected['preprint_version']}; "
                    f"published={target_doi}"
                ),
            }
        ],
        "version_assertion_method": "upstream_metadata",
        "source_metadata_source": BIORXIV_PREPRINT_API_SOURCE,
        "source_intended_application": "full_text",
        "preprint_doi": expected["preprint_doi"],
        "preprint_title": api_row["title"],
        "preprint_version": expected["preprint_version"],
        "biorxiv_published_doi": target_doi,
        "biorxiv_posted_date": api_row["date"],
        "publication_mapping_method": "biorxiv_details_api_published_field",
        "relationship_to_target_work": "predecessor_preprint_published_as_target",
        "biorxiv_api_url": api_url,
        "biorxiv_api_resolved_url": api_resolved_url,
        "biorxiv_api_response_status": api_status,
        "biorxiv_api_response_content_type": api_content_type,
        "biorxiv_api_snapshot_path": layout.display_path(api_snapshot_path),
        "biorxiv_api_snapshot_sha256": api_hash,
        "biorxiv_api_snapshot_bytes": len(api_payload),
        "biorxiv_license_code": api_row.get("license"),
        "source_license": source_license,
        "license_metadata_source": BIORXIV_PREPRINT_API_SOURCE if source_license else None,
        "source_response_status": pdf_status,
        "source_response_content_type": pdf_content_type,
        "acquisition_method": acquisition_method,
        "transport_provenance_verification_status": transport_status,
        "transport_provenance_limitation": transport_limitation,
        "historical_evaluated_edition_relation": "not_established",
        "historical_evaluated_edition_equivalence_asserted": False,
        **verification,
    }
    validation_errors = biorxiv_preprint_manifest_identity_errors(
        layout,
        metadata,
        record,
        paper_dir,
        pdf_path,
    )
    if validation_errors:
        raise ValueError(
            "generated bioRxiv manifest failed validation: " + "; ".join(validation_errors)
        )
    write_json(manifest_path, metadata)
    return metadata


def acquire_biorxiv_preprints(
    layout: Layout,
    *,
    source_dir: Path | None = None,
    target_dois: Sequence[str] | None = None,
    client: httpx.Client | Any | None = None,
) -> dict[str, Any]:
    records = {
        normalize_doi(record.get("doi")): record
        for record in read_jsonl(layout.processed / "papers.jsonl")
    }
    targets = [
        normalize_doi(doi) or "" for doi in (target_dois or tuple(BIORXIV_PREPRINT_CANDIDATES))
    ]
    if any(target not in BIORXIV_PREPRINT_CANDIDATES for target in targets):
        raise ValueError("bioRxiv acquisition is limited to the six reviewed mappings")
    if source_dir is not None:
        source_dir = source_dir.resolve()
        if not source_dir.is_dir():
            raise FileNotFoundError(f"staged bioRxiv input directory is missing: {source_dir}")

    owns_client = source_dir is None and client is None
    if owns_client:
        client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
            trust_env=False,
        )
    results: list[dict[str, Any]] = []
    try:
        for target_doi in targets:
            record = records.get(target_doi)
            base = {
                "record_id": record.get("record_id") if record else None,
                "doi": target_doi,
                "year": record.get("year") if record else None,
                "title": (
                    record.get("title_crossref") or record.get("title_source") if record else None
                ),
            }
            if record is None:
                results.append({**base, "status": "failed", "error": "processed record missing"})
                continue
            try:
                result = _acquire_one(
                    layout,
                    record,
                    BIORXIV_PREPRINT_CANDIDATES[target_doi],
                    source_dir=source_dir,
                    client=client,
                )
            except Exception as error:  # noqa: BLE001 - isolate failures by reviewed mapping
                results.append(
                    {
                        **base,
                        "status": "failed",
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            else:
                results.append({**base, **result})
    finally:
        if owns_client and client is not None:
            client.close()

    layout.reports.mkdir(parents=True, exist_ok=True)
    write_csv(layout.reports / "biorxiv_preprint_acquisition.csv", results, REPORT_FIELDS)
    summary = {
        "scope": "six_explicit_biorxiv_predecessor_mappings",
        "mappings_considered": len(results),
        "downloaded": sum(result.get("status") == "downloaded" for result in results),
        "already_present": sum(result.get("status") == "already_present" for result in results),
        "failed": sum(result.get("status") == "failed" for result in results),
        "transport_verified": sum(
            result.get("transport_provenance_verification_status")
            == BIORXIV_PREPRINT_DIRECT_TRANSPORT
            for result in results
        ),
        "results": results,
    }
    write_json(layout.reports / "biorxiv_preprint_acquisition.json", summary)
    return summary
