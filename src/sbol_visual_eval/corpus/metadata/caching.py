"""On-disk caching of upstream metadata API responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _cache_payload(path: Path, *, offline: bool, refresh: bool) -> dict[str, Any] | None:
    if path.exists() and (offline or not refresh):
        return json.loads(path.read_text(encoding="utf-8"))
    if offline:
        raise FileNotFoundError(f"offline mode requested, but cache is missing: {path}")
    return None
