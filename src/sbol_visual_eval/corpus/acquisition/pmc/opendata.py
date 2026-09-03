"""Objects and metadata from the official PMC Article Datasets S3 bucket."""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from ...util.storage import file_digests
from .ids import _version_number
from .transport import REQUEST_ATTEMPTS, _request_with_retry

PMC_BUCKET = "pmc-oa-opendata"
PMC_BUCKET_HOST = f"{PMC_BUCKET}.s3.amazonaws.com"
PMC_BUCKET_URL = f"https://{PMC_BUCKET_HOST}"
PMC_DATASET_DOCUMENTATION = "https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/"
PMC_DATASET_README = f"{PMC_BUCKET_URL}/README.txt"
PMC_AUTHOR_MANUSCRIPT_DOCUMENTATION = "https://pmc.ncbi.nlm.nih.gov/tools/amdataset/"

IMAGE_SUFFIXES = {
    ".avif",
    ".bmp",
    ".gif",
    ".jp2",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}


def _discover_latest_version(client: httpx.Client, pmcid: str) -> str:
    response = _request_with_retry(
        client,
        "GET",
        f"{PMC_BUCKET_URL}/",
        params={"list-type": "2", "prefix": f"{pmcid}.", "delimiter": "/"},
        follow_redirects=True,
        timeout=60,
    )
    response.raise_for_status()
    root = ElementTree.fromstring(response.content)
    candidates: list[tuple[int, str]] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Prefix" or not element.text:
            continue
        versioned = element.text.rstrip("/").upper()
        version = _version_number(versioned)
        if version is not None:
            candidates.append((version, versioned))
    if not candidates:
        raise FileNotFoundError(f"no PMC AWS article version found for {pmcid}")
    return max(candidates)[1]


def _metadata_url(versioned_pmcid: str) -> str:
    return f"{PMC_BUCKET_URL}/metadata/{quote(versioned_pmcid, safe='')}.json"


def _s3_https_url(source_url: str) -> tuple[str, str | None, str]:
    parsed = urlsplit(source_url)
    if parsed.scheme != "s3" or parsed.netloc != PMC_BUCKET:
        raise ValueError(f"unexpected PMC object URL: {source_url}")
    key = unquote(parsed.path.lstrip("/"))
    if not key or key.endswith("/"):
        raise ValueError(f"PMC object URL has no file key: {source_url}")
    filename = Path(key).name
    if filename in {"", ".", ".."}:
        raise ValueError(f"PMC object URL has an unsafe filename: {source_url}")
    query = parse_qs(parsed.query)
    expected_md5 = (query.get("md5") or [None])[0]
    if expected_md5 and not re.fullmatch(r"[0-9a-fA-F]{32}", expected_md5):
        raise ValueError(f"PMC object URL has an invalid MD5 digest: {source_url}")
    https_url = urlunsplit(("https", PMC_BUCKET_HOST, "/" + quote(key, safe="/"), parsed.query, ""))
    return https_url, expected_md5.casefold() if expected_md5 else None, filename


def _download_asset(
    client: httpx.Client,
    *,
    source_url: str,
    destination: Path,
    role: str,
) -> dict[str, Any]:
    https_url, expected_md5, filename = _s3_https_url(source_url)
    if destination.name != filename and role != "article_pdf":
        raise ValueError("media destination does not preserve the PMC object filename")

    if destination.exists() and expected_md5:
        actual_md5, actual_sha256, size = file_digests(destination)
        if actual_md5 == expected_md5:
            return {
                "role": role,
                "status": "already_present",
                "source_url": source_url,
                "download_url": https_url,
                "local_path": str(destination),
                "bytes": size,
                "md5": actual_md5,
                "sha256": actual_sha256,
                "content_type": None,
                "is_image_asset": destination.suffix.casefold() in IMAGE_SUFFIXES,
            }

    destination.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        temp_path: Path | None = None
        try:
            md5_digest = hashlib.md5()
            sha256_digest = hashlib.sha256()
            size = 0
            content_type: str | None = None
            with client.stream(
                "GET",
                https_url,
                follow_redirects=True,
                timeout=180,
            ) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                content_type = response.headers.get("Content-Type")
                with tempfile.NamedTemporaryFile(
                    dir=destination.parent, suffix=".part", delete=False
                ) as handle:
                    temp_path = Path(handle.name)
                    for block in response.iter_bytes(chunk_size=1024 * 1024):
                        handle.write(block)
                        size += len(block)
                        md5_digest.update(block)
                        sha256_digest.update(block)
            actual_md5 = md5_digest.hexdigest()
            if expected_md5 and actual_md5 != expected_md5:
                raise ValueError(
                    f"MD5 mismatch for {filename}: expected {expected_md5}, found {actual_md5}"
                )
            if role == "article_pdf":
                with temp_path.open("rb") as handle:
                    if b"%PDF" not in handle.read(1024):
                        raise ValueError("article PDF response does not contain a PDF header")
            temp_path.replace(destination)
            return {
                "role": role,
                "status": "downloaded",
                "source_url": source_url,
                "download_url": https_url,
                "local_path": str(destination),
                "bytes": size,
                "md5": actual_md5,
                "sha256": sha256_digest.hexdigest(),
                "content_type": content_type,
                "is_image_asset": destination.suffix.casefold() in IMAGE_SUFFIXES,
            }
        except (httpx.HTTPError, OSError, ValueError) as error:
            last_error = error
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(2**attempt)
    assert last_error is not None
    return {
        "role": role,
        "status": "failed",
        "source_url": source_url,
        "download_url": https_url,
        "local_path": str(destination),
        "error": f"{type(last_error).__name__}: {last_error}",
        "is_image_asset": destination.suffix.casefold() in IMAGE_SUFFIXES,
    }


def _article_pdf_destination(paper_dir: Path, pdf_url: str) -> Path:
    _, expected_md5, _ = _s3_https_url(pdf_url)
    primary = paper_dir / "paper.pdf"
    if not primary.exists():
        return primary
    if expected_md5 and file_digests(primary)[0] == expected_md5:
        return primary
    return paper_dir / "pmc" / "paper.pdf"
