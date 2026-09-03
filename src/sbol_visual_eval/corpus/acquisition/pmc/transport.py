"""HTTP retry policy for NCBI services, honoring Retry-After on throttling."""

from __future__ import annotations

import time
from typing import Any

import httpx

REQUEST_ATTEMPTS = 4


def _request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = REQUEST_ATTEMPTS,
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
            if attempt + 1 == attempts:
                break
            retry_after: float | None = None
            if isinstance(error, httpx.HTTPStatusError):
                raw_retry_after = error.response.headers.get("Retry-After")
                try:
                    retry_after = float(raw_retry_after) if raw_retry_after else None
                except ValueError:
                    retry_after = None
            time.sleep(retry_after if retry_after is not None else 2**attempt)
    assert last_error is not None
    raise last_error
