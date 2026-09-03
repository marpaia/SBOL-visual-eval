"""bioRxiv predecessor-preprint vocabulary and manifest identity checks."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..layout import Layout
from ..util.text import normalize_doi
from . import pdf
from .manifests import is_hex_digest

BIORXIV_PREPRINT_VARIANT_DIRECTORY = "biorxiv_preprint_v1"
BIORXIV_PREPRINT_SOURCE_NAME = "bioRxiv"
BIORXIV_PREPRINT_API_SOURCE = "bioRxiv details API"
BIORXIV_PREPRINT_SOURCE_VERSION = "submittedVersion"
BIORXIV_PREPRINT_MAX_API_BYTES = 2 * 1024 * 1024
BIORXIV_PREPRINT_DIRECT_TRANSPORT = "verified_direct_https_response"
BIORXIV_PREPRINT_IMPORTED_TRANSPORT = "supplied_bytes_unverified"
BIORXIV_PREPRINT_IMPORTED_LIMITATION = (
    "The preprint DOI/title and bioRxiv API publication mapping are verified, but the corpus "
    "builder did not observe the HTTP response that supplied these imported bytes."
)
BIORXIV_PREPRINT_CANDIDATES: dict[str, dict[str, str]] = {
    "10.1021/acssynbio.8b00482": {
        "input_stem": "8b00482",
        "preprint_doi": "10.1101/367045",
        "preprint_version": "1",
        "pdf_url": ("https://www.biorxiv.org/content/biorxiv/early/2018/07/11/367045.full.pdf"),
    },
    "10.1021/acssynbio.9b00275": {
        "input_stem": "9b00275",
        "preprint_doi": "10.1101/694448",
        "preprint_version": "1",
        "pdf_url": ("https://www.biorxiv.org/content/biorxiv/early/2019/07/13/694448.full.pdf"),
        "processed_candidate_url": (
            "https://authors.library.caltech.edu/97175/3/acssynbio.9b00275.pdf"
        ),
    },
    "10.1021/acssynbio.0c00283": {
        "input_stem": "0c00283",
        "preprint_doi": "10.1101/729699",
        "preprint_version": "1",
        "pdf_url": ("https://www.biorxiv.org/content/biorxiv/early/2019/08/13/729699.full.pdf"),
    },
    "10.1021/acssynbio.9b00518": {
        "input_stem": "9b00518",
        "preprint_doi": "10.1101/748350",
        "preprint_version": "1",
        "pdf_url": ("https://www.biorxiv.org/content/biorxiv/early/2019/08/28/748350.full.pdf"),
    },
    "10.1021/acssynbio.0c00449": {
        "input_stem": "0c00449",
        "preprint_doi": "10.1101/2020.07.08.193060",
        "preprint_version": "1",
        "pdf_url": (
            "https://www.biorxiv.org/content/biorxiv/early/2020/07/09/2020.07.08.193060.full.pdf"
        ),
    },
    "10.1021/acssynbio.3c00098": {
        "input_stem": "3c00098",
        "preprint_doi": "10.1101/2022.11.22.517491",
        "preprint_version": "1",
        "pdf_url": (
            "https://www.biorxiv.org/content/biorxiv/early/2022/11/22/2022.11.22.517491.full.pdf"
        ),
    },
}


def biorxiv_api_url(preprint_doi: str) -> str:
    return f"https://api.biorxiv.org/details/biorxiv/{quote(preprint_doi, safe='/')}"


def is_biorxiv_preprint_manifest(manifest: dict[str, Any], paper_dir: Path, pdf_path: Path) -> bool:
    expected_directory = paper_dir / "versions" / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    return any(
        (
            pdf_path.parent.resolve() == expected_directory.resolve(),
            manifest.get("source_name") == BIORXIV_PREPRINT_SOURCE_NAME,
            manifest.get("source_metadata_source") == BIORXIV_PREPRINT_API_SOURCE,
            any(str(key).startswith("biorxiv_") for key in manifest),
            "biorxiv_api_snapshot_path" in manifest,
            "preprint_doi" in manifest,
            "biorxiv_published_doi" in manifest,
        )
    )


def parse_biorxiv_api_payload(payload: bytes) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError(f"duplicate JSON key: {key}")
            output[key] = value
        return output

    response = json.loads(payload, object_pairs_hook=reject_duplicate_keys)
    if not isinstance(response, dict):
        raise TypeError("bioRxiv API response is not a JSON object")
    messages = response.get("messages")
    if not (
        isinstance(messages, list)
        and len(messages) == 1
        and isinstance(messages[0], dict)
        and messages[0].get("status") == "ok"
    ):
        raise ValueError("bioRxiv API response has no unambiguous successful status")
    collection = response.get("collection")
    if not (
        isinstance(collection, list) and len(collection) == 1 and isinstance(collection[0], dict)
    ):
        raise ValueError("bioRxiv API response does not contain exactly one preprint record")
    return collection[0]


def biorxiv_api_snapshot(
    layout: Layout,
    manifest: dict[str, Any],
    paper_dir: Path,
    *,
    errors: list[str],
) -> dict[str, Any] | None:
    raw_path = manifest.get("biorxiv_api_snapshot_path")
    if not isinstance(raw_path, str) or not raw_path:
        errors.append("bioRxiv preprint manifest has no API snapshot path")
        return None
    declared_path = Path(raw_path)
    expected_directory = paper_dir / "versions" / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    try:
        expected_relative_directory = expected_directory.relative_to(layout.root)
    except ValueError:
        errors.append("bioRxiv preprint variant directory is outside the repository")
        return None
    if declared_path.is_absolute() or raw_path != declared_path.as_posix():
        errors.append("bioRxiv API snapshot path is not canonical and repository-relative")
        return None
    if declared_path.parent != expected_relative_directory:
        errors.append("bioRxiv API snapshot is not directly in its dedicated variant directory")
        return None

    expected_hash = manifest.get("biorxiv_api_snapshot_sha256")
    if not is_hex_digest(expected_hash, 64):
        errors.append("bioRxiv preprint manifest has no valid API snapshot SHA-256")
        expected_hash = None
    if not re.fullmatch(r"biorxiv_api__[0-9a-f]{12}\.json", declared_path.name):
        errors.append("bioRxiv API snapshot has an invalid content-addressed filename")
    elif expected_hash is not None and declared_path.name != (
        f"biorxiv_api__{expected_hash[:12].casefold()}.json"
    ):
        errors.append("bioRxiv API snapshot filename does not match its SHA-256")

    snapshot_path = layout.root / declared_path
    candidate = snapshot_path
    while candidate != layout.root:
        if candidate.is_symlink():
            errors.append("bioRxiv API snapshot path may not contain symlinks")
            return None
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    try:
        resolved_path = snapshot_path.resolve()
        resolved_path.relative_to(expected_directory.resolve())
    except ValueError:
        errors.append("bioRxiv API snapshot escapes its dedicated variant directory")
        return None
    if not resolved_path.is_file():
        errors.append("bioRxiv API snapshot file is missing")
        return None
    declared_bytes = manifest.get("biorxiv_api_snapshot_bytes")
    if (
        not isinstance(declared_bytes, int)
        or isinstance(declared_bytes, bool)
        or not 0 < declared_bytes <= BIORXIV_PREPRINT_MAX_API_BYTES
    ):
        errors.append("bioRxiv API snapshot has an invalid declared byte count")
    try:
        stat_size = resolved_path.stat().st_size
    except OSError as error:
        errors.append(f"bioRxiv API snapshot cannot be read: {type(error).__name__}: {error}")
        return None
    if not 0 < stat_size <= BIORXIV_PREPRINT_MAX_API_BYTES:
        errors.append("bioRxiv API snapshot exceeds its safety limit")
        return None
    try:
        with resolved_path.open("rb") as handle:
            payload = handle.read(BIORXIV_PREPRINT_MAX_API_BYTES + 1)
    except OSError as error:
        errors.append(f"bioRxiv API snapshot cannot be read: {type(error).__name__}: {error}")
        return None
    if len(payload) > BIORXIV_PREPRINT_MAX_API_BYTES:
        errors.append("bioRxiv API snapshot exceeds its safety limit")
        return None
    if manifest.get("biorxiv_api_snapshot_bytes") != len(payload):
        errors.append("bioRxiv API snapshot byte count mismatch")
    actual_hash = hashlib.sha256(payload).hexdigest()
    if expected_hash is not None and actual_hash != expected_hash.casefold():
        errors.append("bioRxiv API snapshot SHA-256 mismatch")
    try:
        return parse_biorxiv_api_payload(payload)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError, ValueError) as error:
        errors.append(f"bioRxiv API snapshot cannot be parsed: {type(error).__name__}: {error}")
        return None


def verify_biorxiv_preprint_pdf(path: Path, api_row: dict[str, Any]) -> dict[str, Any]:
    preprint_doi = normalize_doi(api_row.get("doi"))
    preprint_title = api_row.get("title")
    posted_date = api_row.get("date")
    if not isinstance(preprint_doi, str) or not isinstance(preprint_title, str):
        raise TypeError("bioRxiv API record has no preprint DOI/title")
    if not isinstance(posted_date, str):
        raise TypeError("bioRxiv API record has no posting date")
    verification = pdf.verify_pdf(
        path,
        {
            "record_id": f"preprint:{preprint_doi}",
            "doi": preprint_doi,
            "title_source": preprint_title,
            "title_crossref": preprint_title,
        },
    )
    if verification.get("title_similarity", 0) < 0.95:
        raise ValueError("bioRxiv PDF title does not strongly match its API record")
    reader = pdf.PdfReader(path)
    first_page = reader.pages[0].extract_text() or ""
    doi_pattern = re.compile(
        rf"https?://doi\.org/{re.escape(preprint_doi)}(?=$|[\s;,]|doi:)",
        flags=re.IGNORECASE,
    )
    if doi_pattern.search(first_page) is None or "biorxiv preprint" not in first_page.casefold():
        raise ValueError("bioRxiv PDF has no exact-boundary preprint DOI banner")
    try:
        date_value = date.fromisoformat(posted_date)
    except ValueError as error:
        raise ValueError("bioRxiv API posting date is invalid") from error
    posted_label = (
        f"this version posted {date_value.strftime('%B')} {date_value.day}, {date_value.year}"
    )
    normalized_first_page = re.sub(r"\s+", " ", first_page).casefold()
    if posted_label.casefold() not in normalized_first_page:
        raise ValueError("bioRxiv PDF posting-date banner differs from its API record")
    return {
        **verification,
        "source_identity_check": "biorxiv_preprint_doi_title_and_posted_date",
    }


def biorxiv_preprint_manifest_identity_errors(
    layout: Layout,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
) -> list[str]:
    if not is_biorxiv_preprint_manifest(manifest, paper_dir, pdf_path):
        return []
    errors: list[str] = []
    target_doi = normalize_doi(record.get("doi"))
    expected = BIORXIV_PREPRINT_CANDIDATES.get(target_doi or "")
    if expected is None:
        errors.append("bioRxiv preprint mapping is not in the reviewed six-record allowlist")
        return errors

    expected_directory = paper_dir / "versions" / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    if pdf_path.parent.resolve() != expected_directory.resolve():
        errors.append("bioRxiv preprint artifact is not in its dedicated variant directory")
    if manifest.get("schema_version") != 1:
        errors.append("bioRxiv preprint manifest has an unsupported schema_version")
    if manifest.get("status") not in {"downloaded", "already_present"}:
        errors.append("bioRxiv preprint manifest has an unrecognized status")
    if manifest.get("source_name") != BIORXIV_PREPRINT_SOURCE_NAME:
        errors.append("bioRxiv preprint manifest has an unrecognized source_name")
    if manifest.get("source_type") != "preprint_repository":
        errors.append("bioRxiv preprint manifest has an unrecognized source_type")
    if manifest.get("source_access") != "public bioRxiv full-text PDF":
        errors.append("bioRxiv preprint manifest has an unrecognized source_access")
    if manifest.get("source_metadata_source") != BIORXIV_PREPRINT_API_SOURCE:
        errors.append("bioRxiv preprint manifest has an unrecognized metadata source")
    if manifest.get("source_intended_application") != "full_text":
        errors.append("bioRxiv preprint manifest has an unrecognized intended application")
    if manifest.get("source_version") != BIORXIV_PREPRINT_SOURCE_VERSION:
        errors.append("bioRxiv preprint manifest does not identify a submitted version")
    if manifest.get("source_version_detail") != (
        f"bioRxiv preprint version {expected['preprint_version']}"
    ):
        errors.append("bioRxiv preprint version detail is inconsistent")
    if manifest.get("source_version_assertion_method") != "biorxiv_details_api_version":
        errors.append("bioRxiv preprint manifest has an unrecognized version assertion method")
    expected_assertion_evidence = [
        {
            "kind": "biorxiv_details_fields",
            "value": (
                f"doi={expected['preprint_doi']}; version={expected['preprint_version']}; "
                f"published={target_doi}"
            ),
        }
    ]
    if manifest.get("source_version_assertion_evidence") != expected_assertion_evidence:
        errors.append("bioRxiv preprint version assertion evidence is inconsistent")
    if manifest.get("publication_mapping_method") != "biorxiv_details_api_published_field":
        errors.append("bioRxiv preprint manifest has an unrecognized publication mapping method")
    if manifest.get("relationship_to_target_work") != "predecessor_preprint_published_as_target":
        errors.append("bioRxiv preprint manifest has an unrecognized target-work relationship")
    if manifest.get("historical_evaluated_edition_equivalence_asserted") is not False:
        errors.append("bioRxiv preprint manifest does not withhold historical-edition equivalence")

    preprint_doi = normalize_doi(manifest.get("preprint_doi"))
    if preprint_doi != expected["preprint_doi"]:
        errors.append("bioRxiv preprint DOI differs from the reviewed mapping")
    if manifest.get("preprint_version") != expected["preprint_version"]:
        errors.append("bioRxiv preprint version differs from the reviewed mapping")
    if normalize_doi(manifest.get("biorxiv_published_doi")) != target_doi:
        errors.append("bioRxiv published DOI differs from the processed target DOI")
    expected_api_url = biorxiv_api_url(expected["preprint_doi"])
    if manifest.get("biorxiv_api_url") != expected_api_url:
        errors.append("bioRxiv API URL differs from the reviewed mapping")
    if manifest.get("source_url") != expected["pdf_url"]:
        errors.append("bioRxiv PDF source URL differs from the reviewed candidate")
    reviewed_candidate_url = expected.get("processed_candidate_url", expected["pdf_url"])
    matching_candidates = [
        candidate
        for candidate in record.get("pdf_candidates", [])
        if isinstance(candidate, dict) and candidate.get("pdf_url") == reviewed_candidate_url
    ]
    if len(matching_candidates) != 1:
        errors.append("processed record does not contain exactly one reviewed discovery candidate")

    transport_status = manifest.get("transport_provenance_verification_status")
    if transport_status == BIORXIV_PREPRINT_DIRECT_TRANSPORT:
        if manifest.get("transport_provenance_limitation") is not None:
            errors.append("direct bioRxiv response unexpectedly declares a transport limitation")
        if manifest.get("acquisition_method") != "direct_https_download":
            errors.append("direct bioRxiv transport has an inconsistent acquisition method")
        if manifest.get("resolved_url") != expected["pdf_url"]:
            errors.append("direct bioRxiv PDF resolved URL differs from the reviewed candidate")
        if manifest.get("biorxiv_api_resolved_url") != expected_api_url:
            errors.append("direct bioRxiv API resolved URL differs from the reviewed mapping")
        if manifest.get("source_response_status") != 200:
            errors.append("direct bioRxiv PDF response status is not HTTP 200")
        if manifest.get("biorxiv_api_response_status") != 200:
            errors.append("direct bioRxiv API response status is not HTTP 200")
        if (
            not str(manifest.get("source_response_content_type") or "")
            .casefold()
            .startswith("application/pdf")
        ):
            errors.append("direct bioRxiv PDF response has an unrecognized content type")
        if "json" not in str(manifest.get("biorxiv_api_response_content_type") or "").casefold():
            errors.append("direct bioRxiv API response has an unrecognized content type")
    elif transport_status == BIORXIV_PREPRINT_IMPORTED_TRANSPORT:
        if manifest.get("transport_provenance_limitation") != (
            BIORXIV_PREPRINT_IMPORTED_LIMITATION
        ):
            errors.append("imported bioRxiv transport limitation is missing or inconsistent")
        if manifest.get("acquisition_method") != "verified_local_import":
            errors.append("imported bioRxiv transport has an inconsistent acquisition method")
        for field_name in (
            "resolved_url",
            "biorxiv_api_resolved_url",
            "source_response_status",
            "source_response_content_type",
            "biorxiv_api_response_status",
            "biorxiv_api_response_content_type",
        ):
            if manifest.get(field_name) is not None:
                errors.append(f"imported bioRxiv artifact unexpectedly sets {field_name}")
    else:
        errors.append("bioRxiv preprint manifest has an unrecognized transport status")

    api_row = biorxiv_api_snapshot(layout, manifest, paper_dir, errors=errors)
    if api_row is None:
        return errors
    api_preprint_doi = normalize_doi(api_row.get("doi"))
    api_published_doi = normalize_doi(api_row.get("published"))
    api_title = api_row.get("title")
    api_version = str(api_row.get("version")) if api_row.get("version") is not None else None
    if api_preprint_doi != expected["preprint_doi"]:
        errors.append("bioRxiv API preprint DOI differs from the reviewed mapping")
    if api_published_doi != target_doi:
        errors.append("bioRxiv API published field does not identify the processed target DOI")
    if api_version != expected["preprint_version"]:
        errors.append("bioRxiv API version differs from the reviewed mapping")
    if manifest.get("biorxiv_posted_date") != api_row.get("date"):
        errors.append("bioRxiv posting date differs from its API snapshot")
    if not isinstance(api_title, str) or not api_title.strip():
        errors.append("bioRxiv API preprint title is missing")
    elif manifest.get("preprint_title") != api_title:
        errors.append("bioRxiv manifest preprint title differs from its API snapshot")
    if str(api_row.get("server") or "").casefold() != "biorxiv":
        errors.append("bioRxiv API record has an unrecognized server")
    if manifest.get("biorxiv_license_code") != api_row.get("license"):
        errors.append("bioRxiv license code differs from its API snapshot")
    expected_license = "CC-BY-NC-ND-4.0" if api_row.get("license") == "cc_by_nc_nd" else None
    if manifest.get("source_license") != expected_license:
        errors.append("bioRxiv source license differs from its API snapshot")
    expected_license_source = BIORXIV_PREPRINT_API_SOURCE if expected_license else None
    if manifest.get("license_metadata_source") != expected_license_source:
        errors.append("bioRxiv license metadata source is inconsistent")

    api_date = api_row.get("date")
    if isinstance(api_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", api_date):
        preprint_suffix = expected["preprint_doi"].removeprefix("10.1101/")
        date_path = api_date.replace("-", "/")
        derived_url = (
            f"https://www.biorxiv.org/content/biorxiv/early/{date_path}/{preprint_suffix}.full.pdf"
        )
        if derived_url != expected["pdf_url"]:
            errors.append("bioRxiv API date and DOI do not rederive the reviewed PDF URL")
        expected_jats_url = (
            f"https://www.biorxiv.org/content/early/{date_path}/{preprint_suffix}.source.xml"
        )
        if api_row.get("jatsxml") != expected_jats_url:
            errors.append("bioRxiv API JATS URL is not canonical for its DOI and posting date")
    else:
        errors.append("bioRxiv API record has no valid posting date")
    if pdf_path.is_file():
        try:
            verification = verify_biorxiv_preprint_pdf(pdf_path, api_row)
        except Exception as error:  # noqa: BLE001 - report every source-specific failure
            errors.append(f"bioRxiv preprint PDF identity failed: {type(error).__name__}: {error}")
        else:
            for field_name in (
                "artifact_type",
                "page_count",
                "identity_check",
                "title_similarity",
                "source_identity_check",
            ):
                if manifest.get(field_name) != verification.get(field_name):
                    errors.append(
                        f"bioRxiv preprint manifest {field_name} differs from PDF verification"
                    )
    return errors
