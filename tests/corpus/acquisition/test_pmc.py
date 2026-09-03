from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from sbol_visual_eval.corpus.acquisition import pmc
from sbol_visual_eval.corpus.acquisition.pmc import acquire as pmc_acquire
from sbol_visual_eval.corpus.acquisition.pmc import ids as pmc_ids
from sbol_visual_eval.corpus.acquisition.pmc import opendata as pmc_opendata
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.util.storage import write_jsonl


def _md5(payload: bytes) -> str:
    return hashlib.md5(payload).hexdigest()


def test_batches_never_exceed_id_converter_limit() -> None:
    batches = list(pmc_ids._batched([str(index) for index in range(401)], 200))

    assert [len(batch) for batch in batches] == [200, 200, 1]
    assert [item for batch in batches for item in batch] == [str(index) for index in range(401)]


def test_current_pmc_version_and_s3_url_are_resolved_safely() -> None:
    record = {
        "versions": [
            {"pmcid": "PMC123.1", "current": False},
            {"pmcid": "PMC123.2", "current": True},
        ]
    }

    assert pmc_ids._select_current_version(record) == "PMC123.2"
    source = f"s3://pmc-oa-opendata/PMC123.2/figure one.jpg?md5={'a' * 32}"
    https_url, expected_md5, filename = pmc_opendata._s3_https_url(source)
    assert https_url == (
        f"https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/figure%20one.jpg?md5={'a' * 32}"
    )
    assert expected_md5 == "a" * 32
    assert filename == "figure one.jpg"


def test_acquire_pmc_downloads_oa_assets_and_preserves_text_only_gap(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    records = [
        {
            "record_id": "doi:10.1234/open",
            "doi": "10.1234/open",
            "year": 2023,
            "title_source": "Open paper",
            "title_crossref": "Open paper",
        },
        {
            "record_id": "doi:10.1234/manuscript",
            "doi": "10.1234/manuscript",
            "year": 2012,
            "title_source": "Author manuscript",
            "title_crossref": "Author manuscript",
        },
    ]
    write_jsonl(layout.processed / "papers.jsonl", records)

    pdf = b"%PDF-1.4\n%%EOF\n"
    figure = b"fake-jpeg-content"
    open_xml = b"<article><title>Open paper</title></article>"
    open_text = b"Open paper\n"
    manuscript_xml = b"<article><title>Author manuscript</title></article>"
    manuscript_text = b"Author manuscript\n"
    pdf_md5 = _md5(pdf)
    figure_md5 = _md5(figure)
    package_by_version = {
        "PMC100.1": {
            "pmcid": "PMC100",
            "version": 1,
            "pmid": 100,
            "doi": "10.1234/open",
            "title": "Open paper",
            "is_pmc_openaccess": True,
            "is_manuscript": False,
            "is_retracted": False,
            "license_code": "CC BY",
            "pdf_url": f"s3://pmc-oa-opendata/PMC100.1/PMC100.1.pdf?md5={pdf_md5}",
            "xml_url": (f"s3://pmc-oa-opendata/PMC100.1/PMC100.1.xml?md5={_md5(open_xml)}"),
            "text_url": (f"s3://pmc-oa-opendata/PMC100.1/PMC100.1.txt?md5={_md5(open_text)}"),
            "media_urls": [f"s3://pmc-oa-opendata/PMC100.1/figure.jpg?md5={figure_md5}"],
        },
        "PMC200.1": {
            "pmcid": "PMC200",
            "version": 1,
            "pmid": 200,
            "doi": "10.1234/manuscript",
            "title": "Author manuscript",
            "is_pmc_openaccess": False,
            "is_manuscript": True,
            "is_retracted": False,
            "license_code": "TDM",
            "xml_url": (f"s3://pmc-oa-opendata/PMC200.1/PMC200.1.xml?md5={_md5(manuscript_xml)}"),
            "text_url": (f"s3://pmc-oa-opendata/PMC200.1/PMC200.1.txt?md5={_md5(manuscript_text)}"),
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tools/idconv/api/v1/articles/"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "response-date": "2026-09-02 00:00:00",
                    "records": [
                        {
                            "requested-id": "10.1234/open",
                            "doi": "10.1234/open",
                            "pmcid": "PMC100",
                            "pmid": 100,
                            "versions": [{"pmcid": "PMC100.1", "current": True}],
                        },
                        {
                            "requested-id": "10.1234/manuscript",
                            "doi": "10.1234/manuscript",
                            "pmcid": "PMC200",
                            "pmid": 200,
                            "versions": [{"pmcid": "PMC200.1", "current": True}],
                        },
                    ],
                },
            )
        if request.url.path.startswith("/metadata/"):
            version = Path(unquote(request.url.path)).stem
            return httpx.Response(200, json=package_by_version[version])
        if request.url.path.endswith("/PMC100.1.pdf"):
            return httpx.Response(200, content=pdf, headers={"Content-Type": "application/pdf"})
        if request.url.path.endswith("/figure.jpg"):
            return httpx.Response(200, content=figure, headers={"Content-Type": "image/jpeg"})
        if request.url.path.endswith("/PMC100.1.xml"):
            return httpx.Response(
                200, content=open_xml, headers={"Content-Type": "application/xml"}
            )
        if request.url.path.endswith("/PMC100.1.txt"):
            return httpx.Response(200, content=open_text, headers={"Content-Type": "text/plain"})
        if request.url.path.endswith("/PMC200.1.xml"):
            return httpx.Response(
                200, content=manuscript_xml, headers={"Content-Type": "application/xml"}
            )
        if request.url.path.endswith("/PMC200.1.txt"):
            return httpx.Response(
                200, content=manuscript_text, headers={"Content-Type": "text/plain"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return real_client(
            transport=transport,
            headers=kwargs.get("headers"),
            limits=kwargs.get("limits"),
        )

    monkeypatch.setattr(pmc_acquire.httpx, "Client", client_factory)

    results = pmc.acquire_pmc(layout, workers=2)
    by_doi = {result["doi"]: result for result in results}

    open_result = by_doi["10.1234/open"]
    assert open_result["status"] == "downloaded"
    assert open_result["artifact_downloaded_count"] == 4
    assert open_result["image_media_available_count"] == 1
    assert open_result["pdf_local_path"] == "data/papers/2023/10.1234__open/paper.pdf"
    open_dir = layout.papers / "2023" / "10.1234__open"
    assert (open_dir / "paper.pdf").read_bytes() == pdf
    assert (open_dir / "media" / "figure.jpg").read_bytes() == figure
    assert (open_dir / "pmc" / "PMC100.1.xml").read_bytes() == open_xml
    assert (open_dir / "pmc" / "PMC100.1.txt").read_bytes() == open_text
    open_manifest = json.loads((open_dir / "pmc.json").read_text())
    assert open_manifest["license_code"] == "CC BY"
    assert open_manifest["schema_version"] == 2
    assert open_manifest["package_metadata_sha256_scope"] == "raw_http_response_bytes"
    package_metadata_path = layout.root / open_manifest["package_metadata_local_path"]
    assert json.loads(package_metadata_path.read_text()) == package_by_version["PMC100.1"]
    assert (
        hashlib.sha256(package_metadata_path.read_bytes()).hexdigest()
        == (open_manifest["package_metadata_sha256"])
    )
    assert open_manifest["package_metadata_canonical_sha256"] == (
        pmc_acquire.canonical_json_sha256(open_manifest["package_metadata"])
    )
    assert {artifact["sha256"] for artifact in open_manifest["artifacts"]} == {
        hashlib.sha256(pdf).hexdigest(),
        hashlib.sha256(figure).hexdigest(),
        hashlib.sha256(open_xml).hexdigest(),
        hashlib.sha256(open_text).hexdigest(),
    }

    manuscript_result = by_doi["10.1234/manuscript"]
    assert manuscript_result["status"] == "author_manuscript_text_downloaded"
    assert "does not distribute PDF" in manuscript_result["limitation"]
    manuscript_manifest = json.loads(
        (layout.papers / "2012" / "10.1234__manuscript" / "pmc.json").read_text()
    )
    assert manuscript_manifest["package_metadata"]["license_code"] == "TDM"
    assert len(manuscript_manifest["artifacts"]) == 2
    manuscript_dir = layout.papers / "2012" / "10.1234__manuscript" / "pmc"
    assert (manuscript_dir / "PMC200.1.xml").read_bytes() == manuscript_xml
    assert (manuscript_dir / "PMC200.1.txt").read_bytes() == manuscript_text

    summary = json.loads((layout.reports / "pmc_acquisition.json").read_text())
    assert summary["pmc_matches"] == 2
    assert summary["packages_with_pdf"] == 1
    assert summary["non_open_author_manuscripts_text_only"] == 1
    assert (layout.reports / "pmc_acquisition.csv").exists()

    rerun = pmc.acquire_pmc(layout, workers=2)
    rerun_by_doi = {result["doi"]: result for result in rerun}
    assert rerun_by_doi["10.1234/open"]["status"] == "already_present"


def test_existing_non_pmc_pdf_is_not_overwritten(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    existing = paper_dir / "paper.pdf"
    existing.write_bytes(b"different source")
    pmc_pdf = b"%PDF-1.4\nPMC\n"
    source_url = "s3://pmc-oa-opendata/PMC1.1/PMC1.1.pdf?md5=" + hashlib.md5(pmc_pdf).hexdigest()

    destination = pmc_opendata._article_pdf_destination(paper_dir, source_url)

    assert destination == paper_dir / "pmc" / "paper.pdf"
    assert existing.read_bytes() == b"different source"
