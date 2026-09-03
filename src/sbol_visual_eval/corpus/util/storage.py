"""Hashing, serialization, and atomic file IO for corpus artifacts."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_sha256(payload: Any) -> str:
    serialized = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def file_digests(path: Path) -> tuple[str, str, int]:
    md5_digest = hashlib.md5()
    sha256_digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            md5_digest.update(block)
            sha256_digest.update(block)
    return md5_digest.hexdigest(), sha256_digest.hexdigest(), size


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(payload)
    temp_path.replace(path)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )


def serialize_jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    text = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
    )
    return text.encode()


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    atomic_write_bytes(path, serialize_jsonl(records))


def write_gzip_json(path: Path, payload: Any) -> None:
    serialized = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    atomic_write_bytes(path, gzip.compress(serialized, compresslevel=9, mtime=0))


def serialize_csv(records: Sequence[dict[str, Any]], fieldnames: Sequence[str]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(records)
    return handle.getvalue().encode()


def write_csv(path: Path, records: Sequence[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    atomic_write_bytes(path, serialize_csv(records, fieldnames))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_gzip_json(path: Path) -> dict[str, Any]:
    with gzip.open(path, mode="rt", encoding="utf-8") as handle:
        return json.load(handle)
