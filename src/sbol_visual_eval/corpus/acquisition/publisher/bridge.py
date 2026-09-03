"""The loopback publisher bridge and its browser-facing HTTP server."""

from __future__ import annotations

import csv
import json
import re
import secrets
import tempfile
import threading
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...layout import Layout
from ...provenance import pdf
from ...util.storage import read_jsonl, sha256_file, utc_now, write_csv, write_json
from .artifacts import (
    MAX_PDF_BYTES,
    PUBLISHER_MANIFEST_SCHEMA_VERSION,
    PUBLISHER_SOURCE,
    _archive_publisher_pair,
    _audit_replaced_archives,
    _publisher_paths,
    _validated_publisher_pair,
    _with_current_vor_provenance,
)
from .urls import (
    PUBLISHER_ORIGIN,
    _publisher_url,
    _validated_publisher_origin,
    _validated_publisher_url,
)

REPORT_FIELDS = (
    "record_id",
    "doi",
    "year",
    "title",
    "status",
    "local_path",
    "retrieved_at",
    "source_url",
    "resolved_url",
    "source_name",
    "source_version",
    "requested_version",
    "retrieved_version_scope",
    "version_assertion_method",
    "historical_evaluated_edition_relation",
    "source_access",
    "bytes",
    "sha256",
    "page_count",
    "artifact_type",
    "identity_check",
    "title_similarity",
    "error",
)


class PublisherBridge:
    """Receive authenticated publisher PDFs from a local browser session.

    The bridge binds to loopback and never receives or stores browser credentials. The
    authenticated browser fetches same-origin publisher URLs and posts only the PDF bytes
    and their public source URLs to this process.
    """

    def __init__(
        self,
        layout: Layout,
        *,
        token: str | None = None,
        browser_origin: str = PUBLISHER_ORIGIN,
    ) -> None:
        source_path = layout.processed / "papers.jsonl"
        if not source_path.exists():
            raise FileNotFoundError(
                "processed manifest is missing; run `sbol-visual-data build` first"
            )
        self.layout = layout
        self.browser_origin = _validated_publisher_origin(browser_origin)
        self.token = token or secrets.token_urlsafe(24)
        records = read_jsonl(source_path)
        self.records = [record for record in records if record.get("doi")]
        self.records_by_doi = {record["doi"].lower(): record for record in self.records}
        self.results: dict[str, dict[str, Any]] = {}
        self.invalid_existing: dict[str, str] = {}
        self.invalid_replaced_archives: list[str] = []
        self.attempted: set[str] = set()
        self.lock = threading.RLock()
        self.attempt_log_path = layout.reports / "publisher_attempts.jsonl"
        self._seed_attempt_log_from_snapshot()
        self._load_existing()
        self.write_report()

    def _seed_attempt_log_from_snapshot(self) -> None:
        snapshot_path = self.layout.reports / "publisher_acquisition.csv"
        if self.attempt_log_path.exists() or not snapshot_path.exists():
            return
        with snapshot_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return
        self.attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.attempt_log_path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(
                        {
                            **row,
                            "attempted_at": row.get("retrieved_at") or utc_now(),
                            "imported_from_snapshot": True,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )

    def _append_attempt(self, result: dict[str, Any]) -> None:
        entry = {
            **result,
            "attempted_at": utc_now(),
        }
        self.attempt_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.attempt_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _attempt_history(self) -> tuple[int, dict[str, int]]:
        if not self.attempt_log_path.exists():
            return 0, {}
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
        return total, dict(counts)

    def _load_existing(self) -> None:
        for record in self.records:
            destination, manifest_path = _publisher_paths(self.layout, record)
            if not destination.exists() and not manifest_path.exists():
                self.invalid_replaced_archives.extend(
                    _audit_replaced_archives(self.layout, record, None)
                )
                continue
            current_manifest: dict[str, Any] | None = None
            try:
                manifest, verification = _validated_publisher_pair(
                    self.layout, record, destination, manifest_path
                )
            except Exception as error:  # noqa: BLE001 - report and reacquire bad local files
                self.invalid_existing[record["doi"].lower()] = f"{type(error).__name__}: {error}"
            else:
                upgraded_manifest = _with_current_vor_provenance(manifest)
                if upgraded_manifest != manifest:
                    write_json(manifest_path, upgraded_manifest)
                    manifest = upgraded_manifest
                current_manifest = manifest
                self.results[record["doi"].lower()] = {
                    **self._base_result(record),
                    **manifest,
                    **verification,
                    "status": "already_present",
                }
            self.invalid_replaced_archives.extend(
                _audit_replaced_archives(self.layout, record, current_manifest)
            )

    @staticmethod
    def _base_result(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_id": record["record_id"],
            "doi": record.get("doi"),
            "year": record["year"],
            "title": record.get("title_crossref") or record["title_source"],
        }

    def _authorized(self, supplied_token: str | None) -> bool:
        return bool(supplied_token) and secrets.compare_digest(supplied_token, self.token)

    def next_items(self, limit: int) -> list[dict[str, str]]:
        limit = min(max(limit, 1), 8)
        with self.lock:
            items: list[dict[str, str]] = []
            for record in self.records:
                doi = record["doi"].lower()
                if doi in self.results or doi in self.attempted:
                    continue
                self.attempted.add(doi)
                items.append(
                    {
                        "doi": doi,
                        "url": _publisher_url(doi, self.browser_origin),
                    }
                )
                if len(items) == limit:
                    break
            return items

    def store_pdf(
        self,
        doi: str,
        payload: bytes,
        *,
        source_url: str,
        resolved_url: str | None,
        source_access: str = "CU Boulder EZproxy authenticated browser",
    ) -> dict[str, Any]:
        normalized_doi = doi.lower()
        record = self.records_by_doi.get(normalized_doi)
        if record is None:
            raise KeyError(f"DOI is not in this corpus: {doi}")
        if len(payload) > MAX_PDF_BYTES:
            raise ValueError("PDF exceeds the 120 MiB safety limit")
        if not payload.startswith(b"%PDF"):
            raise ValueError("browser response is not a PDF")

        validated_source_url = _validated_publisher_url(source_url, field_name="source_url")
        expected_source_url = _publisher_url(str(record["doi"]), self.browser_origin)
        if validated_source_url != expected_source_url:
            raise ValueError("source_url does not match the queued ACS DOI PDF URL")
        validated_resolved_url = _validated_publisher_url(
            resolved_url, field_name="resolved_url", allow_none=True
        )

        destination, manifest_path = _publisher_paths(self.layout, record)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, suffix=".pdf.part", delete=False
            ) as handle:
                handle.write(payload)
                temp_path = Path(handle.name)
            verification = pdf.verify_pdf(temp_path, record)
            new_hash = sha256_file(temp_path)
            replacement_info: dict[str, str] = {}
            if destination.exists() or manifest_path.exists():
                try:
                    old_manifest, old_verification = _validated_publisher_pair(
                        self.layout, record, destination, manifest_path
                    )
                except Exception:  # noqa: BLE001 - preserve and replace invalid pair
                    _archive_publisher_pair(self.layout, destination, manifest_path, "rejected")
                else:
                    if old_manifest["sha256"].casefold() == new_hash:
                        temp_path.unlink(missing_ok=True)
                        temp_path = None
                        upgraded_manifest = _with_current_vor_provenance(old_manifest)
                        if upgraded_manifest != old_manifest:
                            write_json(manifest_path, upgraded_manifest)
                        result = {
                            **self._base_result(record),
                            **upgraded_manifest,
                            **old_verification,
                            "status": "already_present",
                        }
                        with self.lock:
                            self.results[normalized_doi] = result
                            self.invalid_existing.pop(normalized_doi, None)
                            self._append_attempt(result)
                        return result
                    replacement_info = _archive_publisher_pair(
                        self.layout,
                        destination,
                        manifest_path,
                        "replaced",
                        replacement_sha256=new_hash,
                    )
                    if replacement_info is None:
                        raise RuntimeError("publisher replacement archive was not created")
            temp_path.replace(destination)
            temp_path = None
            manifest = _with_current_vor_provenance(
                {
                    "schema_version": PUBLISHER_MANIFEST_SCHEMA_VERSION,
                    "status": "downloaded",
                    "record_id": record["record_id"],
                    "doi": record["doi"],
                    "retrieved_at": utc_now(),
                    "source_name": PUBLISHER_SOURCE,
                    "source_url": validated_source_url,
                    "resolved_url": validated_resolved_url,
                    "source_version": "publishedVersion",
                    "source_access": source_access,
                    "artifact_type": "article_pdf",
                    "local_path": str(destination.relative_to(self.layout.root)),
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                    **replacement_info,
                    **verification,
                }
            )
            write_json(manifest_path, manifest)
            result = {**self._base_result(record), **manifest}
            with self.lock:
                self.results[normalized_doi] = result
                self.invalid_existing.pop(normalized_doi, None)
                self._append_attempt(result)
                if len(self.results) % 25 == 0:
                    self.write_report()
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
        error: str,
    ) -> dict[str, Any]:
        normalized_doi = doi.lower()
        record = self.records_by_doi.get(normalized_doi)
        if record is None:
            raise KeyError(f"DOI is not in this corpus: {doi}")
        url_errors: list[str] = []
        try:
            source_url = _validated_publisher_url(
                source_url, field_name="source_url", allow_none=True
            )
        except ValueError as url_error:
            source_url = None
            url_errors.append(str(url_error))
        try:
            resolved_url = _validated_publisher_url(
                resolved_url, field_name="resolved_url", allow_none=True
            )
        except ValueError as url_error:
            resolved_url = None
            url_errors.append(str(url_error))
        recorded_error = "; ".join([*url_errors, error])
        result = {
            **self._base_result(record),
            "status": "failed",
            "source_url": source_url,
            "resolved_url": resolved_url,
            "source_name": PUBLISHER_SOURCE,
            "requested_version": "publishedVersion",
            "source_access": "CU Boulder EZproxy authenticated browser",
            "artifact_type": "article_pdf",
            "error": recorded_error[:1000],
        }
        with self.lock:
            existing = self.results.get(normalized_doi)
            if existing is not None and existing.get("status") in {
                "downloaded",
                "already_present",
            }:
                self._append_attempt(
                    {
                        **result,
                        "preserved_existing_status": existing["status"],
                    }
                )
                return existing
            self.results[normalized_doi] = result
            self._append_attempt(result)
            if len(self.results) % 25 == 0:
                self.write_report()
        return result

    def status(self) -> dict[str, Any]:
        with self.lock:
            status_counts = Counter(result["status"] for result in self.results.values())
            acquired = sum(status_counts[status] for status in ("downloaded", "already_present"))
            return {
                "papers_total": len(self.records),
                "attempted_this_session": len(self.attempted),
                "results_recorded": len(self.results),
                "papers_with_publisher_pdf": acquired,
                "pending_this_session": sum(
                    record["doi"].lower() not in self.results
                    and record["doi"].lower() not in self.attempted
                    for record in self.records
                ),
                "status_counts": dict(status_counts),
                "invalid_existing_artifacts": len(self.invalid_existing),
                "invalid_replaced_archives": len(self.invalid_replaced_archives),
            }

    def write_report(self) -> dict[str, Any]:
        with self.lock:
            rows = sorted(self.results.values(), key=lambda row: (row["year"], row["doi"]))
            write_csv(
                self.layout.reports / "publisher_acquisition.csv",
                rows,
                REPORT_FIELDS,
            )
            attempt_history_records, attempt_history_counts = self._attempt_history()
            summary = {
                "generated_at": utc_now(),
                **self.status(),
                "browser_origin": self.browser_origin,
                "source_name": PUBLISHER_SOURCE,
                "report_path": "data/reports/publisher_acquisition.csv",
                "attempt_log_path": "data/reports/publisher_attempts.jsonl",
                "attempt_history_records": attempt_history_records,
                "attempt_history_status_counts": attempt_history_counts,
                "security": (
                    "Authentication cookies are read from the browser or an ephemeral "
                    "environment variable and are never written to corpus files or reports."
                ),
            }
            write_json(self.layout.reports / "publisher_acquisition.json", summary)
            return summary


def _handler_class(bridge: PublisherBridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SBOLPublisherBridge/0.1"

        def end_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin == bridge.browser_origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _json_response(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _params(self) -> dict[str, list[str]]:
            return parse_qs(urlparse(self.path).query)

        def _token(self) -> str | None:
            values = self._params().get("token")
            return values[0] if values else None

        def _browser_origin_allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == bridge.browser_origin

        def do_OPTIONS(self) -> None:
            if not self._browser_origin_allowed():
                self._json_response(HTTPStatus.FORBIDDEN, {"error": "invalid origin"})
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def do_GET(self) -> None:
            if not self._browser_origin_allowed() or not bridge._authorized(self._token()):
                self._json_response(HTTPStatus.FORBIDDEN, {"error": "invalid token"})
                return
            parsed = urlparse(self.path)
            if parsed.path == "/next":
                raw_limit = self._params().get("limit", ["1"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid limit"})
                    return
                self._json_response(HTTPStatus.OK, {"items": bridge.next_items(limit)})
            elif parsed.path == "/status":
                self._json_response(HTTPStatus.OK, bridge.status())
            else:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._browser_origin_allowed() or not bridge._authorized(self._token()):
                self._json_response(HTTPStatus.FORBIDDEN, {"error": "invalid token"})
                return
            params = self._params()
            doi = params.get("doi", [None])[0]
            if not doi:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": "missing DOI"})
                return
            parsed = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_PDF_BYTES:
                    raise ValueError("request exceeds the 120 MiB safety limit")
                payload = self.rfile.read(length)
                if parsed.path == "/upload":
                    result = bridge.store_pdf(
                        doi,
                        payload,
                        source_url=params.get("source_url", [""])[0],
                        resolved_url=params.get("resolved_url", [None])[0],
                    )
                elif parsed.path == "/failure":
                    failure = json.loads(payload or b"{}")
                    result = bridge.record_failure(
                        doi,
                        source_url=failure.get("source_url"),
                        resolved_url=failure.get("resolved_url"),
                        error=str(failure.get("error") or "browser fetch failed"),
                    )
                else:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
            except (KeyError, ValueError, json.JSONDecodeError) as error:
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": f"{type(error).__name__}: {error}"},
                )
                return
            except Exception as error:  # noqa: BLE001 - isolate one paper and report it
                try:
                    bridge.record_failure(
                        doi,
                        source_url=params.get("source_url", [None])[0],
                        resolved_url=params.get("resolved_url", [None])[0],
                        error=f"{type(error).__name__}: {error}",
                    )
                except KeyError:
                    pass
                self._json_response(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": f"{type(error).__name__}: {error}"},
                )
                return
            self._json_response(HTTPStatus.OK, result)

        def log_message(self, format: str, *args: Any) -> None:
            if args and re.search(r"\s(?:4|5)\d\d\s", format % args):
                super().log_message(format, *args)

    return Handler


def serve_publisher_bridge(
    layout: Layout,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    browser_origin: str = PUBLISHER_ORIGIN,
) -> None:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("the authenticated publisher bridge may bind only to loopback")
    bridge = PublisherBridge(layout, browser_origin=browser_origin)
    server = ThreadingHTTPServer((host, port), _handler_class(bridge))
    print(f"Publisher bridge listening on http://{host}:{port}", flush=True)
    print(f"Bridge token: {bridge.token}", flush=True)
    print(
        "Open an authenticated ACS PDF at the configured EZProxy origin, then run the "
        "browser runner against /next and /upload. Credentials never leave the browser.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.write_report()
        server.server_close()
