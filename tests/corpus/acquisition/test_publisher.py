from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from sbol_visual_eval.corpus.acquisition import publisher
from sbol_visual_eval.corpus.acquisition.publisher import authenticated
from sbol_visual_eval.corpus.acquisition.publisher import urls as publisher_urls
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.util.storage import write_jsonl


def _layout_with_record(tmp_path: Path) -> tuple[Layout, dict[str, Any]]:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = {
        "record_id": "doi:10.1234/example",
        "doi": "10.1234/example",
        "year": 2023,
        "title_source": "Example paper",
        "title_crossref": "Example paper",
    }
    write_jsonl(layout.processed / "papers.jsonl", [record])
    return layout, record


def test_publisher_url_preserves_doi_path_separator() -> None:
    assert publisher_urls._publisher_url("10.1234/a b", publisher.PUBLISHER_ORIGIN) == (
        f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/a%20b"
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://pubs.acs.org",
        "https://example.test",
        "https://pubs.acs.org.example.test",
        f"{publisher.PUBLISHER_ORIGIN}/unexpected-path",
    ],
)
def test_publisher_url_rejects_untrusted_or_non_origin_urls(origin: str) -> None:
    with pytest.raises(ValueError, match="authenticated publisher origin"):
        publisher_urls._publisher_url("10.1234/example", origin)


def test_bridge_stores_verified_vor_without_browser_credentials(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 4,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    bridge = publisher.PublisherBridge(layout, token="local-token")
    [item] = bridge.next_items(1)
    assert item["doi"] == record["doi"]

    payload = b"%PDF-1.7\npublisher version\n%%EOF\n"
    result = bridge.store_pdf(
        record["doi"],
        payload,
        source_url=item["url"],
        resolved_url=f"{publisher.PUBLISHER_ORIGIN}/article-pdf/example.pdf",
    )

    destination = layout.papers / "2023" / "10.1234__example" / "publisher" / "paper.pdf"
    manifest_path = destination.parent.parent / "publisher.json"
    assert destination.read_bytes() == payload
    assert result["source_version"] == "publishedVersion"
    assert result["retrieved_version_scope"] == "current_version_of_record_at_retrieval"
    assert result["version_assertion_method"] == "authenticated_acs_version_of_record_pdf_endpoint"
    assert result["historical_evaluated_edition_relation"] == "not_established"
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["source_access"] == "CU Boulder EZproxy authenticated browser"
    assert "cookie" not in json.dumps(manifest).lower()
    assert bridge.next_items(1) == []
    assert bridge.status()["papers_with_publisher_pdf"] == 1

    reloaded = publisher.PublisherBridge(layout, token="another-token")
    assert reloaded.status()["status_counts"] == {"already_present": 1}


def test_bridge_quarantines_unmanaged_publisher_collision(tmp_path: Path, monkeypatch: Any) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 1,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    destination = layout.papers / "2023" / "10.1234__example" / "publisher" / "paper.pdf"
    destination.parent.mkdir(parents=True)
    prior_payload = b"%PDF-1.4\nprior\n"
    destination.write_bytes(prior_payload)

    bridge = publisher.PublisherBridge(layout, token="local-token")
    new_payload = b"%PDF-1.7\nnew\n"
    bridge.store_pdf(
        record["doi"],
        new_payload,
        source_url=f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example",
        resolved_url=None,
    )

    assert destination.read_bytes() == new_payload
    prior_hash = hashlib.sha256(prior_payload).hexdigest()
    preserved = destination.parent / "rejected" / f"paper__{prior_hash[:12]}.pdf"
    assert preserved.read_bytes() == prior_payload


def test_bridge_archives_only_valid_existing_pair_as_replaced(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 2,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    bridge = publisher.PublisherBridge(layout, token="local-token")
    first_payload = b"%PDF-1.7\nfirst valid publisher version\n%%EOF\n"
    bridge.store_pdf(
        record["doi"],
        first_payload,
        source_url=f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example",
        resolved_url=None,
    )
    first_hash = hashlib.sha256(first_payload).hexdigest()

    second_payload = b"%PDF-1.7\nsecond valid publisher version\n%%EOF\n"
    bridge.store_pdf(
        record["doi"],
        second_payload,
        source_url=f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example",
        resolved_url=None,
    )

    replaced = layout.papers / "2023" / "10.1234__example" / "publisher" / "replaced"
    assert (replaced / f"paper__{first_hash[:12]}.pdf").read_bytes() == first_payload
    archived_manifest = json.loads((replaced / f"publisher__{first_hash[:12]}.json").read_text())
    assert archived_manifest["sha256"] == first_hash
    archived_pdf = replaced / f"paper__{first_hash[:12]}.pdf"
    assert archived_manifest["local_path"] == str(archived_pdf.relative_to(layout.root))
    second_hash = hashlib.sha256(second_payload).hexdigest()
    assert archived_manifest["replaced_by_sha256"] == second_hash
    current_manifest = json.loads((replaced.parents[1] / "publisher.json").read_text())
    assert current_manifest["replaces_sha256"] == first_hash
    assert current_manifest["replaces_local_path"] == archived_manifest["local_path"]
    reloaded = publisher.PublisherBridge(layout, token="another-token")
    assert reloaded.status()["invalid_replaced_archives"] == 0


def test_bridge_heals_malformed_manifest_into_rejected(tmp_path: Path, monkeypatch: Any) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 2,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    destination = layout.papers / "2023" / "10.1234__example" / "publisher" / "paper.pdf"
    destination.parent.mkdir(parents=True)
    prior_payload = b"%PDF-1.7\ninvalid prior publisher version\n%%EOF\n"
    destination.write_bytes(prior_payload)
    manifest_path = destination.parent.parent / "publisher.json"
    malformed_manifest = "{not valid json"
    manifest_path.write_text(malformed_manifest)

    bridge = publisher.PublisherBridge(layout, token="local-token")
    assert bridge.status()["invalid_existing_artifacts"] == 1
    new_payload = b"%PDF-1.7\nnew valid publisher version\n%%EOF\n"
    bridge.store_pdf(
        record["doi"],
        new_payload,
        source_url=f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example",
        resolved_url=None,
    )

    prior_hash = hashlib.sha256(prior_payload).hexdigest()
    rejected = destination.parent / "rejected"
    assert (rejected / f"paper__{prior_hash[:12]}.pdf").read_bytes() == prior_payload
    assert (rejected / f"publisher__{prior_hash[:12]}.json").read_text() == malformed_manifest
    assert destination.read_bytes() == new_payload
    assert bridge.status()["invalid_existing_artifacts"] == 0


def test_bridge_backfills_version_provenance_for_verified_existing_pair(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 2,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    destination = layout.papers / "2023" / "10.1234__example" / "publisher" / "paper.pdf"
    destination.parent.mkdir(parents=True)
    payload = b"%PDF-1.7\nverified existing publisher version\n%%EOF\n"
    destination.write_bytes(payload)
    manifest_path = destination.parent.parent / "publisher.json"
    manifest = {
        "schema_version": 1,
        "record_id": record["record_id"],
        "doi": record["doi"],
        "source_url": f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example",
        "resolved_url": f"{publisher.PUBLISHER_ORIGIN}/article.pdf",
        "source_version": "publishedVersion",
        "artifact_type": "article_pdf",
        "local_path": str(destination.relative_to(layout.root)),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    manifest_path.write_text(json.dumps(manifest))

    bridge = publisher.PublisherBridge(layout, token="local-token")

    upgraded = json.loads(manifest_path.read_text())
    assert upgraded["schema_version"] == 2
    assert upgraded["source_name"] == "ACS Publications via CU Boulder EZproxy"
    assert upgraded["retrieved_version_scope"] == "current_version_of_record_at_retrieval"
    assert upgraded["historical_evaluated_edition_relation"] == "not_established"
    assert bridge.status()["papers_with_publisher_pdf"] == 1


def test_bridge_failure_does_not_downgrade_verified_success(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 2,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    bridge = publisher.PublisherBridge(layout, token="local-token")
    source_url = f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example"
    bridge.store_pdf(
        record["doi"],
        b"%PDF-1.7\nverified publisher version\n%%EOF\n",
        source_url=source_url,
        resolved_url=f"{publisher.PUBLISHER_ORIGIN}/article.pdf",
    )

    result = bridge.record_failure(
        record["doi"],
        source_url=source_url,
        resolved_url="https://malicious.example/article.pdf",
        error="late browser failure",
    )

    assert result["status"] == "downloaded"
    assert bridge.status()["status_counts"] == {"downloaded": 1}
    attempts = [
        json.loads(line)
        for line in (layout.reports / "publisher_attempts.jsonl").read_text().splitlines()
    ]
    assert attempts[-1]["status"] == "failed"
    assert attempts[-1]["resolved_url"] is None
    assert attempts[-1]["preserved_existing_status"] == "downloaded"


def test_bridge_reports_orphaned_replacement_archive(tmp_path: Path, monkeypatch: Any) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 2,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    bridge = publisher.PublisherBridge(layout, token="local-token")
    source_url = f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example"
    bridge.store_pdf(
        record["doi"],
        b"%PDF-1.7\nfirst publisher version\n%%EOF\n",
        source_url=source_url,
        resolved_url=None,
    )
    bridge.store_pdf(
        record["doi"],
        b"%PDF-1.7\nsecond publisher version\n%%EOF\n",
        source_url=source_url,
        resolved_url=None,
    )
    archived_manifest = next(
        (layout.papers / "2023" / "10.1234__example" / "publisher" / "replaced").glob(
            "publisher__*.json"
        )
    )
    archived_manifest.unlink()

    reloaded = publisher.PublisherBridge(layout, token="another-token")

    assert reloaded.status()["papers_with_publisher_pdf"] == 1
    assert reloaded.status()["invalid_replaced_archives"] >= 1


def test_authenticated_fetch_rejects_redirect_away_from_acs() -> None:
    class Response:
        def __init__(self) -> None:
            self.status_code = 200
            self.content = b"%PDF-1.7\nnot accepted\n%%EOF\n"
            self.url = "https://malicious.example/article.pdf"
            self.headers = {"content-type": "application/pdf"}
            self.ok = True

    class Session:
        def get(self, url: str, **kwargs: Any) -> Response:
            return Response()

    outcome = authenticated._fetch_authenticated_item(
        Session(),
        {
            "doi": "10.1234/example",
            "url": f"{publisher.PUBLISHER_ORIGIN}/doi/pdf/10.1234/example",
        },
        cookie_value="secret",
        verify=True,
        max_retries=1,
    )

    assert outcome["kind"] == "failure"
    assert outcome["fatal_access"] is True
    assert outcome["resolved_url"] is None
    assert "redirect rejected" in outcome["error"]


def test_authenticated_downloader_stops_on_ezproxy_suspension_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    records = [
        {
            "record_id": f"doi:10.1234/example-{index}",
            "doi": f"10.1234/example-{index}",
            "year": 2023,
            "title_source": f"Example paper {index}",
            "title_crossref": f"Example paper {index}",
        }
        for index in range(4)
    ]
    write_jsonl(layout.processed / "papers.jsonl", records)
    monkeypatch.setattr(
        authenticated,
        "_ezproxy_ca_bundle",
        lambda explicit_path=None: nullcontext(True),
    )

    class Response:
        def __init__(self, url: str) -> None:
            self.status_code = 200
            self.content = (
                b"<html><title>Suspended Account</title>"
                b"Your off-campus account has lost access.</html>"
            )
            self.url = url
            self.headers = {"content-type": "text/html; charset=utf-8"}
            self.ok = True

    class Session:
        def get(self, url: str, **kwargs: Any) -> Response:
            return Response(url)

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        authenticated.curl_requests,
        "Session",
        lambda **kwargs: Session(),
    )

    summary = publisher.acquire_publisher_authenticated(
        layout,
        cookie="private-cookie-value",
        delay_seconds=0,
        max_retries=1,
        workers=1,
        limit=4,
    )

    assert summary["new_records_attempted"] == 3
    assert summary["stopped_reason"] == "authentication_or_access_failure"
    attempts = [
        json.loads(line)
        for line in (layout.reports / "publisher_attempts.jsonl").read_text().splitlines()
    ]
    assert len(attempts) == 3
    assert all("institutional EZProxy access suspended" in row["error"] for row in attempts)
    assert "Your off-campus account" not in json.dumps(attempts)


def test_authenticated_downloader_retries_throttle_and_never_records_cookie(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _layout_with_record(tmp_path)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: {
            "artifact_type": "article_pdf",
            "page_count": 2,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )
    monkeypatch.setattr(
        authenticated,
        "_ezproxy_ca_bundle",
        lambda explicit_path=None: nullcontext(True),
    )
    sleeps: list[float] = []
    monkeypatch.setattr(authenticated.time, "sleep", sleeps.append)
    payload = b"%PDF-1.7\nauthenticated publisher paper\n%%EOF\n"

    class Response:
        def __init__(self, status_code: int, content: bytes) -> None:
            self.status_code = status_code
            self.content = content
            self.url = f"{publisher.PUBLISHER_ORIGIN}/article.pdf"
            self.headers = {
                "content-type": "application/pdf" if status_code == 200 else "text/html",
                "retry-after": "2" if status_code == 429 else "",
            }

        @property
        def ok(self) -> bool:
            return 200 <= self.status_code < 300

    class Session:
        def __init__(self) -> None:
            self.responses = [Response(429, b"throttled"), Response(200, payload)]
            self.calls: list[dict[str, Any]] = []

        def get(self, url: str, **kwargs: Any) -> Response:
            self.calls.append({"url": url, **kwargs})
            return self.responses.pop(0)

        def close(self) -> None:
            pass

    session = Session()
    monkeypatch.setattr(
        authenticated.curl_requests,
        "Session",
        lambda **kwargs: session,
    )

    summary = publisher.acquire_publisher_authenticated(
        layout,
        cookie="private-cookie-value",
        delay_seconds=0,
        max_retries=2,
        limit=1,
    )

    assert summary["papers_with_publisher_pdf"] == 1
    assert sleeps == [2.0]
    assert session.calls[0]["cookies"] == {"ezproxy": "private-cookie-value"}
    manifest_path = layout.papers / "2023" / "10.1234__example" / "publisher.json"
    manifest_text = manifest_path.read_text()
    attempt_log = (layout.reports / "publisher_attempts.jsonl").read_text()
    assert record["doi"] in manifest_text
    assert "private-cookie-value" not in manifest_text
    assert "private-cookie-value" not in attempt_log
