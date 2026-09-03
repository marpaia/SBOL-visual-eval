from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from sbol_visual_eval.corpus.inventory import build_local_inventory
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.provenance.generic import generic_manifest_identity_errors
from sbol_visual_eval.corpus.provenance.pmc import (
    PMC_PACKAGE_CANONICALIZATION,
    PMC_PACKAGE_HASH_SCOPE,
)
from sbol_visual_eval.corpus.provenance.pmc_web import (
    PMC_WEB_MAX_EVIDENCE_BYTES,
    PMC_WEB_SOURCE_ACCESS,
    PMC_WEB_SOURCE_NAME,
    PMC_WEB_TRANSPORT_LIMITATION,
    PMC_WEB_TRANSPORT_STATUS,
    PMC_WEB_VARIANT_DIRECTORY,
)
from sbol_visual_eval.corpus.util.storage import (
    canonical_json_sha256,
    sha256_file,
    write_json,
    write_jsonl,
)
from sbol_visual_eval.corpus.validation import validate_local_article_pdfs


def _fixture(layout: Layout) -> tuple[dict[str, Any], Path, Path]:
    record = {
        "record_id": "doi:10.1234/pmc-web",
        "doi": "10.1234/pmc-web",
        "year": 2023,
        "title_source": "PMC web paper",
        "title_crossref": "PMC web paper",
        "europepmc_pmcid": "PMC123",
        "figures_total": 1,
        "figures_sbol_visual_compatible": 0,
        "figures_sbol_visual_compliant": 0,
        "figures_best_practices": 0,
        "has_compatible_figures": False,
        "all_compatible_figures_compliant": False,
    }
    write_jsonl(layout.processed / "papers.jsonl", [record])
    paper_dir = layout.papers / "2023" / "10.1234__pmc-web"
    package_metadata = {
        "doi": record["doi"],
        "pmcid": "PMC123",
        "version": 1,
        "is_manuscript": True,
        "is_pmc_openaccess": False,
    }
    package_path = paper_dir / "pmc" / "package_metadata.json"
    write_json(package_path, package_metadata)
    pmc_manifest_path = paper_dir / "pmc.json"
    write_json(
        pmc_manifest_path,
        {
            "schema_version": 2,
            "status": "downloaded",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "pmcid": "PMC123",
            "versioned_pmcid": "PMC123.1",
            "pmc_version": 1,
            "source_version": "author_manuscript",
            "is_manuscript": True,
            "is_pmc_openaccess": False,
            "license_code": "TDM",
            "package_metadata_url": "https://pmc.test/package.json",
            "package_metadata_sha256": sha256_file(package_path),
            "package_metadata_sha256_scope": PMC_PACKAGE_HASH_SCOPE,
            "package_metadata_local_path": str(package_path.relative_to(layout.root)),
            "package_metadata_canonical_sha256": canonical_json_sha256(package_metadata),
            "package_metadata_canonicalization": PMC_PACKAGE_CANONICALIZATION,
            "package_metadata": package_metadata,
            "artifacts": [],
        },
    )

    payload = b"%PDF-1.7\nauthor manuscript article\n%%EOF\n"
    digest = hashlib.sha256(payload).hexdigest()
    variant_dir = paper_dir / "versions" / PMC_WEB_VARIANT_DIRECTORY
    pdf_path = variant_dir / f"paper__{digest[:12]}.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(payload)
    metadata_path = variant_dir / f"metadata__{digest[:12]}.json"
    article_url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"
    pdf_url = f"{article_url}pdf/nihms-123.pdf"
    source_page_payload = (
        '<div class="fm-authors-manuscript">Author manuscript</div>'
        f'<meta name="citation_pdf_url" content="{pdf_url}">'
    ).encode()
    source_page_hash = hashlib.sha256(source_page_payload).hexdigest()
    source_page_path = variant_dir / f"source_page__{source_page_hash[:12]}.html"
    source_page_path.write_bytes(source_page_payload)
    pmc_manifest_payload = pmc_manifest_path.read_bytes()
    pmc_manifest_hash = hashlib.sha256(pmc_manifest_payload).hexdigest()
    pmc_manifest_snapshot_path = variant_dir / f"pmc_manifest__{pmc_manifest_hash[:12]}.json"
    pmc_manifest_snapshot_path.write_bytes(pmc_manifest_payload)
    package_payload = package_path.read_bytes()
    package_hash = hashlib.sha256(package_payload).hexdigest()
    package_snapshot_path = variant_dir / f"pmc_package_metadata__{package_hash[:12]}.json"
    package_snapshot_path.write_bytes(package_payload)
    write_json(
        metadata_path,
        {
            "schema_version": 1,
            "status": "downloaded",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "year": 2023,
            "title": record["title_crossref"],
            "artifact_type": "article_pdf",
            "local_path": str(pdf_path.relative_to(layout.root)),
            "source_url": pdf_url,
            "resolved_url": pdf_url,
            "source_landing_page_url": article_url,
            "discovered_from_url": article_url,
            "source_name": PMC_WEB_SOURCE_NAME,
            "source_type": "repository",
            "source_version": "author_manuscript",
            "source_license": None,
            "source_access": PMC_WEB_SOURCE_ACCESS,
            "source_metadata_source": "PMC package metadata and public article HTML",
            "source_intended_application": "full_text",
            "transport_provenance_verification_status": PMC_WEB_TRANSPORT_STATUS,
            "transport_provenance_limitation": PMC_WEB_TRANSPORT_LIMITATION,
            "license_metadata_source": None,
            "upstream_license_code": "TDM",
            "pmcid": "PMC123",
            "versioned_pmcid": "PMC123.1",
            "pmc_manifest_path": str(pmc_manifest_snapshot_path.relative_to(layout.root)),
            "pmc_manifest_original_path": str(pmc_manifest_path.relative_to(layout.root)),
            "pmc_manifest_sha256": pmc_manifest_hash,
            "pmc_package_metadata_url": "https://pmc.test/package.json",
            "pmc_package_metadata_snapshot_path": str(
                package_snapshot_path.relative_to(layout.root)
            ),
            "pmc_package_metadata_snapshot_sha256": package_hash,
            "pmc_package_metadata_snapshot_bytes": len(package_payload),
            "source_landing_page_snapshot_path": str(source_page_path.relative_to(layout.root)),
            "source_landing_page_snapshot_sha256": source_page_hash,
            "source_landing_page_snapshot_bytes": len(source_page_payload),
            "version_assertion_method": "upstream_metadata",
            "source_version_assertion": "author_manuscript",
            "source_version_assertion_method": "pmc_acquisition_manifest",
            "source_version_assertion_evidence": [
                {
                    "kind": "pmc_manifest_fields",
                    "value": "is_manuscript=true; source_version=author_manuscript",
                }
            ],
            "historical_evaluated_edition_relation": "not_established",
            "historical_evaluated_edition_equivalence_asserted": False,
            "bytes": len(payload),
            "sha256": digest,
        },
    )
    return record, pdf_path, metadata_path


def _xml_fixture(layout: Layout) -> tuple[dict[str, Any], Path, Path]:
    record, pdf_path, metadata_path = _fixture(layout)
    paper_dir = layout.papers / "2023" / "10.1234__pmc-web"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    source_page_path = layout.root / metadata["source_landing_page_snapshot_path"]
    source_page_path.unlink()
    xml_payload = f"""
        <!DOCTYPE article PUBLIC "-//NLM//DTD JATS 1.4//EN" "JATS-archivearticle1-4.dtd">
        <article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
          <article-id pub-id-type="pmcid">PMC123</article-id>
          <article-id pub-id-type="doi">{record["doi"]}</article-id>
          <article-id pub-id-type="manuscript-id">NIHMS123</article-id>
          <self-uri content-type="pmc-pdf" xlink:href="nihms-123.pdf"/>
        </article-meta></front></article>
    """.encode()
    xml_hash = hashlib.sha256(xml_payload).hexdigest()
    xml_md5 = hashlib.md5(xml_payload).hexdigest()
    original_xml_path = paper_dir / "pmc" / "PMC123.1.xml"
    original_xml_path.write_bytes(xml_payload)
    xml_source_url = f"s3://pmc-oa-opendata/PMC123.1/PMC123.1.xml?md5={xml_md5}"
    xml_download_url = (
        f"https://pmc-oa-opendata.s3.amazonaws.com/PMC123.1/PMC123.1.xml?md5={xml_md5}"
    )

    package_path = paper_dir / "pmc" / "package_metadata.json"
    package_metadata = json.loads(package_path.read_text(encoding="utf-8"))
    package_metadata["xml_url"] = xml_source_url
    package_metadata["mid"] = "NIHMS123"
    write_json(package_path, package_metadata)

    upstream_path = paper_dir / "pmc.json"
    upstream_manifest = json.loads(upstream_path.read_text(encoding="utf-8"))
    upstream_manifest.update(
        {
            "package_metadata": package_metadata,
            "package_metadata_sha256": sha256_file(package_path),
            "package_metadata_canonical_sha256": canonical_json_sha256(package_metadata),
        }
    )
    upstream_manifest["artifacts"] = [
        {
            "bytes": len(xml_payload),
            "download_url": xml_download_url,
            "local_path": str(original_xml_path.relative_to(layout.root)),
            "md5": xml_md5,
            "role": "article_xml",
            "sha256": xml_hash,
            "source_url": xml_source_url,
            "status": "downloaded",
        }
    ]
    write_json(upstream_path, upstream_manifest)

    old_manifest_snapshot = layout.root / metadata["pmc_manifest_path"]
    old_manifest_snapshot.unlink()
    upstream_payload = upstream_path.read_bytes()
    upstream_hash = hashlib.sha256(upstream_payload).hexdigest()
    upstream_snapshot = metadata_path.parent / f"pmc_manifest__{upstream_hash[:12]}.json"
    upstream_snapshot.write_bytes(upstream_payload)
    old_package_snapshot = layout.root / metadata["pmc_package_metadata_snapshot_path"]
    old_package_snapshot.unlink()
    package_payload = package_path.read_bytes()
    package_hash = hashlib.sha256(package_payload).hexdigest()
    package_snapshot = metadata_path.parent / f"pmc_package_metadata__{package_hash[:12]}.json"
    package_snapshot.write_bytes(package_payload)
    xml_snapshot = metadata_path.parent / f"pmc_article_xml__{xml_hash[:12]}.xml"
    xml_snapshot.write_bytes(xml_payload)

    metadata.update(
        {
            "source_metadata_source": "PMC package metadata and verified article XML",
            "source_landing_page_snapshot_path": None,
            "source_landing_page_snapshot_sha256": None,
            "source_landing_page_snapshot_bytes": None,
            "pmc_manifest_path": str(upstream_snapshot.relative_to(layout.root)),
            "pmc_manifest_sha256": upstream_hash,
            "pmc_package_metadata_snapshot_path": str(package_snapshot.relative_to(layout.root)),
            "pmc_package_metadata_snapshot_sha256": package_hash,
            "pmc_package_metadata_snapshot_bytes": len(package_payload),
            "pmc_article_xml_original_path": str(original_xml_path.relative_to(layout.root)),
            "pmc_article_xml_snapshot_path": str(xml_snapshot.relative_to(layout.root)),
            "pmc_article_xml_snapshot_sha256": xml_hash,
            "pmc_article_xml_snapshot_bytes": len(xml_payload),
        }
    )
    write_json(metadata_path, metadata)
    return record, pdf_path, metadata_path


def test_pmc_web_transport_is_explicitly_unverified_and_never_preferred(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record, pdf_path, _ = _fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)

    assert validate_local_article_pdfs(layout, [record]) == []
    build_local_inventory(layout)

    with (layout.reports / "local_corpus_inventory.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    [artifact] = json.loads(row["article_artifacts_json"])
    assert artifact["path"] == str(pdf_path.relative_to(layout.root))
    assert artifact["verification_status"] == "unverified"
    assert artifact["verification_errors"] == [PMC_WEB_TRANSPORT_LIMITATION]
    assert row["preferred_pdf_path"] == ""
    assert row["has_local_article_pdf"] == "True"
    assert row["article_pdf_count"] == "1"
    assert row["has_verified_local_article_pdf"] == "False"
    assert row["verified_article_pdf_count"] == "0"
    assert row["unverified_artifact_count"] == "1"


def test_pmc_web_version_evidence_uses_immutable_snapshots(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, _ = _fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    paper_dir = layout.papers / "2023" / "10.1234__pmc-web"
    write_json(paper_dir / "pmc.json", {"replaced": True})
    (paper_dir / "pmc" / "package_metadata.json").write_text("replaced\n", encoding="utf-8")

    assert validate_local_article_pdfs(layout, [record]) == []


def test_pmc_web_xml_evidence_is_bound_to_upstream_artifact_and_rederived(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)

    assert validate_local_article_pdfs(layout, [record]) == []

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source_url"] = "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/nihms-999.pdf"
    metadata["resolved_url"] = metadata["source_url"]
    write_json(metadata_path, metadata)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(
        "source URL differs from its article XML pmc-pdf self-uri" in error for error in errors
    )


@pytest.mark.parametrize(
    ("replacement", "expected_error"),
    [
        (
            b'<article-id pub-id-type="doi">10.1234/not-this-paper</article-id>',
            "article XML does not uniquely identify the record DOI",
        ),
        (
            b'<self-uri content-type="pmc-pdf" xlink:href="nihms-999.pdf"/>',
            "source URL differs from its article XML pmc-pdf self-uri",
        ),
    ],
)
def test_pmc_web_xml_snapshot_semantics_are_rechecked(
    tmp_path: Path,
    monkeypatch: Any,
    replacement: bytes,
    expected_error: str,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    xml_path = layout.root / metadata["pmc_article_xml_snapshot_path"]
    payload = xml_path.read_bytes()
    if b"article-id" in replacement:
        payload = re.sub(
            rb'<article-id pub-id-type="doi">[^<]+</article-id>',
            replacement,
            payload,
            count=1,
        )
    else:
        payload = re.sub(
            rb'<self-uri content-type="pmc-pdf"[^>]*/>',
            replacement,
            payload,
            count=1,
        )
    xml_path.write_bytes(payload)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(expected_error in error for error in errors)


def test_pmc_web_xml_snapshot_rejects_entity_declarations_before_parsing(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    xml_path = layout.root / metadata["pmc_article_xml_snapshot_path"]
    payload = xml_path.read_bytes().replace(
        b"<!DOCTYPE article PUBLIC",
        b'<!DOCTYPE article [<!ENTITY x "expanded">]><!DOCTYPE article PUBLIC',
        1,
    )
    xml_path.write_bytes(payload)

    errors = validate_local_article_pdfs(layout, [record])
    assert any("article XML contains an entity declaration" in error for error in errors)


def test_pmc_web_xml_manuscript_id_is_bound_to_package_metadata(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    xml_path = layout.root / metadata["pmc_article_xml_snapshot_path"]
    xml_path.write_bytes(xml_path.read_bytes().replace(b"NIHMS123", b"NIHMS999"))

    errors = validate_local_article_pdfs(layout, [record])
    assert any(
        "article XML manuscript identifier differs from package metadata" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("variant", "expected_error"),
    [
        ("utf16_entity", "article XML is not strict UTF-8"),
        ("utf16_declaration", "article XML declaration does not specify UTF-8"),
        ("quoted_gt_internal_subset", "article XML contains a DOCTYPE internal subset"),
        (
            "comment_doctype_decoy",
            "article XML contains a DOCTYPE internal subset",
        ),
        (
            "unicode_comment_doctype_decoy",
            "article XML contains a DOCTYPE internal subset",
        ),
        ("nested_subarticle", "article XML root is not article"),
    ],
)
def test_pmc_web_xml_rejects_encoding_dtd_and_hierarchy_bypasses(
    tmp_path: Path,
    monkeypatch: Any,
    variant: str,
    expected_error: str,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    xml_path = layout.root / metadata["pmc_article_xml_snapshot_path"]
    article_meta = f"""
      <article-meta>
        <article-id pub-id-type="pmcid">PMC123</article-id>
        <article-id pub-id-type="doi">{record["doi"]}</article-id>
        <article-id pub-id-type="manuscript-id">NIHMS123</article-id>
        <self-uri content-type="pmc-pdf" xlink:href="nihms-123.pdf"/>
      </article-meta>
    """
    if variant == "utf16_entity":
        payload = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE article [<!ENTITY d "10.1234/pmc-web">]>'
            '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
            + article_meta.replace(record["doi"], "&d;")
            + "</front></article>"
        ).encode("utf-16")
    elif variant == "utf16_declaration":
        payload = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
            + article_meta
            + "</front></article>"
        ).encode()
    elif variant == "quoted_gt_internal_subset":
        payload = (
            '<!DOCTYPE article SYSTEM "foo>bar" [<!ELEMENT article ANY>]>'
            '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
            + article_meta
            + "</front></article>"
        ).encode()
    elif variant == "comment_doctype_decoy":
        payload = (
            '<!-- <!DOCTYPE " --><!DOCTYPE article [<!ELEMENT article ANY>]>'
            '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
            + article_meta
            + "</front></article>"
        ).encode()
    elif variant == "unicode_comment_doctype_decoy":
        payload = (
            "<!-- <!DOCTYPE ' " + "ß" * 10 + " --><!DOCTYPE article [<!ELEMENT article ANY>]>"
            '<article xmlns:xlink="http://www.w3.org/1999/xlink"><front>'
            + article_meta
            + "</front></article>"
        ).encode()
    else:
        payload = (
            '<not-an-article xmlns:xlink="http://www.w3.org/1999/xlink"><sub-article><front>'
            + article_meta
            + "</front></sub-article></not-an-article>"
        ).encode()
    xml_path.write_bytes(payload)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(expected_error in error for error in errors)


def test_pmc_web_xml_original_path_and_upstream_hash_are_bound(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["pmc_article_xml_original_path"] = "data/papers/2023/10.1234__pmc-web/pmc/other.xml"
    write_json(metadata_path, metadata)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(
        "article XML original path differs from its upstream manifest" in error for error in errors
    )


def test_pmc_web_xml_rejects_jointly_aliased_upstream_and_original_paths(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    upstream_path = layout.root / metadata["pmc_manifest_path"]
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    aliased_path = "data/papers/2023/10.1234__pmc-web/pmc/../pmc/PMC123.1.xml"
    upstream["artifacts"][0]["local_path"] = aliased_path
    metadata["pmc_article_xml_original_path"] = aliased_path
    write_json(upstream_path, upstream)
    write_json(metadata_path, metadata)

    errors = validate_local_article_pdfs(layout, [record])
    assert any("article XML local path is not canonical" in error for error in errors)


def test_pmc_web_evidence_snapshot_rejects_symlink_alias(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    snapshot_path = layout.root / metadata["pmc_article_xml_snapshot_path"]
    alias_path = snapshot_path.with_name("arbitrary-alias.xml")
    alias_path.symlink_to(snapshot_path.name)
    metadata["pmc_article_xml_snapshot_path"] = str(alias_path.relative_to(layout.root))
    write_json(metadata_path, metadata)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(
        "pmc_article_xml_snapshot_path is not directly in its variant directory" in error
        or "pmc_article_xml_snapshot_path has an invalid content-addressed filename" in error
        or "pmc_article_xml_snapshot_path path may not contain symlinks" in error
        for error in errors
    )


def test_pmc_web_evidence_snapshot_is_size_bounded_before_read(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    snapshot_path = layout.root / metadata["pmc_article_xml_snapshot_path"]
    with snapshot_path.open("wb") as handle:
        handle.seek(PMC_WEB_MAX_EVIDENCE_BYTES)
        handle.write(b"x")

    errors = validate_local_article_pdfs(layout, [record])
    assert any(
        "pmc_article_xml_snapshot_path exceeds the evidence safety limit" in error
        for error in errors
    )


def test_pmc_web_xml_requires_one_accepted_upstream_artifact(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    upstream_path = layout.root / metadata["pmc_manifest_path"]
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    upstream["artifacts"].append(dict(upstream["artifacts"][0]))
    write_json(upstream_path, upstream)

    errors = validate_local_article_pdfs(layout, [record])
    assert any("does not identify exactly one accepted article XML" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        (
            "source_url",
            "s3://pmc-oa-opendata/PMC123.1/forged.xml?md5=" + "0" * 32,
            "article XML source URL differs from package metadata",
        ),
        (
            "download_url",
            "https://evil.test/PMC123.1/PMC123.1.xml",
            "article XML download URL is not canonical",
        ),
        (
            "md5",
            "0" * 32,
            "article XML snapshot MD5 differs from its upstream manifest",
        ),
    ],
)
def test_pmc_web_xml_upstream_transport_chain_is_rechecked(
    tmp_path: Path,
    monkeypatch: Any,
    field: str,
    value: str,
    expected_error: str,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    upstream_path = layout.root / metadata["pmc_manifest_path"]
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    upstream["artifacts"][0][field] = value
    write_json(upstream_path, upstream)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(expected_error in error for error in errors)


def test_pmc_web_xml_transport_urls_require_versioned_canonical_filename(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    upstream_path = layout.root / metadata["pmc_manifest_path"]
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    digest = upstream["artifacts"][0]["md5"]
    source_url = f"s3://pmc-oa-opendata/PMC123.1/alternate.xml?md5={digest}"
    upstream["package_metadata"]["xml_url"] = source_url
    upstream["artifacts"][0]["source_url"] = source_url
    upstream["artifacts"][0]["download_url"] = (
        f"https://pmc-oa-opendata.s3.amazonaws.com/PMC123.1/alternate.xml?md5={digest}"
    )
    write_json(upstream_path, upstream)

    errors = validate_local_article_pdfs(layout, [record])
    assert any("package article XML URL is not canonical" in error for error in errors)


def test_page_banner_assertion_is_rederived_from_immutable_html_snapshot(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["source_version_assertion_method"] = "pmc_public_page_author_manuscript_banner"
    metadata["source_version_assertion_evidence"] = [
        {
            "kind": "public_page_author_manuscript_banner",
            "value": "Author manuscript",
        }
    ]
    metadata["source_metadata_source"] = "PMC public article HTML"
    unused_snapshots = [
        layout.root / metadata["pmc_manifest_path"],
        layout.root / metadata["pmc_package_metadata_snapshot_path"],
    ]
    for field in (
        "pmc_manifest_path",
        "pmc_manifest_original_path",
        "pmc_manifest_sha256",
        "pmc_package_metadata_url",
        "pmc_package_metadata_snapshot_path",
        "pmc_package_metadata_snapshot_sha256",
        "pmc_package_metadata_snapshot_bytes",
        "versioned_pmcid",
        "upstream_license_code",
    ):
        metadata[field] = None
    for snapshot in unused_snapshots:
        snapshot.unlink()
    write_json(metadata_path, metadata)

    assert validate_local_article_pdfs(layout, [record]) == []


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        (
            "schema_version",
            999,
            "PMC web manifest has an unsupported schema_version",
        ),
        (
            "source_version",
            "publishedVersion",
            "PMC web manifest does not identify an author manuscript",
        ),
        ("pmcid", "PMC999", "PMC web manifest PMCID mismatch"),
        ("pmcid", "PMC123.evil", "PMC web manifest PMCID mismatch"),
        (
            "source_url",
            "https://evil.test/articles/PMC123/pdf/nihms-123.pdf",
            "PMC web source_url is not a main PDF",
        ),
        (
            "source_url",
            ("https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/%25252F..%25252Fevil.pdf"),
            "PMC web source_url is not a main PDF",
        ),
        (
            "source_url",
            "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/nihms-123.pdf?token=secret",
            "PMC web source_url is not a main PDF",
        ),
        (
            "source_version_assertion_evidence",
            [{"kind": "pmc_manifest_fields", "value": "published version"}],
            "PMC web source-version assertion evidence is inconsistent",
        ),
        (
            "pmc_manifest_sha256",
            "0" * 64,
            "PMC web pmc_manifest_path SHA-256 mismatch",
        ),
        (
            "source_license",
            "CC BYGARBAGE",
            "PMC web source license differs from its upstream manifest",
        ),
        (
            "source_landing_page_snapshot_sha256",
            "0" * 64,
            "PMC web source_landing_page_snapshot_path SHA-256 mismatch",
        ),
    ],
)
def test_tampered_pmc_web_provenance_is_invalid_and_never_preferred(
    tmp_path: Path,
    monkeypatch: Any,
    field: str,
    value: Any,
    expected_error: str,
) -> None:
    layout = Layout(tmp_path)
    record, pdf_path, metadata_path = _fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata[field] = value
    write_json(metadata_path, metadata)

    errors = validate_local_article_pdfs(layout, [record])
    assert any(expected_error in error for error in errors)
    build_local_inventory(layout)

    with (layout.reports / "local_corpus_inventory.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    [artifact] = json.loads(row["article_artifacts_json"])
    assert artifact["path"] == str(pdf_path.relative_to(layout.root))
    assert artifact["verification_status"] == "invalid"
    assert row["preferred_pdf_path"] == ""
    assert row["has_local_article_pdf"] == "True"
    assert row["article_pdf_count"] == "1"
    assert row["has_verified_local_article_pdf"] == "False"
    assert row["verified_article_pdf_count"] == "0"
    assert row["invalid_artifact_count"] == "1"


def test_pmc_web_transport_markers_prevent_reclassification_as_generic(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _fixture(layout)
    manifest = json.loads(metadata_path.read_text(encoding="utf-8"))
    for field in (
        "source_name",
        "source_access",
        "source_version_assertion_method",
        "pmc_manifest_path",
        "source_landing_page_snapshot_path",
        "pmc_package_metadata_snapshot_path",
        "transport_provenance_verification_status",
        "transport_provenance_limitation",
    ):
        manifest.pop(field)
    relocated_path = metadata_path.parents[1] / "renamed_source" / "paper__example.pdf"
    manifest["local_path"] = str(relocated_path.relative_to(layout.root))

    errors = generic_manifest_identity_errors(
        layout,
        manifest,
        record,
        metadata_path.parents[2],
        relocated_path,
    )

    assert "PMC web artifact is not in its dedicated variant directory" in errors


def test_orphan_pmc_web_evidence_snapshot_is_rejected(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    orphan = metadata_path.parent / "source_page__000000000000.html"
    orphan.write_text("<html>orphan</html>", encoding="utf-8")

    errors = validate_local_article_pdfs(layout, [record])

    assert any(
        "PMC web evidence snapshot has no provenance manifest" in error
        and str(orphan.relative_to(layout.root)) in error
        for error in errors
    )


def test_orphan_pmc_web_article_xml_snapshot_is_rejected(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    layout = Layout(tmp_path)
    record, _, metadata_path = _xml_fixture(layout)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, candidate: None)
    orphan = metadata_path.parent / "pmc_article_xml__000000000000.xml"
    orphan.write_text("<article/>", encoding="utf-8")

    errors = validate_local_article_pdfs(layout, [record])

    assert any(
        "PMC web evidence snapshot has no provenance manifest" in error
        and str(orphan.relative_to(layout.root)) in error
        for error in errors
    )
