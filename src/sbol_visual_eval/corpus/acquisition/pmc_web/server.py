"""The bounded loopback HTTP server that exposes the bridge to the browser runner."""

from __future__ import annotations

import json
import re
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...layout import Layout
from .bridge import MAX_PDF_BYTES, PmcWebBridge
from .errors import PmcTransientChallengeError, QueueClaimError
from .urls import PMC_ORIGIN

MAX_FAILURE_BYTES = 64 * 1024
BODY_READ_TIMEOUT_SECONDS = 60
CONNECTION_DEADLINE_SECONDS = 60
MAX_BRIDGE_WORKERS = 4


def _handler_class(bridge: PmcWebBridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SBOLPmcWebBridge/0.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(BODY_READ_TIMEOUT_SECONDS)
            self._deadline_timer = threading.Timer(
                CONNECTION_DEADLINE_SECONDS,
                self._expire_connection,
            )
            self._deadline_timer.daemon = True
            self._deadline_timer.start()

        def _expire_connection(self) -> None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self) -> None:
            self._deadline_timer.cancel()
            super().finish()

        def end_headers(self) -> None:
            origin = self.headers.get("Origin")
            if origin == PMC_ORIGIN:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, X-SBOL-Bridge-Token, X-SBOL-Queue-Nonce",
            )
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def _json_response(
            self,
            status: int,
            payload: Any,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _params(self) -> dict[str, list[str]]:
            return parse_qs(urlparse(self.path).query)

        def _token(self) -> str | None:
            return self.headers.get("X-SBOL-Bridge-Token")

        def _origin_allowed(self) -> bool:
            origin = self.headers.get("Origin")
            return origin is None or origin == PMC_ORIGIN

        def do_OPTIONS(self) -> None:
            if self.headers.get("Origin") != PMC_ORIGIN:
                self._json_response(HTTPStatus.FORBIDDEN, {"error": "invalid origin"})
                return
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def do_GET(self) -> None:
            if not self._origin_allowed() or not bridge._authorized(self._token()):
                self._json_response(HTTPStatus.FORBIDDEN, {"error": "invalid token or origin"})
                return
            parsed = urlparse(self.path)
            if parsed.path == "/next":
                raw_limit = self._params().get("limit", ["1"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": "invalid limit"})
                    return
                try:
                    items = bridge.next_items(limit)
                except PmcTransientChallengeError as error:
                    self._json_response(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "error": "pmc_discovery_challenge",
                            "retryable": True,
                            "requires_manual_restart": True,
                            "retry_after_seconds": error.retry_after_seconds,
                        },
                        headers={"Retry-After": str(error.retry_after_seconds)},
                    )
                    return
                self._json_response(HTTPStatus.OK, {"items": items})
            elif parsed.path == "/status":
                self._json_response(HTTPStatus.OK, bridge.status())
            else:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if self.headers.get("Origin") != PMC_ORIGIN or not bridge._authorized(self._token()):
                self._json_response(HTTPStatus.FORBIDDEN, {"error": "invalid token or origin"})
                return
            parsed = urlparse(self.path)
            if parsed.path not in {"/upload", "/failure", "/transient", "/release"}:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            params = self._params()
            doi = params.get("doi", [None])[0]
            if not doi:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": "missing DOI"})
                return
            queue_nonce = self.headers.get("X-SBOL-Queue-Nonce")
            if not bridge._queue_item_matches(doi, queue_nonce):
                self._json_response(
                    HTTPStatus.CONFLICT,
                    {"error": "queue item is absent, expired, or already consumed"},
                )
                return
            try:
                raw_length = self.headers.get("Content-Length")
                if raw_length is None:
                    self._json_response(
                        HTTPStatus.LENGTH_REQUIRED,
                        {"error": "Content-Length is required"},
                    )
                    return
                length = int(raw_length)
                maximum_length = MAX_PDF_BYTES if parsed.path == "/upload" else MAX_FAILURE_BYTES
                if length < 0 or length > maximum_length:
                    raise ValueError("request exceeds the bridge endpoint safety limit")
                payload = self.rfile.read(length)
                if len(payload) != length:
                    raise ValueError("request body ended before Content-Length bytes were read")
                if parsed.path == "/upload":
                    result = bridge.store_pdf(
                        doi,
                        payload,
                        source_url=params.get("source_url", [""])[0],
                        resolved_url=params.get("resolved_url", [""])[0],
                        article_url=params.get("article_url", [""])[0],
                        queue_nonce=queue_nonce,
                    )
                elif parsed.path == "/failure":
                    failure = json.loads(payload or b"{}")
                    if not isinstance(failure, dict):
                        raise TypeError("failure body must be a JSON object")
                    result = bridge.record_failure(
                        doi,
                        source_url=failure.get("source_url"),
                        resolved_url=failure.get("resolved_url"),
                        article_url=failure.get("article_url"),
                        error=str(failure.get("error") or "browser fetch failed"),
                        queue_nonce=queue_nonce,
                    )
                elif parsed.path == "/transient":
                    transient = json.loads(payload or b"{}")
                    if not isinstance(transient, dict):
                        raise TypeError("transient body must be a JSON object")
                    result = bridge.record_transient_challenge(
                        doi,
                        source_url=str(transient.get("source_url") or ""),
                        resolved_url=transient.get("resolved_url"),
                        article_url=str(transient.get("article_url") or ""),
                        error=str(transient.get("error") or "PMC access challenge"),
                        queue_nonce=queue_nonce,
                    )
                else:
                    release = json.loads(payload or b"{}")
                    if not isinstance(release, dict):
                        raise TypeError("release body must be a JSON object")
                    result = bridge.release_queue_item(
                        doi,
                        source_url=str(release.get("source_url") or ""),
                        resolved_url=release.get("resolved_url"),
                        article_url=str(release.get("article_url") or ""),
                        reason=str(release.get("reason") or "browser runner stopped by user"),
                        queue_nonce=queue_nonce,
                    )
            except QueueClaimError as error:
                self._json_response(HTTPStatus.CONFLICT, {"error": str(error)})
                return
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                if parsed.path not in {"/transient", "/release"}:
                    try:
                        require_queued = bridge._queue_item_matches(doi, queue_nonce)
                        bridge.record_failure(
                            doi,
                            source_url=params.get("source_url", [None])[0],
                            resolved_url=params.get("resolved_url", [None])[0],
                            article_url=params.get("article_url", [None])[0],
                            error=f"{type(error).__name__}: {error}",
                            phase="bridge_storage",
                            queue_nonce=queue_nonce,
                            require_queued=require_queued,
                        )
                    except (KeyError, QueueClaimError):
                        pass
                self._json_response(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "invalid_request"},
                )
                return
            except Exception as error:  # noqa: BLE001 - isolate one malformed PDF
                if parsed.path not in {"/transient", "/release"}:
                    try:
                        require_queued = bridge._queue_item_matches(doi, queue_nonce)
                        bridge.record_failure(
                            doi,
                            source_url=params.get("source_url", [None])[0],
                            resolved_url=params.get("resolved_url", [None])[0],
                            article_url=params.get("article_url", [None])[0],
                            error=f"{type(error).__name__}: {error}",
                            phase="bridge_storage",
                            queue_nonce=queue_nonce,
                            require_queued=require_queued,
                        )
                    except KeyError:
                        pass
                self._json_response(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "pdf_processing_failed"},
                )
                return
            self._json_response(HTTPStatus.OK, result)

        def log_message(self, format: str, *args: Any) -> None:
            rendered = format % args
            status_match = re.search(r"\s([45]\d\d)\s", rendered)
            if status_match:
                super().log_message(
                    "%s %s -> %s",
                    self.command,
                    urlparse(self.path).path,
                    status_match.group(1),
                )

    return Handler


class _BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Serve a small bounded number of loopback requests with absolute deadlines."""

    daemon_threads = True
    block_on_close = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._worker_slots = threading.BoundedSemaphore(MAX_BRIDGE_WORKERS)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def serve_pmc_web_bridge(
    layout: Layout,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    """Serve the browser byte bridge without attempting to solve access challenges."""

    if host != "127.0.0.1":
        raise ValueError("the PMC browser bridge may bind only to 127.0.0.1")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    bridge = PmcWebBridge(layout)
    server = _BoundedThreadingHTTPServer((host, port), _handler_class(bridge))
    print(f"PMC browser bridge listening on http://{host}:{port}", flush=True)
    print(f"Bridge token: {bridge.token}", flush=True)
    print(
        "Open a public article at https://pmc.ncbi.nlm.nih.gov, then run "
        "scripts/pmc_browser_runner.js with this token. The runner does not solve CAPTCHAs.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.write_report()
        bridge.close()
        server.server_close()
