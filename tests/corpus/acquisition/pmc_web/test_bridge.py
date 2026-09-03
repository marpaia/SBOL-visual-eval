"""Bridge queueing, storage, failure recording, and challenge cooldowns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sbol_visual_eval.corpus.acquisition import pmc_web
from sbol_visual_eval.corpus.acquisition.pmc_web import (
    article_xml as pmc_web_article_xml,
)
from sbol_visual_eval.corpus.acquisition.pmc_web import (
    bridge as pmc_web_bridge,
)
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.util.storage import (
    sha256_file,
    write_json,
    write_jsonl,
)
from sbol_visual_eval.corpus.validation import validate_local_article_pdfs

from .helpers import (
    FakeClient,
    _article_html,
    _article_xml,
    _challenge_html,
    _fake_verification,
    _paper_dir,
    _patch_verifiers,
    _pmc_manifest,
    _record,
    _response,
)


def test_queue_requires_author_manuscript_and_no_verified_pmc_pdf(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    eligible = _record("10.1234/eligible", "PMC100")
    with_pdf = _record("10.1234/with-pdf", "PMC200")
    published = _record("10.1234/published", "PMC300")
    missing_pmcid = _record("10.1234/no-pmcid", None)
    write_jsonl(layout.processed / "papers.jsonl", [eligible, with_pdf, published, missing_pmcid])

    _pmc_manifest(layout, eligible)
    existing_pdf = _paper_dir(layout, with_pdf) / "pmc" / "paper.pdf"
    existing_pdf.parent.mkdir(parents=True)
    existing_payload = b"%PDF-1.7\nexisting article\n%%EOF\n"
    existing_pdf.write_bytes(existing_payload)
    _pmc_manifest(
        layout,
        with_pdf,
        artifacts=[
            {
                "role": "article_pdf",
                "status": "downloaded",
                "local_path": str(existing_pdf.relative_to(layout.root)),
                "sha256": hashlib.sha256(existing_payload).hexdigest(),
            }
        ],
    )
    _pmc_manifest(layout, published, author_manuscript=False)
    _patch_verifiers(monkeypatch)

    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC100/"
    client = FakeClient({article_url: _response(article_url, _article_html("PMC100"))})
    bridge = pmc_web.PmcWebBridge(layout, token="local-token", client=client)

    assert bridge.status()["eligible_author_manuscripts"] == 1
    assert bridge.status()["manifest_backed_xml_discovery"] == 1
    assert bridge.status()["live_html_discovery_required"] == 0
    assert bridge.status()["eligibility_counts"] == {
        "eligible": 1,
        "verified_pmc_article_pdf_present": 1,
        "not_an_author_manuscript": 1,
        "no_europepmc_pmcid": 1,
    }
    [item] = bridge.next_items(8)
    assert {key: item[key] for key in item if key != "queue_nonce"} == {
        "doi": eligible["doi"],
        "pmcid": "PMC100",
        "article_url": article_url,
        "url": f"{article_url}pdf/nihms-main.pdf",
        "version_assertion_method": "pmc_acquisition_manifest",
    }
    assert item["queue_nonce"]
    assert client.requests == []
    assert bridge.next_items() == []


def test_queue_exhausts_manifest_xml_records_before_live_html_records(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    html_first = _record("10.1234/html-first", "PMC110")
    xml_first = _record("10.1234/xml-first", "PMC120")
    html_second = _record("10.1234/html-second", "PMC130")
    xml_second = _record("10.1234/xml-second", "PMC140")
    write_jsonl(
        layout.processed / "papers.jsonl",
        [html_first, xml_first, html_second, xml_second],
    )
    _pmc_manifest(layout, xml_first)
    _pmc_manifest(layout, xml_second)
    html_first_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC110/"
    html_second_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC130/"
    client = FakeClient(
        {
            html_first_url: _response(
                html_first_url,
                _article_html("PMC110", main_name="nihms-110.pdf"),
            ),
            html_second_url: _response(
                html_second_url,
                _article_html("PMC130", main_name="nihms-130.pdf"),
            ),
        }
    )
    bridge = pmc_web.PmcWebBridge(layout, token="token", client=client)

    assert [record["doi"] for record in bridge.records] == [
        xml_first["doi"],
        xml_second["doi"],
        html_first["doi"],
        html_second["doi"],
    ]
    for expected_record in (xml_first, xml_second):
        [item] = bridge.next_items()
        assert item["doi"] == expected_record["doi"]
        assert client.requests == []
        bridge.record_failure(
            expected_record["doi"],
            source_url=item["url"],
            resolved_url=item["url"],
            article_url=item["article_url"],
            error="test-only queue progression",
            queue_nonce=item["queue_nonce"],
        )

    [item] = bridge.next_items()
    assert item["doi"] == html_first["doi"]
    assert client.requests == [html_first_url]


def test_bridge_rejects_article_xml_changed_after_pmc_manifest_was_written(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/xml-tamper", "PMC101")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    xml_path = _paper_dir(layout, record) / "pmc" / "PMC101.1.xml"
    xml_path.write_bytes(_article_xml(record).replace(b"nihms-main.pdf", b"nihms-evil.pdf"))

    with pytest.raises(ValueError, match="SHA-256 does not match"):
        pmc_web.PmcWebBridge(layout, token="local-token", client=FakeClient({}))


def test_bridge_rejects_oversized_article_xml_before_reading_payload(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/xml-oversized", "PMC104")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    xml_path = _paper_dir(layout, record) / "pmc" / "PMC104.1.xml"
    with xml_path.open("wb") as handle:
        handle.seek(pmc_web_article_xml.MAX_ARTICLE_XML_BYTES)
        handle.write(b"x")

    with pytest.raises(ValueError, match="byte count does not match"):
        pmc_web.PmcWebBridge(layout, token="local-token", client=FakeClient({}))


def test_bridge_rejects_noncanonical_article_xml_manifest_path(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/xml-path-alias", "PMC102")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    manifest_path = _paper_dir(layout, record) / "pmc.json"
    manifest = json.loads(manifest_path.read_text())
    xml_artifact = next(
        artifact for artifact in manifest["artifacts"] if artifact["role"] == "article_xml"
    )
    xml_artifact["local_path"] = xml_artifact["local_path"].replace(
        "/pmc/",
        "/pmc/../pmc/",
    )
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match="local path is not canonical"):
        pmc_web.PmcWebBridge(layout, token="local-token", client=FakeClient({}))


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        (
            "source_url",
            "s3://pmc-oa-opendata/PMC102.1/other.xml?md5=" + "0" * 32,
            "not bound to package metadata xml_url",
        ),
        (
            "download_url",
            "https://evil.test/PMC102.1.xml",
            "noncanonical HTTPS download URL",
        ),
        ("md5", "0" * 32, "URL MD5 differs from its artifact"),
    ],
)
def test_bridge_rejects_article_xml_without_verified_package_lineage(
    tmp_path: Path,
    field: str,
    value: str,
    expected_error: str,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/xml-lineage", "PMC102")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    manifest_path = _paper_dir(layout, record) / "pmc.json"
    manifest = json.loads(manifest_path.read_text())
    xml_artifact = next(
        artifact for artifact in manifest["artifacts"] if artifact["role"] == "article_xml"
    )
    xml_artifact[field] = value
    write_json(manifest_path, manifest)

    with pytest.raises(ValueError, match=expected_error):
        pmc_web.PmcWebBridge(layout, token="local-token", client=FakeClient({}))


def test_bridge_binds_article_xml_manuscript_id_to_package_metadata(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/xml-manuscript-id", "PMC103")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(
        layout,
        record,
        article_xml=_article_xml(record, manuscript_id="EMS999"),
    )

    with pytest.raises(ValueError, match="manuscript ID differs from package metadata"):
        pmc_web.PmcWebBridge(layout, token="local-token", client=FakeClient({}))


def test_bridge_stores_collision_safe_generic_archived_variant(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/example", "PMC123")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record, license_code="TDM")
    _patch_verifiers(monkeypatch)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC123/"
    client = FakeClient({article_url: _response(article_url, _article_html("PMC123"))})
    bridge = pmc_web.PmcWebBridge(layout, token="private-token", client=client)
    [item] = bridge.next_items()
    payload = b"%PDF-1.7\nauthor manuscript article\n%%EOF\n"
    digest = hashlib.sha256(payload).hexdigest()

    result = bridge.store_pdf(
        record["doi"],
        payload,
        source_url=item["url"],
        resolved_url=item["url"],
        article_url=item["article_url"],
        queue_nonce=item["queue_nonce"],
    )

    variant_dir = _paper_dir(layout, record) / "versions" / "pmc_author_manuscript"
    pdf_path = variant_dir / f"paper__{digest[:12]}.pdf"
    metadata_path = variant_dir / f"metadata__{digest[:12]}.json"
    assert pdf_path.read_bytes() == payload
    metadata = json.loads(metadata_path.read_text())
    assert metadata["schema_version"] == 1
    assert metadata["artifact_type"] == "article_pdf"
    assert metadata["local_path"] == str(pdf_path.relative_to(layout.root))
    assert metadata["sha256"] == digest
    assert metadata["bytes"] == len(payload)
    assert metadata["source_version"] == "author_manuscript"
    assert metadata["version_assertion_method"] == "upstream_metadata"
    assert metadata["historical_evaluated_edition_relation"] == "not_established"
    assert metadata["historical_evaluated_edition_equivalence_asserted"] is False
    assert metadata["transport_provenance_verification_status"] == ("browser_supplied_unverified")
    assert metadata["source_license"] is None
    assert metadata["upstream_license_code"] == "TDM"
    assert metadata["source_url"] == item["url"]
    assert metadata["discovered_from_url"] == article_url
    assert metadata["source_metadata_source"] == ("PMC package metadata and verified article XML")
    assert metadata["source_landing_page_snapshot_path"] is None
    assert metadata["source_landing_page_snapshot_sha256"] is None
    assert metadata["source_landing_page_snapshot_bytes"] is None
    xml_snapshot = layout.root / metadata["pmc_article_xml_snapshot_path"]
    original_xml = _paper_dir(layout, record) / "pmc" / "PMC123.1.xml"
    assert xml_snapshot.read_bytes() == original_xml.read_bytes() == _article_xml(record)
    assert metadata["pmc_article_xml_snapshot_sha256"] == sha256_file(xml_snapshot)
    assert metadata["pmc_article_xml_snapshot_bytes"] == xml_snapshot.stat().st_size
    assert metadata["pmc_article_xml_original_path"] == str(original_xml.relative_to(layout.root))
    pmc_snapshot = layout.root / metadata["pmc_manifest_path"]
    original_pmc_manifest = _paper_dir(layout, record) / "pmc.json"
    assert pmc_snapshot.read_bytes() == original_pmc_manifest.read_bytes()
    assert metadata["pmc_manifest_sha256"] == sha256_file(pmc_snapshot)
    assert metadata["pmc_manifest_original_path"] == str(
        original_pmc_manifest.relative_to(layout.root)
    )
    package_snapshot = layout.root / metadata["pmc_package_metadata_snapshot_path"]
    original_package_metadata = _paper_dir(layout, record) / "pmc" / "package_metadata.json"
    assert package_snapshot.read_bytes() == original_package_metadata.read_bytes()
    assert metadata["pmc_package_metadata_snapshot_sha256"] == sha256_file(package_snapshot)
    assert metadata["pmc_package_metadata_snapshot_bytes"] == package_snapshot.stat().st_size
    assert result["status"] == "downloaded"
    persisted_text = (
        metadata_path.read_text() + (layout.reports / "pmc_web_attempts.jsonl").read_text()
    )
    assert "private-token" not in persisted_text

    monkeypatch.setattr(pdf, "verify_pdf", _fake_verification)
    assert validate_local_article_pdfs(layout, [record]) == []

    reloaded = pmc_web.PmcWebBridge(
        layout,
        token="another-token",
        client=FakeClient({}),
    )
    assert reloaded.status()["papers_with_pmc_web_pdf"] == 1
    assert reloaded.status()["status_counts"] == {"already_present": 1}
    assert reloaded.next_items() == []


def test_explicit_cc_license_is_preserved(tmp_path: Path, monkeypatch: Any) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/licensed", "PMC321")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record, license_code="CC BY-NC")
    _patch_verifiers(monkeypatch)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC321/"
    client = FakeClient({article_url: _response(article_url, _article_html("PMC321"))})
    bridge = pmc_web.PmcWebBridge(layout, token="token", client=client)
    [item] = bridge.next_items()

    result = bridge.store_pdf(
        record["doi"],
        b"%PDF-1.7\nlicensed author manuscript\n%%EOF\n",
        source_url=item["url"],
        resolved_url=item["url"],
        article_url=item["article_url"],
        queue_nonce=item["queue_nonce"],
    )

    assert result["source_license"] == "CC BY-NC"
    assert result["license_metadata_source"] == "PMC AWS package metadata"


@pytest.mark.parametrize(
    ("html", "expected_method"),
    [
        (
            (
                b'<div class="fm-authors-manuscript">Author manuscript</div>'
                b'<a href="pdf/main.pdf">Download PDF</a>'
            ),
            "pmc_public_page_author_manuscript_banner",
        ),
        (
            b'<a href="pdf/nihms-123456.pdf">Download PDF</a>',
            "pmc_nihms_pdf_basename",
        ),
    ],
)
def test_missing_pmc_manifest_requires_and_records_public_page_version_evidence(
    tmp_path: Path,
    monkeypatch: Any,
    html: bytes,
    expected_method: str,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/page-evidence", "PMC555")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _patch_verifiers(monkeypatch)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC555/"
    client = FakeClient({article_url: _response(article_url, html)})
    bridge = pmc_web.PmcWebBridge(layout, token="token", client=client)

    [item] = bridge.next_items()
    assert item["version_assertion_method"] == expected_method
    assert bridge.status()["manifest_backed_xml_discovery"] == 0
    assert bridge.status()["live_html_discovery_required"] == 1
    result = bridge.store_pdf(
        record["doi"],
        b"%PDF-1.7\npage-evidenced author manuscript\n%%EOF\n",
        source_url=item["url"],
        resolved_url=item["url"],
        article_url=item["article_url"],
        queue_nonce=item["queue_nonce"],
    )

    assert result["source_version"] == "author_manuscript"
    assert result["source_version_assertion_method"] == expected_method
    assert result["source_version_assertion_evidence"]
    assert result["pmc_manifest_path"] is None
    assert result["pmc_article_xml_original_path"] is None
    assert result["pmc_article_xml_snapshot_path"] is None
    assert result["pmc_article_xml_snapshot_sha256"] is None
    assert result["pmc_article_xml_snapshot_bytes"] is None
    page_snapshot = layout.root / result["source_landing_page_snapshot_path"]
    assert page_snapshot.read_bytes() == html
    assert result["source_landing_page_snapshot_sha256"] == sha256_file(page_snapshot)
    assert result["source_license"] is None
    assert result["version_assertion_method"] == "upstream_metadata"


def test_missing_pmc_manifest_without_page_evidence_is_skipped_without_version(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/unestablished", "PMC666")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC666/"
    html = b'<a href="pdf/main.pdf">Download PDF</a>'
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="token",
        client=FakeClient({article_url: _response(article_url, html)}),
    )

    assert bridge.next_items() == []

    attempts = [
        json.loads(line)
        for line in (layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()
    ]
    assert attempts[0]["status"] == "skipped_version_unestablished"
    assert attempts[0]["phase"] == "html_version_assertion"
    assert attempts[0]["requested_version"] == "author_manuscript"
    assert "source_version" not in attempts[0]
    assert not list(layout.papers.glob("**/*.pdf"))


def test_discovery_failure_is_appended_and_next_record_is_preserved(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    first = _record("10.1234/first", "PMC111", title="First")
    second = _record("10.1234/second", "PMC222", title="Second")
    write_jsonl(layout.processed / "papers.jsonl", [first, second])
    _patch_verifiers(monkeypatch)
    first_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC111/"
    second_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC222/"
    client = FakeClient(
        {
            first_url: _response(first_url, b"<html>No PDF</html>"),
            second_url: _response(
                second_url,
                _article_html("PMC222", main_name="nihms-222.pdf"),
            ),
        }
    )
    bridge = pmc_web.PmcWebBridge(layout, token="token", client=client)

    [item] = bridge.next_items()

    assert item["doi"] == second["doi"]
    attempts = [
        json.loads(line)
        for line in (layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()
    ]
    assert attempts[0]["doi"] == first["doi"]
    assert attempts[0]["phase"] == "html_discovery"
    assert attempts[0]["status"] == "failed"
    assert bridge.status()["status_counts"] == {"failed": 1}


def test_discovery_challenge_stops_before_next_record_and_persists_cooldown(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    first = _record("10.1234/challenged", "PMC800")
    second = _record("10.1234/must-not-be-consumed", "PMC801")
    write_jsonl(layout.processed / "papers.jsonl", [first, second])
    monkeypatch.setattr(pmc_web_bridge, "HTML_REQUEST_INTERVAL_SECONDS", 0)
    first_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC800/"
    second_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC801/"
    client = FakeClient(
        {
            first_url: _response(first_url, _challenge_html()),
            second_url: _response(second_url, _article_html("PMC801")),
        }
    )
    bridge = pmc_web.PmcWebBridge(layout, token="token", client=client)

    with pytest.raises(pmc_web.PmcTransientChallengeError):
        bridge.next_items()

    assert client.requests == [first_url]
    assert bridge.results == {}
    assert bridge.attempted == set()
    assert bridge.queued_items == {}
    assert bridge.status()["pending_this_session"] == 2
    assert bridge.status()["challenge_retry_after_seconds"] > 0
    attempts = [
        json.loads(line)
        for line in (layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()
    ]
    assert len(attempts) == 1
    assert attempts[0]["doi"] == first["doi"]
    assert attempts[0]["status"] == "transient_challenge"
    assert attempts[0]["phase"] == "html_discovery_transient"

    with pytest.raises(pmc_web.PmcTransientChallengeError):
        bridge.next_items()
    assert client.requests == [first_url]
    assert len((layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()) == 1

    restarted = pmc_web.PmcWebBridge(layout, token="new-token", client=FakeClient({}))
    with pytest.raises(pmc_web.PmcTransientChallengeError):
        restarted.next_items()
    assert restarted.client.requests == []


def test_browser_challenge_release_consumes_nonce_but_requeues_record(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/browser-challenge", "PMC802")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    monkeypatch.setattr(pmc_web_bridge, "HTML_REQUEST_INTERVAL_SECONDS", 0)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC802/"
    client = FakeClient({article_url: _response(article_url, _article_html("PMC802"))})
    bridge = pmc_web.PmcWebBridge(layout, token="token", client=client)
    [item] = bridge.next_items()

    result = bridge.record_transient_challenge(
        record["doi"],
        source_url=item["url"],
        resolved_url=item["url"],
        article_url=item["article_url"],
        error="PMC HTML challenge",
        queue_nonce=item["queue_nonce"],
    )

    assert result["status"] == "transient_challenge"
    assert bridge.results == {}
    assert bridge.attempted == set()
    assert bridge.queued_items == {}
    assert bridge.status()["pending_this_session"] == 1
    with pytest.raises(pmc_web.QueueClaimError):
        bridge.record_transient_challenge(
            record["doi"],
            source_url=item["url"],
            resolved_url=item["url"],
            article_url=item["article_url"],
            error="replay",
            queue_nonce=item["queue_nonce"],
        )
    with pytest.raises(pmc_web.PmcTransientChallengeError):
        bridge.next_items()

    bridge.challenge_retry_not_before = 0
    [retried] = bridge.next_items()
    assert retried["doi"] == record["doi"]
    assert retried["queue_nonce"] != item["queue_nonce"]


def test_user_release_requeues_without_challenge_cooldown(tmp_path: Path, monkeypatch: Any) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/user-release", "PMC806")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    monkeypatch.setattr(pmc_web_bridge, "HTML_REQUEST_INTERVAL_SECONDS", 0)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC806/"
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="token",
        client=FakeClient({article_url: _response(article_url, _article_html("PMC806"))}),
    )
    [item] = bridge.next_items()

    result = bridge.release_queue_item(
        record["doi"],
        source_url=item["url"],
        resolved_url=None,
        article_url=item["article_url"],
        reason="user stopped runner",
        queue_nonce=item["queue_nonce"],
    )

    assert result["status"] == "released_by_user"
    assert bridge.results == {}
    assert bridge.attempted == set()
    assert bridge.queued_items == {}
    assert bridge.status()["challenge_retry_after_seconds"] == 0
    assert not (layout.reports / "pmc_web_cooldown.json").exists()
    with pytest.raises(pmc_web.QueueClaimError):
        bridge.release_queue_item(
            record["doi"],
            source_url=item["url"],
            resolved_url=None,
            article_url=item["article_url"],
            reason="replay",
            queue_nonce=item["queue_nonce"],
        )
    [retried] = bridge.next_items()
    assert retried["doi"] == record["doi"]
    assert retried["queue_nonce"] != item["queue_nonce"]


def test_unknown_pmc_artifact_status_does_not_suppress_fallback(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/stale-status", "PMC444")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    existing_pdf = _paper_dir(layout, record) / "pmc" / "paper.pdf"
    existing_pdf.parent.mkdir(parents=True)
    existing_payload = b"%PDF-1.7\nstale status\n%%EOF\n"
    existing_pdf.write_bytes(existing_payload)
    _pmc_manifest(
        layout,
        record,
        artifacts=[
            {
                "role": "article_pdf",
                "status": "mystery",
                "local_path": str(existing_pdf.relative_to(layout.root)),
                "sha256": hashlib.sha256(existing_payload).hexdigest(),
            }
        ],
    )
    _patch_verifiers(monkeypatch)

    bridge = pmc_web.PmcWebBridge(layout, token="token", client=FakeClient({}))

    assert bridge.status()["eligible_author_manuscripts"] == 1


def test_upload_and_failure_queue_claims_are_one_shot(tmp_path: Path, monkeypatch: Any) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    first = _record("10.1234/one-shot", "PMC700")
    second = _record("10.1234/not-queued", "PMC701")
    write_jsonl(layout.processed / "papers.jsonl", [first, second])
    _pmc_manifest(layout, first)
    _pmc_manifest(layout, second)
    _patch_verifiers(monkeypatch)
    first_article = f"{pmc_web.PMC_ORIGIN}/articles/PMC700/"
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="token",
        client=FakeClient({first_article: _response(first_article, _article_html("PMC700"))}),
    )
    [item] = bridge.next_items()

    with pytest.raises(pmc_web.QueueClaimError):
        bridge.record_failure(
            second["doi"],
            source_url=item["url"],
            resolved_url=item["url"],
            article_url=item["article_url"],
            error="not queued",
            queue_nonce=item["queue_nonce"],
        )

    bridge.store_pdf(
        first["doi"],
        b"%PDF-1.7\none shot\n%%EOF\n",
        source_url=item["url"],
        resolved_url=item["url"],
        article_url=item["article_url"],
        queue_nonce=item["queue_nonce"],
    )
    with pytest.raises(pmc_web.QueueClaimError):
        bridge.store_pdf(
            first["doi"],
            b"%PDF-1.7\nreplay\n%%EOF\n",
            source_url=item["url"],
            resolved_url=item["url"],
            article_url=item["article_url"],
            queue_nonce=item["queue_nonce"],
        )
    assert len(list((_paper_dir(layout, first) / "versions").glob("**/paper__*.pdf"))) == 1


def test_failure_requires_and_consumes_live_queue_item(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/failure", "PMC702")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    _pmc_manifest(layout, record)
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC702/"
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="token",
        client=FakeClient({article_url: _response(article_url, _article_html("PMC702"))}),
    )
    [item] = bridge.next_items()

    bridge.record_failure(
        record["doi"],
        source_url=item["url"],
        resolved_url=item["url"],
        article_url=item["article_url"],
        error='=HYPERLINK("https://evil.test")',
        queue_nonce=item["queue_nonce"],
    )
    with pytest.raises(pmc_web.QueueClaimError):
        bridge.record_failure(
            record["doi"],
            source_url=item["url"],
            resolved_url=item["url"],
            article_url=item["article_url"],
            error="replay",
            queue_nonce=item["queue_nonce"],
        )
    bridge.write_report()
    assert (
        '=HYPERLINK(\\"https://evil.test\\")'
        in (layout.reports / "pmc_web_attempts.jsonl").read_text()
    )
    assert "'=HYPERLINK" in (layout.reports / "pmc_web_acquisition.csv").read_text()


@pytest.mark.parametrize(
    ("status", "payload", "expected_error"),
    [
        (302, b"", "redirected"),
        (200, b"x" * 33, "response safety limit"),
    ],
)
def test_discovery_treats_redirect_and_oversized_html_as_transient(
    tmp_path: Path,
    monkeypatch: Any,
    status: int,
    payload: bytes,
    expected_error: str,
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/transient-html", "PMC703")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC703/"
    monkeypatch.setattr(pmc_web_bridge, "MAX_HTML_BYTES", 32)
    bridge = pmc_web.PmcWebBridge(
        layout,
        token="token",
        client=FakeClient({article_url: _response(article_url, payload, status=status)}),
    )

    with pytest.raises(pmc_web.PmcTransientChallengeError):
        bridge.next_items()
    attempts = [
        json.loads(line)
        for line in (layout.reports / "pmc_web_attempts.jsonl").read_text().splitlines()
    ]
    assert [attempt["status"] for attempt in attempts] == ["transient_challenge"]
    assert expected_error in attempts[0]["error"]
    assert bridge.results == {}
    assert bridge.attempted == set()


def test_malformed_processed_year_cannot_escape_papers_root(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/bad-year", "PMC705")
    record["year"] = "/tmp/escape"
    write_jsonl(layout.processed / "papers.jsonl", [record])

    with pytest.raises(ValueError, match="invalid year"):
        pmc_web.PmcWebBridge(layout, token="token", client=FakeClient({}))


def test_symlinked_variant_directory_cannot_escape_paper_directory(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record("10.1234/symlink", "PMC706")
    write_jsonl(layout.processed / "papers.jsonl", [record])
    paper_dir = _paper_dir(layout, record)
    paper_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (paper_dir / "versions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlinked directories"):
        pmc_web.PmcWebBridge(layout, token="token", client=FakeClient({}))
    assert list(outside.iterdir()) == []
