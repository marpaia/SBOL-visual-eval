from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from sbol_visual_eval.corpus.acquisition import downloads
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.provenance.generic import generic_manifest_identity_errors
from sbol_visual_eval.corpus.util.storage import write_json


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _DownloadResponse:
    def __init__(
        self,
        *,
        status_code: int,
        content: bytes,
        url: str,
        content_type: str,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = {"content-type": content_type}

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if self.is_success:
            return
        request = httpx.Request("GET", self.url)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=request, response=response)


def _repository_record() -> dict[str, Any]:
    return {
        "record_id": "doi:10.1234/repository-example",
        "doi": "10.1234/repository-example",
        "year": 2023,
        "title_source": "Repository example article",
        "title_crossref": "Repository example article",
        "pdf_candidates": [
            {
                "pdf_url": "https://repository.example/article.pdf",
                "landing_page_url": "https://repository.example/article",
                "source_name": "Example repository",
                "source_type": "repository",
                "version": "acceptedVersion",
                "license": None,
                "has_explicit_open_license": False,
                "metadata_source": "OpenAlex",
            }
        ],
    }


@pytest.mark.parametrize(
    ("initial_status", "initial_content", "initial_content_type", "reason"),
    [
        (404, b"not found", "text/html", "status_404"),
        (200, b"repository interstitial", "text/html", "non_pdf_response"),
    ],
)
def test_repository_candidate_retries_without_explicit_accept_and_records_provenance(
    tmp_path: Path,
    monkeypatch: Any,
    initial_status: int,
    initial_content: bytes,
    initial_content_type: str,
    reason: str,
) -> None:
    layout = Layout(tmp_path)
    record = _repository_record()
    pdf_payload = b"%PDF-1.7\nrepository article\n%%EOF\n"
    responses = iter(
        [
            _DownloadResponse(
                status_code=initial_status,
                content=initial_content,
                url="https://repository.example/resolved-initial",
                content_type=initial_content_type,
            ),
            _DownloadResponse(
                status_code=200,
                content=pdf_payload,
                url="https://repository.example/resolved-article.pdf",
                content_type="application/pdf",
            ),
        ]
    )
    calls: list[dict[str, Any]] = []

    def request(*args: Any, **kwargs: Any) -> _DownloadResponse:
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(downloads, "request_with_retry", request)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 7,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )

    result = downloads._download_one_paper(
        object(),
        layout,
        record,
        allow_license_unknown=True,
        include_publisher=False,
        force=False,
    )

    assert result["status"] == "downloaded"
    assert len(calls) == 2
    assert calls[0]["headers"] == {"Accept": "application/pdf"}
    assert "headers" not in calls[1]
    expected_retry = {
        "initial_content_type": initial_content_type,
        "initial_explicit_accept_header": "application/pdf",
        "initial_resolved_url": "https://repository.example/resolved-initial",
        "initial_status_code": initial_status,
        "reason": reason,
        "retry_omitted_explicit_accept_header": True,
        "url": "https://repository.example/article.pdf",
    }
    assert result["repository_request_retries"] == [expected_retry]
    manifest = json.loads((layout.paper_directory(record) / "metadata.json").read_text())
    assert manifest["repository_request_retries"] == [expected_retry]
    assert manifest["resolved_url"] == "https://repository.example/resolved-article.pdf"


def test_nonrepository_404_does_not_retry_without_explicit_accept(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _repository_record()
    record["pdf_candidates"][0]["source_type"] = "journal"
    calls: list[dict[str, Any]] = []

    def request(*args: Any, **kwargs: Any) -> _DownloadResponse:
        calls.append(kwargs)
        return _DownloadResponse(
            status_code=404,
            content=b"not found",
            url="https://repository.example/article.pdf",
            content_type="text/html",
        )

    monkeypatch.setattr(downloads, "request_with_retry", request)

    result = downloads._download_one_paper(
        object(),
        layout,
        record,
        allow_license_unknown=True,
        include_publisher=False,
        force=False,
    )

    assert result["status"] == "failed_all_candidates"
    assert len(calls) == 1
    assert calls[0]["headers"] == {"Accept": "application/pdf"}
    assert "repository_request_retries" not in result["attempts"][0]


def test_download_emits_bound_generic_manifest_and_rewrites_archive_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = {
        "record_id": "doi:10.1234/example",
        "doi": "10.1234/example",
        "year": 2023,
        "title_source": "Example article",
        "title_crossref": "Example article",
        "pdf_candidates": [
            {
                "pdf_url": "https://example.test/published.pdf",
                "landing_page_url": "https://example.test/article",
                "source_name": "Example publisher",
                "source_type": "publisher",
                "version": "publishedVersion",
                "license": "cc-by",
                "has_explicit_open_license": True,
                "metadata_source": "Crossref",
            }
        ],
    }
    paper_dir = layout.paper_directory(record)
    paper_dir.mkdir(parents=True)
    prior_pdf = paper_dir / "paper.pdf"
    prior_payload = b"%PDF-1.7\naccepted article\n%%EOF\n"
    prior_pdf.write_bytes(prior_payload)
    prior_hash = _sha256(prior_payload)
    write_json(
        paper_dir / "metadata.json",
        {
            "schema_version": 1,
            "record_id": record["record_id"],
            "doi": record["doi"],
            "year": record["year"],
            "title": record["title_crossref"],
            "artifact_type": "article_pdf",
            "local_path": str(prior_pdf.relative_to(layout.root)),
            "bytes": len(prior_payload),
            "sha256": prior_hash,
            "source_url": "https://example.test/accepted.pdf",
            "source_version": "acceptedVersion",
            "version_assertion_method": "upstream_metadata",
            "historical_evaluated_edition_relation": "not_established",
        },
    )

    published_payload = b"%PDF-1.7\npublished article\n%%EOF\n"
    response = SimpleNamespace(
        content=published_payload,
        url="https://example.test/resolved.pdf",
        raise_for_status=lambda: None,
    )
    monkeypatch.setattr(downloads, "request_with_retry", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 5,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )

    result = downloads._download_one_paper(
        object(),
        layout,
        record,
        allow_license_unknown=False,
        include_publisher=True,
        force=False,
    )

    assert result["status"] == "downloaded"
    current_manifest = json.loads((paper_dir / "metadata.json").read_text())
    current_pdf = paper_dir / "paper.pdf"
    assert (
        generic_manifest_identity_errors(layout, current_manifest, record, paper_dir, current_pdf)
        == []
    )
    assert current_manifest["source_version"] == "publishedVersion"

    archive_dir = paper_dir / "versions" / "acceptedVersion"
    archived_pdf = archive_dir / f"paper__{prior_hash[:12]}.pdf"
    archived_manifest_path = archive_dir / f"metadata__{prior_hash[:12]}.json"
    archived_manifest = json.loads(archived_manifest_path.read_text())
    assert archived_manifest["local_path"] == str(archived_pdf.relative_to(layout.root))
    assert (
        generic_manifest_identity_errors(layout, archived_manifest, record, paper_dir, archived_pdf)
        == []
    )
