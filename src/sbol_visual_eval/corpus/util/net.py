"""HTTP helpers with bounded retries and checksum-aware downloads."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from .storage import atomic_write_bytes, md5_file


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                response.raise_for_status()
            return response
        except (httpx.HTTPError, OSError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def download_to_path(
    client: httpx.Client,
    url: str,
    destination: Path,
    *,
    expected_md5: str | None = None,
    force: bool = False,
) -> None:
    if (
        destination.exists()
        and not force
        and (expected_md5 is None or md5_file(destination) == expected_md5)
    ):
        return
    response = request_with_retry(client, "GET", url, follow_redirects=True, timeout=90)
    response.raise_for_status()
    atomic_write_bytes(destination, response.content)
    if expected_md5 and md5_file(destination) != expected_md5:
        destination.unlink(missing_ok=True)
        raise ValueError(f"checksum mismatch for {destination.name}")
