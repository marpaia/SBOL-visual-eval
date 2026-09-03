"""The loopback bridge: eligibility queue, discovery, storage, and reports."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ...layout import Layout
from ...provenance.pmc_web import (
    PMC_WEB_SOURCE_NAME,
)
from ...util.storage import read_jsonl, sha256_file, utc_now, write_csv, write_json
from . import verifier
from .archive import (
    _archived_paths,
    _preserve_invalid_metadata,
    _safe_paper_directory,
    _safe_variant_directory,
    _validate_archived_pair,
)
from .article_xml import _article_xml_pdf_link, _verified_article_xml_artifact
from .discovery import (
    _discover_main_pdf_url,
    _page_author_manuscript_assertion,
    _pmc_challenge_reason,
)
from .eligibility import _eligible_record, _explicit_license
from .errors import (
    PMC_CHALLENGE_RETRY_SECONDS,
    AuthorManuscriptVersionUnestablishedError,
    PmcTransientChallengeError,
    QueueClaimError,
)
from .manifests import PMC_AUTHOR_MANUSCRIPT_VERSION, build_pdf_manifest
from .urls import (
    PMC_ORIGIN,
    _article_url,
    _normalized_doi,
    _normalized_pmcid,
    _validated_article_url,
    _validated_pmc_pdf_url,
)

MAX_PDF_BYTES = 120 * 1024 * 1024
MAX_HTML_BYTES = 16 * 1024 * 1024
HTML_REQUEST_INTERVAL_SECONDS = 1.0
HTML_RESPONSE_DEADLINE_SECONDS = 60
USER_AGENT = "SBOL-visual-eval/0.1 (PMC public article PDF fallback)"


REPORT_FIELDS = (
    "record_id",
    "doi",
    "year",
    "title",
    "status",
    "pmcid",
    "versioned_pmcid",
    "local_path",
    "retrieved_at",
    "source_url",
    "resolved_url",
    "source_landing_page_url",
    "source_landing_page_snapshot_path",
    "source_landing_page_snapshot_sha256",
    "source_landing_page_snapshot_bytes",
    "source_name",
    "source_version",
    "requested_version",
    "version_assertion_method",
    "source_version_assertion_method",
    "historical_evaluated_edition_relation",
    "historical_evaluated_edition_equivalence_asserted",
    "transport_provenance_verification_status",
    "transport_provenance_limitation",
    "source_license",
    "source_access",
    "pmc_manifest_path",
    "pmc_manifest_sha256",
    "pmc_manifest_original_path",
    "pmc_package_metadata_snapshot_path",
    "pmc_package_metadata_snapshot_sha256",
    "pmc_package_metadata_snapshot_bytes",
    "pmc_article_xml_original_path",
    "pmc_article_xml_snapshot_path",
    "pmc_article_xml_snapshot_sha256",
    "pmc_article_xml_snapshot_bytes",
    "bytes",
    "sha256",
    "page_count",
    "artifact_type",
    "identity_check",
    "title_similarity",
    "error",
)


def _csv_safe_value(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class PmcWebBridge:
    """Receive public PMC author-manuscript PDFs from a same-origin browser page."""

    def __init__(
        self,
        layout: Layout,
        *,
        token: str | None = None,
        client: httpx.Client | Any | None = None,
    ) -> None:
        source_path = layout.processed / "papers.jsonl"
        if not source_path.is_file():
            raise FileNotFoundError(
                "processed manifest is missing; run `sbol-visual-data build` first"
            )
        self.layout = layout
        self.token = token or secrets.token_urlsafe(24)
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(30, connect=10),
        )
        self._owns_client = client is None
        self.records: list[dict[str, Any]] = []
        live_html_records: list[dict[str, Any]] = []
        self.manifests_by_doi: dict[str, dict[str, Any] | None] = {}
        self.pmc_manifest_bytes_by_doi: dict[str, bytes] = {}
        self.pmc_package_metadata_bytes_by_doi: dict[str, bytes] = {}
        self.pmc_article_xml_bytes_by_doi: dict[str, bytes] = {}
        self.pmc_article_xml_paths_by_doi: dict[str, Path] = {}
        self.pmc_article_xml_links_by_doi: dict[str, dict[str, str]] = {}
        self.eligibility_counts: Counter[str] = Counter()
        for record in read_jsonl(source_path):
            manifest, reason = _eligible_record(layout, record)
            self.eligibility_counts[reason] += 1
            if reason not in {
                "eligible",
                "eligible_requires_page_author_manuscript_evidence",
            }:
                continue
            doi = _normalized_doi(record.get("doi"))
            if doi is None:
                self.eligibility_counts["eligible_missing_doi"] += 1
                continue
            pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
            assert pmcid is not None
            if manifest is not None:
                manifest_path = _safe_paper_directory(layout, record) / "pmc.json"
                manifest_bytes = manifest_path.read_bytes()
                try:
                    reread_manifest = json.loads(manifest_bytes)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"eligible PMC manifest changed during bridge initialization: {manifest_path}"
                    ) from error
                if reread_manifest != manifest:
                    raise ValueError(
                        f"eligible PMC manifest changed during bridge initialization: {manifest_path}"
                    )
                self.pmc_manifest_bytes_by_doi[doi] = manifest_bytes
                package_metadata_local_path = manifest.get("package_metadata_local_path")
                if not isinstance(package_metadata_local_path, str):
                    raise ValueError("eligible PMC manifest has no package metadata path")
                package_metadata_path = Path(package_metadata_local_path)
                if not package_metadata_path.is_absolute():
                    package_metadata_path = layout.root / package_metadata_path
                paper_directory = _safe_paper_directory(layout, record).resolve()
                try:
                    package_metadata_path.resolve().relative_to(paper_directory)
                except ValueError as error:
                    raise ValueError(
                        "eligible PMC package metadata path escapes its paper directory"
                    ) from error
                package_metadata_bytes = package_metadata_path.read_bytes()
                if hashlib.sha256(package_metadata_bytes).hexdigest() != manifest.get(
                    "package_metadata_sha256"
                ):
                    raise ValueError("eligible PMC package metadata changed during initialization")
                self.pmc_package_metadata_bytes_by_doi[doi] = package_metadata_bytes
                article_xml_path, article_xml_bytes = _verified_article_xml_artifact(
                    layout,
                    record,
                    manifest,
                )
                article_xml_link = _article_xml_pdf_link(article_xml_bytes, record, pmcid)
                package_metadata = manifest.get("package_metadata")
                expected_manuscript_id = (
                    package_metadata.get("mid") if isinstance(package_metadata, dict) else None
                )
                if (
                    not isinstance(expected_manuscript_id, str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,127}", expected_manuscript_id)
                    or article_xml_link["manuscript_id"].casefold()
                    != expected_manuscript_id.casefold()
                ):
                    raise ValueError(
                        "eligible PMC article XML manuscript ID differs from package metadata"
                    )
                self.pmc_article_xml_bytes_by_doi[doi] = article_xml_bytes
                self.pmc_article_xml_paths_by_doi[doi] = article_xml_path
                self.pmc_article_xml_links_by_doi[doi] = article_xml_link
            if manifest is None:
                live_html_records.append(record)
            else:
                self.records.append(record)
            self.manifests_by_doi[doi] = manifest
        # Exhaust deterministic, local XML discovery before making even one live
        # article-page request. Both partitions preserve processed-manifest order.
        self.records.extend(live_html_records)
        self.records_by_doi = {
            _normalized_doi(record.get("doi")): record for record in self.records
        }
        self.results: dict[str, dict[str, Any]] = {}
        self.attempted: set[str] = set()
        self.queued_items: dict[str, dict[str, Any]] = {}
        self.invalid_existing_variants: list[str] = []
        self.lock = threading.RLock()
        self.discovery_lock = threading.Lock()
        self.last_html_request_started_at = 0.0
        self.challenge_retry_not_before = 0.0
        self.challenge_cooldown_path = layout.reports / "pmc_web_cooldown.json"
        self._load_challenge_cooldown()
        self.attempt_log_path = layout.reports / "pmc_web_attempts.jsonl"
        (
            self.attempt_history_records,
            self.attempt_history_counts,
        ) = self._read_attempt_history()
        self.session_attempt_records = 0
        self._load_existing()
        self.write_report()

    @staticmethod
    def _base_result(record: dict[str, Any], pmcid: str) -> dict[str, Any]:
        return {
            "record_id": record["record_id"],
            "doi": _normalized_doi(record.get("doi")),
            "year": record["year"],
            "title": record.get("title_crossref") or record["title_source"],
            "pmcid": pmcid,
        }

    def _load_existing(self) -> None:
        for record in self.records:
            doi = _normalized_doi(record.get("doi"))
            assert doi is not None
            pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
            assert pmcid is not None
            directory = _safe_variant_directory(self.layout, record, create=False)
            valid: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for metadata_path in sorted(directory.glob("metadata__*.json")):
                pdf_path = metadata_path.with_name(
                    metadata_path.name.replace("metadata__", "paper__", 1)
                ).with_suffix(".pdf")
                try:
                    valid.append(
                        _validate_archived_pair(self.layout, record, pdf_path, metadata_path)
                    )
                except Exception as error:  # noqa: BLE001 - report invalid local variants
                    self.invalid_existing_variants.append(
                        f"{self.layout.display_path(metadata_path)}: "
                        f"{type(error).__name__}: {error}"
                    )
            if valid:
                metadata, verification = max(
                    valid, key=lambda pair: str(pair[0].get("retrieved_at") or "")
                )
                self.results[doi] = {
                    **self._base_result(record, pmcid),
                    **metadata,
                    **verification,
                    "status": "already_present",
                }

    def _authorized(self, supplied_token: str | None) -> bool:
        return bool(supplied_token) and secrets.compare_digest(supplied_token, self.token)

    def _load_challenge_cooldown(self) -> None:
        try:
            payload = json.loads(self.challenge_cooldown_path.read_text(encoding="utf-8"))
            if (
                payload.get("schema_version") != 1
                or payload.get("status") != "pmc_access_challenge_cooldown"
            ):
                return
            retry_not_before = datetime.fromisoformat(str(payload["retry_not_before"]))
            if retry_not_before.tzinfo is None:
                return
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            return
        remaining = (retry_not_before.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        if remaining > 0:
            self.challenge_retry_not_before = time.monotonic() + min(remaining, 60 * 60)

    def _set_challenge_cooldown(self, retry_after_seconds: int, *, reason: str) -> None:
        retry_after_seconds = max(1, min(retry_after_seconds, 60 * 60))
        self.challenge_retry_not_before = max(
            self.challenge_retry_not_before,
            time.monotonic() + retry_after_seconds,
        )
        retry_not_before = datetime.now(UTC) + timedelta(seconds=retry_after_seconds)
        write_json(
            self.challenge_cooldown_path,
            {
                "schema_version": 1,
                "status": "pmc_access_challenge_cooldown",
                "recorded_at": utc_now(),
                "retry_not_before": retry_not_before.isoformat(timespec="seconds"),
                "retry_after_seconds": retry_after_seconds,
                "reason": reason,
            },
        )

    def _append_attempt(self, result: dict[str, Any], *, phase: str) -> None:
        entry = {**result, "phase": phase, "attempted_at": utc_now()}
        self.attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.attempt_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        self.attempt_history_records += 1
        self.session_attempt_records += 1
        self.attempt_history_counts[str(entry.get("status") or "unknown")] += 1

    def _read_attempt_history(self) -> tuple[int, Counter[str]]:
        if not self.attempt_log_path.is_file():
            return 0, Counter()
        counts: Counter[str] = Counter()
        total = 0
        with self.attempt_log_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    counts["invalid_log_record"] += 1
                else:
                    counts[str(entry.get("status") or "unknown")] += 1
        return total, counts

    def _maybe_write_report(self) -> None:
        if self.session_attempt_records and self.session_attempt_records % 25 == 0:
            self.write_report()

    def _discover_item(self, record: dict[str, Any]) -> dict[str, Any]:
        doi = _normalized_doi(record.get("doi"))
        assert doi is not None
        pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
        assert pmcid is not None
        article_url = _article_url(pmcid)
        manifest = self.manifests_by_doi[doi]
        if manifest is not None:
            assertion = {
                "method": "pmc_acquisition_manifest",
                "evidence": [
                    {
                        "kind": "pmc_manifest_fields",
                        "value": "is_manuscript=true; source_version=author_manuscript",
                    }
                ],
            }
            return {
                "doi": doi,
                "pmcid": pmcid,
                "article_url": article_url,
                "url": self.pmc_article_xml_links_by_doi[doi]["pdf_url"],
                "version_assertion_method": assertion["method"],
                "_version_assertion": assertion,
                "_source_page_payload": None,
                "_article_xml_payload": self.pmc_article_xml_bytes_by_doi[doi],
            }

        with self.discovery_lock:
            wait_seconds = (
                self.last_html_request_started_at + HTML_REQUEST_INTERVAL_SECONDS - time.monotonic()
            )
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self.last_html_request_started_at = time.monotonic()
            html_deadline = self.last_html_request_started_at + HTML_RESPONSE_DEADLINE_SECONDS
            with self.client.stream("GET", article_url) as response:
                if response.status_code in {403, 429} or 500 <= response.status_code < 600:
                    retry_after = response.headers.get("retry-after")
                    retry_seconds = (
                        min(
                            max(int(retry_after), PMC_CHALLENGE_RETRY_SECONDS),
                            60 * 60,
                        )
                        if retry_after and retry_after.isdigit()
                        else PMC_CHALLENGE_RETRY_SECONDS
                    )
                    raise PmcTransientChallengeError(
                        f"PMC article endpoint returned transient HTTP {response.status_code}",
                        retry_after_seconds=retry_seconds,
                    )
                if 300 <= response.status_code < 400:
                    raise PmcTransientChallengeError(
                        "public PMC article URL unexpectedly redirected"
                    )
                response.raise_for_status()
                resolved_article_url = _validated_article_url(
                    str(response.url), pmcid, field_name="resolved article URL"
                )
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        parsed_length = int(declared_length)
                    except ValueError as error:
                        raise PmcTransientChallengeError(
                            "public PMC HTML has an invalid Content-Length"
                        ) from error
                    if parsed_length < 0 or parsed_length > MAX_HTML_BYTES:
                        raise PmcTransientChallengeError(
                            "public PMC HTML exceeds the response safety limit"
                        )
                payload = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > html_deadline:
                        raise PmcTransientChallengeError(
                            "public PMC HTML exceeded the response time limit"
                        )
                    payload.extend(chunk)
                    if len(payload) > MAX_HTML_BYTES:
                        raise PmcTransientChallengeError(
                            "public PMC HTML exceeds the response safety limit"
                        )
        page_payload = bytes(payload)
        challenge_reason = _pmc_challenge_reason(page_payload)
        if challenge_reason is not None:
            raise PmcTransientChallengeError(
                f"PMC article endpoint returned a {challenge_reason} interstitial"
            )
        pdf_url = _discover_main_pdf_url(page_payload, resolved_article_url, pmcid)
        assertion = _page_author_manuscript_assertion(page_payload, pdf_url)
        if assertion is None:
            raise AuthorManuscriptVersionUnestablishedError(
                "public PMC page and PDF basename do not establish author-manuscript status"
            )
        return {
            "doi": doi,
            "pmcid": pmcid,
            "article_url": article_url,
            "url": pdf_url,
            "version_assertion_method": assertion["method"],
            "_version_assertion": assertion,
            "_source_page_payload": page_payload,
            "_article_xml_payload": None,
        }

    def next_items(self, limit: int = 1) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), 1)
        items: list[dict[str, Any]] = []
        while len(items) < limit:
            with self.lock:
                retry_seconds = int(self.challenge_retry_not_before - time.monotonic()) + 1
                if retry_seconds > 0:
                    raise PmcTransientChallengeError(
                        "PMC discovery is paused after an access challenge",
                        retry_after_seconds=retry_seconds,
                    )
                record = next(
                    (
                        candidate
                        for candidate in self.records
                        if (doi := _normalized_doi(candidate.get("doi"))) not in self.results
                        and doi not in self.attempted
                    ),
                    None,
                )
                if record is None:
                    break
                doi = _normalized_doi(record.get("doi"))
                assert doi is not None
                pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
                assert pmcid is not None
                self.attempted.add(doi)
            try:
                item = self._discover_item(record)
            except (PmcTransientChallengeError, httpx.TransportError) as error:
                if not isinstance(error, PmcTransientChallengeError):
                    error = PmcTransientChallengeError(
                        f"PMC article request failed transiently: {type(error).__name__}"
                    )
                version_established = self.manifests_by_doi[doi] is not None
                result: dict[str, Any] = {
                    **self._base_result(record, pmcid),
                    "status": "transient_challenge",
                    "source_landing_page_url": _article_url(str(record["europepmc_pmcid"])),
                    "source_name": PMC_WEB_SOURCE_NAME,
                    "requested_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
                    "source_access": "public PMC browser session with same-origin cookies",
                    "artifact_type": "article_pdf",
                    "retry_after_seconds": error.retry_after_seconds,
                    "error": str(error)[:1000],
                }
                if version_established:
                    result["source_version"] = PMC_AUTHOR_MANUSCRIPT_VERSION
                with self.lock:
                    self.attempted.discard(doi)
                    self._set_challenge_cooldown(
                        error.retry_after_seconds,
                        reason="server_side_article_html_challenge",
                    )
                    self._append_attempt(result, phase="html_discovery_transient")
                    self._maybe_write_report()
                raise
            except Exception as error:  # noqa: BLE001 - isolate public-page failures
                version_established = self.manifests_by_doi[doi] is not None
                self.record_failure(
                    doi,
                    source_url=None,
                    resolved_url=None,
                    article_url=_article_url(str(record["europepmc_pmcid"])),
                    error=f"{type(error).__name__}: {error}",
                    phase=(
                        "html_version_assertion"
                        if isinstance(error, AuthorManuscriptVersionUnestablishedError)
                        else "html_discovery"
                    ),
                    status=(
                        "skipped_version_unestablished"
                        if isinstance(error, AuthorManuscriptVersionUnestablishedError)
                        else "failed"
                    ),
                    version_established=version_established,
                    require_queued=False,
                )
                continue
            with self.lock:
                assertion = item.pop("_version_assertion")
                source_page_payload = item.pop("_source_page_payload")
                article_xml_payload = item.pop("_article_xml_payload")
                queue_nonce = secrets.token_urlsafe(18)
                self.queued_items[doi] = {
                    "nonce": queue_nonce,
                    "source_url": item["url"],
                    "version_assertion": assertion,
                    "source_page_payload": source_page_payload,
                    "article_xml_payload": article_xml_payload,
                }
                item["queue_nonce"] = queue_nonce
            items.append(item)
        return items

    def record_transient_challenge(
        self,
        doi: str,
        *,
        source_url: str,
        resolved_url: str | None,
        article_url: str,
        error: str,
        queue_nonce: str | None,
    ) -> dict[str, Any]:
        normalized_doi = _normalized_doi(doi)
        record = self.records_by_doi.get(normalized_doi)
        if record is None or normalized_doi is None:
            raise KeyError(f"DOI is not an eligible PMC author manuscript: {doi}")
        pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
        assert pmcid is not None
        source_url = _validated_pmc_pdf_url(source_url, pmcid, field_name="source_url")
        if resolved_url is not None:
            resolved_url = _validated_pmc_pdf_url(
                resolved_url,
                pmcid,
                field_name="resolved_url",
            )
        article_url = _validated_article_url(article_url, pmcid, field_name="article_url")
        with self.lock:
            self._claim_queue_item(normalized_doi, queue_nonce, source_url=source_url)
            self.attempted.discard(normalized_doi)
            self._set_challenge_cooldown(
                PMC_CHALLENGE_RETRY_SECONDS,
                reason="browser_pdf_response_challenge",
            )
            result = {
                **self._base_result(record, pmcid),
                "status": "transient_challenge",
                "source_url": source_url,
                "resolved_url": resolved_url,
                "source_landing_page_url": article_url,
                "source_name": PMC_WEB_SOURCE_NAME,
                "source_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
                "requested_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
                "source_access": "public PMC browser session with same-origin cookies",
                "artifact_type": "article_pdf",
                "retry_after_seconds": PMC_CHALLENGE_RETRY_SECONDS,
                "error": error[:1000],
            }
            self._append_attempt(result, phase="browser_pdf_fetch_transient")
            self._maybe_write_report()
        return result

    def release_queue_item(
        self,
        doi: str,
        *,
        source_url: str,
        resolved_url: str | None,
        article_url: str,
        reason: str,
        queue_nonce: str | None,
    ) -> dict[str, Any]:
        normalized_doi = _normalized_doi(doi)
        record = self.records_by_doi.get(normalized_doi)
        if record is None or normalized_doi is None:
            raise KeyError(f"DOI is not an eligible PMC author manuscript: {doi}")
        pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
        assert pmcid is not None
        source_url = _validated_pmc_pdf_url(source_url, pmcid, field_name="source_url")
        if resolved_url is not None:
            resolved_url = _validated_pmc_pdf_url(
                resolved_url,
                pmcid,
                field_name="resolved_url",
            )
        article_url = _validated_article_url(article_url, pmcid, field_name="article_url")
        with self.lock:
            self._claim_queue_item(normalized_doi, queue_nonce, source_url=source_url)
            self.attempted.discard(normalized_doi)
            result = {
                **self._base_result(record, pmcid),
                "status": "released_by_user",
                "source_url": source_url,
                "resolved_url": resolved_url,
                "source_landing_page_url": article_url,
                "source_name": PMC_WEB_SOURCE_NAME,
                "source_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
                "requested_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
                "source_access": "public PMC browser session with same-origin cookies",
                "artifact_type": "article_pdf",
                "error": reason[:1000],
            }
            self._append_attempt(result, phase="browser_user_release")
            self._maybe_write_report()
        return result

    def _claim_queue_item(
        self,
        doi: str,
        queue_nonce: str | None,
        *,
        source_url: str | None,
    ) -> dict[str, Any]:
        if not isinstance(queue_nonce, str) or not queue_nonce:
            raise QueueClaimError("missing one-shot queue nonce")
        with self.lock:
            queued = self.queued_items.get(doi)
            if queued is None or not secrets.compare_digest(queue_nonce, queued["nonce"]):
                raise QueueClaimError("queue item is absent, expired, or already consumed")
            if source_url is None or source_url != queued["source_url"]:
                raise QueueClaimError("source_url does not match the queued PMC PDF URL")
            return self.queued_items.pop(doi)

    def _queue_item_matches(self, doi: str, queue_nonce: str | None) -> bool:
        if not isinstance(queue_nonce, str) or not queue_nonce:
            return False
        normalized_doi = _normalized_doi(doi)
        if normalized_doi is None:
            return False
        with self.lock:
            queued = self.queued_items.get(normalized_doi)
            return bool(
                queued and secrets.compare_digest(queue_nonce, str(queued.get("nonce") or ""))
            )

    def store_pdf(
        self,
        doi: str,
        payload: bytes,
        *,
        source_url: str,
        resolved_url: str,
        article_url: str,
        queue_nonce: str | None,
    ) -> dict[str, Any]:
        normalized_doi = _normalized_doi(doi)
        record = self.records_by_doi.get(normalized_doi)
        if record is None or normalized_doi is None:
            raise KeyError(f"DOI is not an eligible PMC author manuscript: {doi}")
        pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
        assert pmcid is not None
        _validated_pmc_pdf_url(source_url, pmcid, field_name="source_url")
        resolved_url = _validated_pmc_pdf_url(resolved_url, pmcid, field_name="resolved_url")
        article_url = _validated_article_url(article_url, pmcid, field_name="article_url")
        queued = self._claim_queue_item(
            normalized_doi,
            queue_nonce,
            source_url=source_url,
        )
        version_assertion = queued["version_assertion"]
        source_page_payload = queued["source_page_payload"]
        article_xml_payload = queued["article_xml_payload"]
        if isinstance(source_page_payload, bytes) == isinstance(article_xml_payload, bytes):
            raise TypeError("queued PMC PDF-link evidence is invalid")
        if source_page_payload is not None and not isinstance(source_page_payload, bytes):
            raise TypeError("queued PMC source-page evidence is invalid")
        if article_xml_payload is not None and not isinstance(article_xml_payload, bytes):
            raise TypeError("queued PMC article-XML evidence is invalid")
        if len(payload) > MAX_PDF_BYTES:
            raise ValueError("PDF exceeds the 120 MiB safety limit")
        if b"%PDF" not in payload[:1024]:
            raise ValueError("browser response does not contain a PDF header")

        variant_dir = _safe_variant_directory(self.layout, record, create=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=variant_dir, suffix=".pdf.part", delete=False
            ) as handle:
                handle.write(payload)
                temp_path = Path(handle.name)
            verification = verifier._verify_uploaded_pdf(temp_path, record)
            digest = sha256_file(temp_path)
            destination, metadata_path = _archived_paths(self.layout, record, digest)
            if destination.is_symlink() or metadata_path.is_symlink():
                raise ValueError("PMC web PDF and manifest destinations may not be symlinks")

            if destination.exists() and metadata_path.exists():
                try:
                    existing_metadata, existing_verification = _validate_archived_pair(
                        self.layout, record, destination, metadata_path
                    )
                except Exception:  # noqa: BLE001, S110 - repair after preserving metadata
                    pass
                else:
                    temp_path.unlink()
                    temp_path = None
                    result = {
                        **self._base_result(record, pmcid),
                        **existing_metadata,
                        **existing_verification,
                        "status": "already_present",
                    }
                    with self.lock:
                        self.results[normalized_doi] = result
                        self._append_attempt(result, phase="browser_pdf_upload")
                        self._maybe_write_report()
                    return result

            if destination.exists() and sha256_file(destination) != digest:
                raise ValueError("content-addressed PDF path contains different bytes")
            preserved_invalid_metadata = _preserve_invalid_metadata(metadata_path)
            if not destination.exists():
                temp_path.replace(destination)
                temp_path = None
            else:
                temp_path.unlink()
                temp_path = None

            pmc_manifest = self.manifests_by_doi[normalized_doi]
            metadata = build_pdf_manifest(
                self.layout,
                record,
                variant_dir=variant_dir,
                normalized_doi=normalized_doi,
                pmcid=pmcid,
                source_url=source_url,
                resolved_url=resolved_url,
                article_url=article_url,
                version_assertion=version_assertion,
                source_page_payload=source_page_payload,
                article_xml_payload=article_xml_payload,
                pmc_manifest=pmc_manifest,
                pmc_manifest_bytes=(
                    self.pmc_manifest_bytes_by_doi[normalized_doi]
                    if pmc_manifest is not None
                    else None
                ),
                pmc_package_metadata_bytes=(
                    self.pmc_package_metadata_bytes_by_doi[normalized_doi]
                    if pmc_manifest is not None
                    else None
                ),
                pmc_article_xml_original_path=(
                    self.pmc_article_xml_paths_by_doi[normalized_doi]
                    if pmc_manifest is not None
                    else None
                ),
                explicit_license=(_explicit_license(pmc_manifest) if pmc_manifest else None),
                original_pmc_manifest_path=(
                    _safe_paper_directory(self.layout, record) / "pmc.json"
                ),
                destination=destination,
                digest=digest,
                preserved_invalid_metadata=preserved_invalid_metadata,
                verification=verification,
            )
            write_json(metadata_path, metadata)
            result = {**self._base_result(record, pmcid), **metadata}
            with self.lock:
                self.results[normalized_doi] = result
                self._append_attempt(result, phase="browser_pdf_upload")
                self._maybe_write_report()
            return result
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def record_failure(
        self,
        doi: str,
        *,
        source_url: str | None,
        resolved_url: str | None,
        article_url: str | None,
        error: str,
        phase: str = "browser_pdf_fetch",
        status: str = "failed",
        version_established: bool = True,
        queue_nonce: str | None = None,
        require_queued: bool = True,
    ) -> dict[str, Any]:
        normalized_doi = _normalized_doi(doi)
        record = self.records_by_doi.get(normalized_doi)
        if record is None or normalized_doi is None:
            raise KeyError(f"DOI is not an eligible PMC author manuscript: {doi}")
        pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
        assert pmcid is not None
        if require_queued:
            self._claim_queue_item(
                normalized_doi,
                queue_nonce,
                source_url=source_url,
            )
        url_errors: list[str] = []
        if source_url is not None:
            try:
                source_url = _validated_pmc_pdf_url(source_url, pmcid, field_name="source_url")
            except ValueError as url_error:
                source_url = None
                url_errors.append(str(url_error))
        if resolved_url is not None:
            try:
                resolved_url = _validated_pmc_pdf_url(
                    resolved_url, pmcid, field_name="resolved_url"
                )
            except ValueError as url_error:
                resolved_url = None
                url_errors.append(str(url_error))
        if article_url is not None:
            try:
                article_url = _validated_article_url(article_url, pmcid, field_name="article_url")
            except ValueError as url_error:
                article_url = None
                url_errors.append(str(url_error))
        result: dict[str, Any] = {
            **self._base_result(record, pmcid),
            "status": status,
            "source_url": source_url,
            "resolved_url": resolved_url,
            "source_landing_page_url": article_url,
            "source_name": PMC_WEB_SOURCE_NAME,
            "requested_version": PMC_AUTHOR_MANUSCRIPT_VERSION,
            "source_access": "public PMC browser session with same-origin cookies",
            "artifact_type": "article_pdf",
            "error": "; ".join([*url_errors, error])[:1000],
        }
        if version_established:
            result["source_version"] = PMC_AUTHOR_MANUSCRIPT_VERSION
        with self.lock:
            existing = self.results.get(normalized_doi)
            if existing is not None and existing.get("status") in {
                "downloaded",
                "already_present",
            }:
                self._append_attempt(
                    {**result, "preserved_existing_status": existing["status"]},
                    phase=phase,
                )
                return existing
            self.results[normalized_doi] = result
            self._append_attempt(result, phase=phase)
            self._maybe_write_report()
        return result

    def status(self) -> dict[str, Any]:
        with self.lock:
            counts = Counter(
                str(result.get("status") or "unknown") for result in self.results.values()
            )
            acquired = sum(counts[status] for status in ("downloaded", "already_present"))
            challenge_retry_seconds = max(
                0,
                int(self.challenge_retry_not_before - time.monotonic()) + 1,
            )
            return {
                "eligible_author_manuscripts": len(self.records),
                "manifest_backed_xml_discovery": len(self.pmc_article_xml_links_by_doi),
                "live_html_discovery_required": (
                    len(self.records) - len(self.pmc_article_xml_links_by_doi)
                ),
                "attempted_this_session": len(self.attempted),
                "results_recorded": len(self.results),
                "papers_with_pmc_web_pdf": acquired,
                "pending_this_session": sum(
                    _normalized_doi(record.get("doi")) not in self.results
                    and _normalized_doi(record.get("doi")) not in self.attempted
                    for record in self.records
                ),
                "status_counts": dict(counts),
                "eligibility_counts": dict(self.eligibility_counts),
                "invalid_existing_variants": len(self.invalid_existing_variants),
                "challenge_retry_after_seconds": challenge_retry_seconds,
            }

    def write_report(self) -> dict[str, Any]:
        with self.lock:
            rows = sorted(
                self.results.values(),
                key=lambda row: (int(row["year"]), str(row["doi"])),
            )
            csv_rows = [{key: _csv_safe_value(value) for key, value in row.items()} for row in rows]
            write_csv(
                self.layout.reports / "pmc_web_acquisition.csv",
                csv_rows,
                REPORT_FIELDS,
            )
            summary = {
                "generated_at": utc_now(),
                **self.status(),
                "browser_origin": PMC_ORIGIN,
                "source_name": PMC_WEB_SOURCE_NAME,
                "report_path": "data/reports/pmc_web_acquisition.csv",
                "attempt_log_path": "data/reports/pmc_web_attempts.jsonl",
                "attempt_history_records": self.attempt_history_records,
                "attempt_history_status_counts": dict(self.attempt_history_counts),
                "limitation": (
                    "The fallback stores a current public PMC author manuscript. It does "
                    "not assert that this file is the edition used by the historical review."
                ),
                "security": (
                    "The bridge accepts requests only with its ephemeral token, emits CORS "
                    "permission only for the HTTPS PMC origin, and receives no PMC cookies "
                    "or PMC credentials."
                ),
            }
            write_json(self.layout.reports / "pmc_web_acquisition.json", summary)
            return summary

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
