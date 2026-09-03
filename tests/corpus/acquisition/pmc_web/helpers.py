"""Shared fixtures-in-plain-form for the PMC-web bridge tests."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from sbol_visual_eval.corpus.acquisition.pmc_web import (
    verifier as pmc_web_verifier,
)
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.provenance.pmc import (
    PMC_PACKAGE_CANONICALIZATION,
    PMC_PACKAGE_HASH_SCOPE,
)
from sbol_visual_eval.corpus.util.storage import (
    canonical_json_sha256,
    sha256_file,
    write_json,
)


class FakeClient:
    def __init__(self, responses: dict[str, httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def get(self, url: str) -> httpx.Response:
        self.requests.append(url)
        return self.responses[url]

    @contextmanager
    def stream(self, method: str, url: str) -> Any:
        assert method == "GET"
        self.requests.append(url)
        yield self.responses[url]


def _record(
    doi: str,
    pmcid: str | None,
    *,
    title: str = "Example paper",
) -> dict[str, Any]:
    return {
        "record_id": f"doi:{doi}",
        "doi": doi,
        "year": 2023,
        "title_source": title,
        "title_crossref": title,
        "europepmc_pmcid": pmcid,
        "figures_total": 1,
        "figures_sbol_visual_compatible": 0,
        "figures_sbol_visual_compliant": 0,
        "figures_best_practices": 0,
        "has_compatible_figures": False,
        "all_compatible_figures_compliant": False,
    }


def _paper_dir(layout: Layout, record: dict[str, Any]) -> Path:
    return layout.papers / str(record["year"]) / str(record["doi"]).replace("/", "__")


def _article_xml(
    record: dict[str, Any],
    *,
    pdf_names: tuple[str, ...] = ("nihms-main.pdf",),
    manuscript_id: str = "NIHMS123",
) -> bytes:
    self_uris = "".join(
        (
            '<self-uri xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'content-type="pmc-pdf" xlink:href="{name}"/>'
        )
        for name in pdf_names
    )
    return (
        '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>'
        f'<article-id pub-id-type="pmcid">{record["europepmc_pmcid"]}</article-id>'
        f'<article-id pub-id-type="doi">{record["doi"]}</article-id>'
        f'<article-id pub-id-type="manuscript-id">{manuscript_id}</article-id>'
        f"{self_uris}</article-meta></front></article>"
    ).encode()


def _pmc_manifest(
    layout: Layout,
    record: dict[str, Any],
    *,
    author_manuscript: bool = True,
    artifacts: list[dict[str, Any]] | None = None,
    license_code: str | None = "TDM",
    article_xml: bytes | None = None,
) -> None:
    paper_dir = _paper_dir(layout, record)
    article_xml = article_xml if article_xml is not None else _article_xml(record)
    article_xml_md5 = hashlib.md5(article_xml, usedforsecurity=False).hexdigest()
    versioned_pmcid = f"{record['europepmc_pmcid']}.1"
    article_xml_source_url = (
        f"s3://pmc-oa-opendata/{versioned_pmcid}/{versioned_pmcid}.xml?md5={article_xml_md5}"
    )
    package_metadata = {
        "doi": record["doi"],
        "pmcid": record["europepmc_pmcid"],
        "version": 1,
        "is_manuscript": author_manuscript,
        "is_pmc_openaccess": not author_manuscript,
        "mid": "NIHMS123",
        "xml_url": article_xml_source_url,
    }
    package_path = paper_dir / "pmc" / "package_metadata.json"
    write_json(package_path, package_metadata)
    article_xml_path = paper_dir / "pmc" / f"{versioned_pmcid}.xml"
    article_xml_path.write_bytes(article_xml)
    article_xml_artifact = {
        "bytes": len(article_xml),
        "download_url": article_xml_source_url.replace(
            "s3://pmc-oa-opendata",
            "https://pmc-oa-opendata.s3.amazonaws.com",
            1,
        ),
        "local_path": str(article_xml_path.relative_to(layout.root)),
        "md5": article_xml_md5,
        "role": "article_xml",
        "sha256": hashlib.sha256(article_xml).hexdigest(),
        "source_url": article_xml_source_url,
        "status": "downloaded",
    }
    write_json(
        paper_dir / "pmc.json",
        {
            "schema_version": 2,
            "status": "downloaded",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "pmcid": record["europepmc_pmcid"],
            "versioned_pmcid": versioned_pmcid,
            "pmc_version": 1,
            "source_version": ("author_manuscript" if author_manuscript else "published_version"),
            "is_manuscript": author_manuscript,
            "is_pmc_openaccess": not author_manuscript,
            "license_code": license_code,
            "package_metadata_url": (
                "https://pmc-oa-opendata.s3.amazonaws.com/metadata/example.json"
            ),
            "package_metadata_sha256": sha256_file(package_path),
            "package_metadata_sha256_scope": PMC_PACKAGE_HASH_SCOPE,
            "package_metadata_local_path": str(package_path.relative_to(layout.root)),
            "package_metadata_canonical_sha256": canonical_json_sha256(package_metadata),
            "package_metadata_canonicalization": PMC_PACKAGE_CANONICALIZATION,
            "package_metadata": package_metadata,
            "artifacts": [article_xml_artifact, *(artifacts or [])],
        },
    )


def _response(
    url: str,
    html: bytes,
    status: int = 200,
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status, content=html, headers=headers, request=request)


def _article_html(pmcid: str, *, main_name: str = "nihms-main.pdf") -> bytes:
    return f"""
        <html><head>
          <meta name="citation_pdf_url"
                content="https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/{main_name}">
        </head><body>
          <a href="pdf/{main_name}">Download PDF</a>
          <a href="pdf/appendix_s1.pdf">Supplementary PDF</a>
        </body></html>
    """.encode()


def _challenge_html() -> bytes:
    payload = b"""
        <html><head><title>Checking your browser - reCAPTCHA</title></head>
        <body><main><h1>Checking your browser</h1><div class="g-recaptcha"></div></main></body>
        </html>
    """
    return payload + b" " * (21 * 1024 - len(payload))


def _fake_verification(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    assert path.is_file()
    assert record["doi"]
    return {
        "artifact_type": "article_pdf",
        "page_count": 7,
        "identity_check": "doi",
        "title_similarity": 1.0,
    }


def _patch_verifiers(monkeypatch: Any) -> None:
    monkeypatch.setattr(pdf, "verify_pdf", _fake_verification)
    monkeypatch.setattr(pmc_web_verifier, "_verify_uploaded_pdf", _fake_verification)
