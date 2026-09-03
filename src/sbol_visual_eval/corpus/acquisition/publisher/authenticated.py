"""Paced, bounded VOR downloads through an already authenticated EZProxy session."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from curl_cffi import requests as curl_requests

from ...layout import Layout
from ...util.storage import write_json
from .artifacts import MAX_PDF_BYTES
from .bridge import PublisherBridge
from .urls import PUBLISHER_ORIGIN, _validated_publisher_url

EZPROXY_COOKIE_ENV = "SBOL_EZPROXY_COOKIE"
SSL_COM_ROOT_NAME = "SSL.com TLS RSA Root CA 2022"


@contextmanager
def _ezproxy_ca_bundle(explicit_path: Path | None = None) -> Iterator[str | bool]:
    if explicit_path is not None:
        resolved = explicit_path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"CA bundle does not exist: {resolved}")
        yield str(resolved)
        return
    if sys.platform != "darwin":
        yield True
        return
    security = Path("/usr/bin/security")
    keychain = Path("/System/Library/Keychains/SystemRootCertificates.keychain")
    try:
        certificate = subprocess.run(
            [
                str(security),
                "find-certificate",
                "-p",
                "-c",
                SSL_COM_ROOT_NAME,
                str(keychain),
            ],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(
            "could not export the EZProxy root certificate; pass --ca-bundle"
        ) from error
    if b"BEGIN CERTIFICATE" not in certificate:
        raise RuntimeError("the exported EZProxy root certificate is not PEM")
    with tempfile.TemporaryDirectory(prefix="sbol-ezproxy-ca-") as directory:
        path = Path(directory) / "root.pem"
        path.write_bytes(certificate)
        yield str(path)


def _retry_after_seconds(response: Any, attempt: int) -> float:
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return min(max(float(raw), 1.0), 300.0)
        except ValueError:
            pass
    return min(60.0 * attempt, 300.0)


def _successful_non_pdf_error(response: Any, content: bytes) -> str:
    normalized = content[: 64 * 1024].lower()
    suspended = b"suspended account" in normalized or (
        b"off-campus account" in normalized and b"has lost access" in normalized
    )
    if suspended:
        return (
            "institutional EZProxy access suspended; ACS PDF endpoint returned the "
            "CU Libraries suspension page"
        )
    return (
        f"publisher access challenge: HTTP {response.status_code} ACS PDF endpoint "
        f"returned non-PDF content-type={response.headers.get('content-type')}; "
        f"signature={content[:5]!r}"
    )


def _fetch_authenticated_item(
    session: Any,
    item: dict[str, str],
    *,
    cookie_value: str,
    verify: str | bool,
    max_retries: int,
) -> dict[str, Any]:
    try:
        _validated_publisher_url(item.get("url"), field_name="source_url")
    except ValueError as error:
        return {
            "kind": "failure",
            "item": item,
            "resolved_url": None,
            "error": str(error),
            "fatal_access": True,
        }
    response = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(
                item["url"],
                cookies={"ezproxy": cookie_value},
                allow_redirects=True,
                timeout=60,
                verify=verify,
            )
        except Exception as error:  # noqa: BLE001 - one retryable network request
            if attempt < max_retries:
                time.sleep(min(2**attempt, 30))
                continue
            return {
                "kind": "failure",
                "item": item,
                "resolved_url": None,
                "error": f"{type(error).__name__}: {error}",
                "fatal_access": True,
            }
        try:
            resolved_url = _validated_publisher_url(str(response.url), field_name="resolved_url")
        except ValueError as error:
            return {
                "kind": "failure",
                "item": item,
                "resolved_url": None,
                "error": f"publisher redirect rejected: {error}",
                "fatal_access": True,
            }
        if response.status_code == 429:
            wait_seconds = _retry_after_seconds(response, attempt)
            print(
                f"Publisher throttled on {item['doi']}; waiting "
                f"{wait_seconds:.0f}s (attempt {attempt}/{max_retries})",
                flush=True,
            )
            time.sleep(wait_seconds)
            continue
        if response.status_code in {401, 403}:
            return {
                "kind": "failure",
                "item": item,
                "resolved_url": resolved_url,
                "error": f"publisher HTTP {response.status_code}",
                "fatal_access": True,
            }
        content = bytes(response.content)
        if 200 <= response.status_code < 300 and not content.startswith(b"%PDF"):
            return {
                "kind": "failure",
                "item": item,
                "resolved_url": resolved_url,
                "error": _successful_non_pdf_error(response, content),
                "fatal_access": True,
            }
        if len(content) > MAX_PDF_BYTES:
            return {
                "kind": "failure",
                "item": item,
                "resolved_url": resolved_url,
                "error": "PDF exceeds the 120 MiB safety limit",
                "fatal_access": False,
            }
        if not response.ok or not content.startswith(b"%PDF"):
            return {
                "kind": "failure",
                "item": item,
                "resolved_url": resolved_url,
                "error": (
                    f"publisher HTTP {response.status_code}; "
                    f"content-type={response.headers.get('content-type')}; "
                    f"signature={content[:5]!r}"
                ),
                "fatal_access": False,
            }
        return {
            "kind": "success",
            "item": item,
            "resolved_url": resolved_url,
            "content": content,
            "fatal_access": False,
        }
    return {
        "kind": "failure",
        "item": item,
        "resolved_url": (
            _validated_publisher_url(str(response.url), field_name="resolved_url", allow_none=True)
            if response is not None
            else None
        ),
        "error": f"publisher remained throttled after {max_retries} attempts",
        "fatal_access": False,
    }


def acquire_publisher_authenticated(
    layout: Layout,
    *,
    cookie: str | None = None,
    delay_seconds: float = 1.0,
    max_retries: int = 5,
    workers: int = 1,
    limit: int | None = None,
    ca_bundle: Path | None = None,
    browser_origin: str = PUBLISHER_ORIGIN,
    impersonate: str = "chrome136",
) -> dict[str, Any]:
    """Download ACS VOR PDFs through an already authenticated EZProxy session."""

    cookie_value = cookie or os.environ.get(EZPROXY_COOKIE_ENV)
    if not cookie_value:
        raise ValueError(f"set {EZPROXY_COOKIE_ENV} to the value of the browser's ezproxy cookie")
    if cookie_value.startswith("ezproxy="):
        cookie_value = cookie_value.split("=", 1)[1]
    if not cookie_value or any(character in cookie_value for character in "\r\n;"):
        raise ValueError("the EZProxy cookie value is malformed")
    if delay_seconds < 0:
        raise ValueError("delay_seconds must not be negative")
    if max_retries < 1:
        raise ValueError("max_retries must be at least one")
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between one and eight")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least one")

    bridge = PublisherBridge(layout, browser_origin=browser_origin)
    attempted_new = 0
    consecutive_access_failures = 0
    stopped_reason = None
    sessions = [curl_requests.Session(impersonate=impersonate) for _ in range(workers)]
    try:
        with (
            _ezproxy_ca_bundle(ca_bundle) as verify,
            ThreadPoolExecutor(max_workers=workers) as executor,
        ):
            while limit is None or attempted_new < limit:
                batch_size = workers
                if limit is not None:
                    batch_size = min(batch_size, limit - attempted_new)
                items = bridge.next_items(batch_size)
                if not items:
                    break
                attempted_new += len(items)
                futures = {
                    executor.submit(
                        _fetch_authenticated_item,
                        sessions[index],
                        item,
                        cookie_value=cookie_value,
                        verify=verify,
                        max_retries=max_retries,
                    ): item
                    for index, item in enumerate(items)
                }
                for future in as_completed(futures):
                    outcome = future.result()
                    item = outcome["item"]
                    if outcome["kind"] == "success":
                        try:
                            bridge.store_pdf(
                                item["doi"],
                                outcome["content"],
                                source_url=item["url"],
                                resolved_url=outcome["resolved_url"],
                                source_access=(
                                    "CU Boulder EZproxy authenticated session (curl-cffi)"
                                ),
                            )
                        except Exception as error:  # noqa: BLE001 - one bad paper
                            bridge.record_failure(
                                item["doi"],
                                source_url=item["url"],
                                resolved_url=outcome["resolved_url"],
                                error=f"{type(error).__name__}: {error}",
                            )
                        consecutive_access_failures = 0
                    else:
                        bridge.record_failure(
                            item["doi"],
                            source_url=item["url"],
                            resolved_url=outcome["resolved_url"],
                            error=outcome["error"],
                        )
                        if outcome["fatal_access"]:
                            consecutive_access_failures += 1
                        else:
                            consecutive_access_failures = 0
                if consecutive_access_failures >= 3:
                    stopped_reason = "authentication_or_access_failure"
                    break
                if attempted_new % 25 < workers:
                    current = bridge.status()
                    print(
                        f"Authenticated publisher acquisition {attempted_new:,} new attempts; "
                        f"{current['papers_with_publisher_pdf']:,}/{current['papers_total']:,} "
                        "publisher PDFs present",
                        flush=True,
                    )
                if delay_seconds:
                    time.sleep(delay_seconds)
    except KeyboardInterrupt:
        stopped_reason = "interrupted"
        print("Authenticated publisher acquisition interrupted; progress is preserved", flush=True)
    finally:
        for session in sessions:
            session.close()
        summary = bridge.write_report()
    summary["new_records_attempted"] = attempted_new
    summary["stopped_reason"] = stopped_reason or "complete_or_limit"
    write_json(layout.reports / "publisher_acquisition.json", summary)
    return summary
