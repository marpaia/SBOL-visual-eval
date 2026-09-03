from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from sbol_visual_eval.corpus.inventory import build_local_inventory
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.provenance.pmc import (
    PMC_PACKAGE_CANONICALIZATION,
    PMC_PACKAGE_HASH_SCOPE,
)
from sbol_visual_eval.corpus.util.storage import canonical_json_sha256, write_json, write_jsonl


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _md5(payload: bytes) -> str:
    return hashlib.md5(payload).hexdigest()


def _record(
    doi: str,
    year: int,
    *,
    figures_total: int,
    figures_compatible: int,
    figures_compliant: int,
    figures_best_practices: int,
) -> dict[str, Any]:
    return {
        "record_id": f"doi:{doi}",
        "doi": doi,
        "year": year,
        "title_source": f"Paper {doi}",
        "title_crossref": f"Paper {doi}",
        "figures_total": figures_total,
        "figures_sbol_visual_compatible": figures_compatible,
        "figures_sbol_visual_compliant": figures_compliant,
        "figures_best_practices": figures_best_practices,
        "has_compatible_figures": figures_compatible > 0,
        "all_compatible_figures_compliant": (
            figures_compatible > 0 and figures_compatible == figures_compliant
        ),
    }


def _write_generic_pdf(
    layout: Layout,
    record: dict[str, Any],
    *,
    payload: bytes,
    source: str,
    version: str,
) -> Path:
    paper_dir = layout.papers / str(record["year"]) / str(record["doi"]).replace("/", "__")
    paper_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = paper_dir / "paper.pdf"
    pdf_path.write_bytes(payload)
    write_json(
        paper_dir / "metadata.json",
        {
            "schema_version": 1,
            "record_id": record["record_id"],
            "doi": record["doi"],
            "year": record["year"],
            "title": record["title_crossref"],
            "artifact_type": "article_pdf",
            "local_path": str(pdf_path.relative_to(layout.root)),
            "bytes": len(payload),
            "source_name": source,
            "source_url": f"https://example.test/{record['doi']}.pdf",
            "source_version": version,
            "sha256": _sha256(payload),
            "version_assertion_method": "upstream_metadata",
            "historical_evaluated_edition_relation": "not_established",
        },
    )
    return paper_dir


def _write_pmc_manifest(
    layout: Layout,
    record: dict[str, Any],
    paper_dir: Path,
    artifacts: list[dict[str, Any]],
) -> None:
    package_metadata = {
        "doi": record["doi"],
        "pmcid": "PMC123",
        "version": 1,
        "is_manuscript": False,
        "is_pmc_openaccess": True,
    }
    package_path = paper_dir / "pmc" / "package_metadata.json"
    write_json(package_path, package_metadata)
    write_json(
        paper_dir / "pmc.json",
        {
            "schema_version": 2,
            "status": "downloaded",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "pmcid": "PMC123",
            "versioned_pmcid": "PMC123.1",
            "pmc_version": 1,
            "source_name": "NIH NLM PubMed Central Article Datasets",
            "source_version": "published_version",
            "is_manuscript": False,
            "is_pmc_openaccess": True,
            "package_metadata_sha256": _sha256(package_path.read_bytes()),
            "package_metadata_sha256_scope": PMC_PACKAGE_HASH_SCOPE,
            "package_metadata_local_path": str(package_path.relative_to(layout.root)),
            "package_metadata_canonical_sha256": (canonical_json_sha256(package_metadata)),
            "package_metadata_canonicalization": (PMC_PACKAGE_CANONICALIZATION),
            "package_metadata": package_metadata,
            "artifacts": artifacts,
        },
    )


def test_inventory_maps_artifact_provenance_and_prefers_version_of_record(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, record: None)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    records = [
        _record(
            "10.1234/alternate",
            2023,
            figures_total=10,
            figures_compatible=2,
            figures_compliant=1,
            figures_best_practices=1,
        ),
        _record(
            "10.1234/fallback",
            2023,
            figures_total=5,
            figures_compatible=0,
            figures_compliant=0,
            figures_best_practices=0,
        ),
        _record(
            "10.1234/generic-vor",
            2022,
            figures_total=8,
            figures_compatible=1,
            figures_compliant=1,
            figures_best_practices=1,
        ),
    ]
    write_jsonl(layout.processed / "papers.jsonl", records)

    alternate_dir = _write_generic_pdf(
        layout,
        records[0],
        payload=b"accepted article",
        source="Institutional repository",
        version="acceptedVersion",
    )
    pmc_pdf = b"published article"
    pmc_xml = b"<article>published article</article>"
    pmc_text = b"published article\n"
    pmc_artifacts = (
        ("article_pdf", alternate_dir / "pmc" / "paper.pdf", pmc_pdf),
        ("article_xml", alternate_dir / "pmc" / "article.xml", pmc_xml),
        ("article_text", alternate_dir / "pmc" / "article.txt", pmc_text),
    )
    for _, path, payload in pmc_artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    _write_pmc_manifest(
        layout,
        records[0],
        alternate_dir,
        [
            {
                "role": kind,
                "status": "downloaded",
                "local_path": str(path.relative_to(layout.root)),
                "source_url": f"s3://pmc/{path.name}",
                "bytes": len(payload),
                "md5": _md5(payload),
                "sha256": _sha256(payload),
            }
            for kind, path, payload in pmc_artifacts
        ],
    )

    _write_generic_pdf(
        layout,
        records[1],
        payload=b"submitted article",
        source="Preprint repository",
        version="submittedVersion",
    )

    generic_vor_dir = _write_generic_pdf(
        layout,
        records[2],
        payload=b"publisher article",
        source="Publisher",
        version="publishedVersion",
    )
    accepted_variant = b"prior accepted article"
    variant_dir = generic_vor_dir / "versions" / "acceptedVersion"
    variant_dir.mkdir(parents=True)
    variant_hash = _sha256(accepted_variant)
    variant_pdf = variant_dir / f"paper__{variant_hash[:12]}.pdf"
    variant_pdf.write_bytes(accepted_variant)
    write_json(
        variant_dir / f"metadata__{variant_hash[:12]}.json",
        {
            "schema_version": 1,
            "record_id": records[2]["record_id"],
            "doi": records[2]["doi"],
            "year": records[2]["year"],
            "title": records[2]["title_crossref"],
            "artifact_type": "article_pdf",
            "local_path": str(variant_pdf.relative_to(layout.root)),
            "bytes": len(accepted_variant),
            "source_name": "Institutional repository",
            "source_url": "https://example.test/prior.pdf",
            "source_version": "acceptedVersion",
            "sha256": variant_hash,
            "version_assertion_method": "upstream_metadata",
            "historical_evaluated_edition_relation": "not_established",
        },
    )

    summary = build_local_inventory(layout)

    with (layout.reports / "local_corpus_inventory.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = {row["doi"]: row for row in csv.DictReader(handle)}

    alternate = rows["10.1234/alternate"]
    alternate_artifacts = {
        artifact["path"]: artifact for artifact in json.loads(alternate["article_artifacts_json"])
    }
    alternate_root = "data/papers/2023/10.1234__alternate/paper.pdf"
    alternate_pmc = "data/papers/2023/10.1234__alternate/pmc/paper.pdf"
    assert {
        key: alternate_artifacts[alternate_root][key]
        for key in ("source", "version", "hash", "kind")
    } == {
        "source": "Institutional repository",
        "version": "acceptedVersion",
        "hash": _sha256(b"accepted article"),
        "kind": "article_pdf",
    }
    assert {
        key: alternate_artifacts[alternate_pmc][key]
        for key in ("source", "version", "hash", "kind")
    } == {
        "source": "NIH NLM PubMed Central Article Datasets",
        "version": "published_version",
        "hash": _sha256(pmc_pdf),
        "kind": "article_pdf",
    }
    assert {artifact["kind"] for artifact in alternate_artifacts.values()} == {
        "article_pdf",
        "article_xml",
        "article_text",
    }
    assert alternate["preferred_pdf_path"] == alternate_pmc
    assert alternate["preferred_pdf_is_version_of_record"] == "True"
    assert alternate["has_verified_local_article_pdf"] == "True"
    assert alternate["verified_article_pdf_count"] == "2"
    assert alternate["has_local_version_of_record_pdf"] == "True"
    assert alternate["version_of_record_pdf_count"] == "1"

    fallback = rows["10.1234/fallback"]
    assert fallback["preferred_pdf_path"] == ("data/papers/2023/10.1234__fallback/paper.pdf")
    assert fallback["preferred_pdf_is_version_of_record"] == "False"
    assert fallback["has_local_version_of_record_pdf"] == "False"

    generic_vor = rows["10.1234/generic-vor"]
    assert generic_vor["preferred_pdf_path"] == ("data/papers/2022/10.1234__generic-vor/paper.pdf")
    assert generic_vor["article_pdf_count"] == "2"
    assert generic_vor["version_of_record_pdf_count"] == "1"
    assert json.loads(generic_vor["source_versions_json"]) == [
        "acceptedVersion",
        "publishedVersion",
    ]

    assert summary["papers_with_local_article_pdf"] == 3
    assert summary["papers_with_verified_local_article_pdf"] == 3
    assert summary["local_article_pdf_files"] == 5
    assert summary["verified_local_article_pdf_files"] == 5
    assert summary["papers_with_local_version_of_record_pdf"] == 2
    assert summary["local_version_of_record_pdf_files"] == 2
    assert summary["compatible_papers_with_local_version_of_record_pdf"] == 2
    assert summary["historical_counts_represented_by_local_version_of_record_pdf_papers"] == {
        "figures_total": 18,
        "figures_sbol_visual_compatible": 3,
        "figures_sbol_visual_compliant": 2,
        "figures_best_practices": 2,
    }
    assert summary["papers_with_local_version_of_record_pdf_by_year"]["2022"] == 1
    assert summary["papers_with_local_version_of_record_pdf_by_year"]["2023"] == 1


def test_inventory_prefers_explicit_publisher_version_of_record(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, record: None)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record(
        "10.1234/publisher",
        2023,
        figures_total=4,
        figures_compatible=1,
        figures_compliant=1,
        figures_best_practices=0,
    )
    write_jsonl(layout.processed / "papers.jsonl", [record])
    paper_dir = _write_generic_pdf(
        layout,
        record,
        payload=b"accepted article",
        source="Institutional repository",
        version="acceptedVersion",
    )
    publisher_payload = b"publisher version of record"
    publisher_pdf = paper_dir / "publisher" / "paper.pdf"
    publisher_pdf.parent.mkdir()
    publisher_pdf.write_bytes(publisher_payload)
    publisher_path = str(publisher_pdf.relative_to(layout.root))
    write_json(
        paper_dir / "publisher.json",
        {
            "status": "downloaded",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "source_name": "ACS Publications",
            "source_url": "https://pubs.acs.org/doi/pdf/10.1234/publisher",
            "source_version": "publishedVersion",
            "source_access": "CU Boulder EZproxy authenticated browser",
            "artifact_type": "article_pdf",
            "local_path": publisher_path,
            "bytes": len(publisher_payload),
            "sha256": _sha256(publisher_payload),
        },
    )
    prior_publisher_payload = b"prior publisher version"
    prior_publisher_hash = _sha256(prior_publisher_payload)
    replaced_dir = paper_dir / "publisher" / "replaced"
    replaced_dir.mkdir()
    replaced_pdf = replaced_dir / f"paper__{prior_publisher_hash[:12]}.pdf"
    replaced_pdf.write_bytes(prior_publisher_payload)
    write_json(
        replaced_dir / f"publisher__{prior_publisher_hash[:12]}.json",
        {
            "status": "replaced",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "source_name": "ACS Publications",
            "source_url": "https://pubs.acs.org/doi/pdf/10.1234/publisher",
            "source_version": "publishedVersion",
            "artifact_type": "article_pdf",
            "local_path": str(replaced_pdf.relative_to(layout.root)),
            "bytes": len(prior_publisher_payload),
            "sha256": prior_publisher_hash,
        },
    )

    summary = build_local_inventory(layout)

    with (layout.reports / "local_corpus_inventory.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    artifacts = json.loads(row["article_artifacts_json"])
    publisher_artifact = next(
        artifact for artifact in artifacts if artifact["path"] == publisher_path
    )
    assert {
        key: publisher_artifact[key] for key in ("path", "source", "version", "hash", "kind")
    } == {
        "path": publisher_path,
        "source": "ACS Publications",
        "version": "publishedVersion",
        "hash": _sha256(publisher_payload),
        "kind": "article_pdf",
    }
    assert row["preferred_pdf_path"] == publisher_path
    assert row["preferred_pdf_is_version_of_record"] == "True"
    assert row["has_local_version_of_record_pdf"] == "True"
    assert row["article_pdf_count"] == "3"
    assert row["version_of_record_pdf_count"] == "2"
    assert summary["papers_with_local_version_of_record_pdf"] == 1
    assert summary["local_version_of_record_pdf_files"] == 2


def test_inventory_exposes_unverified_artifacts_and_never_prefers_invalid_pdf(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, record: None)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record(
        "10.1234/verification",
        2023,
        figures_total=2,
        figures_compatible=1,
        figures_compliant=1,
        figures_best_practices=0,
    )
    write_jsonl(layout.processed / "papers.jsonl", [record])
    paper_dir = _write_generic_pdf(
        layout,
        record,
        payload=b"verified accepted article",
        source="Repository",
        version="acceptedVersion",
    )
    invalid_pdf = paper_dir / "pmc" / "paper.pdf"
    invalid_pdf.parent.mkdir()
    invalid_pdf.write_bytes(b"invalid VOR bytes")
    unverified_text = paper_dir / "pmc" / "article.txt"
    unverified_text.write_text("article text", encoding="utf-8")
    invalid_pdf_payload = invalid_pdf.read_bytes()
    unverified_text_payload = unverified_text.read_bytes()
    _write_pmc_manifest(
        layout,
        record,
        paper_dir,
        [
            {
                "role": "article_pdf",
                "status": "downloaded",
                "local_path": str(invalid_pdf.relative_to(layout.root)),
                "source_url": "s3://pmc/paper.pdf",
                "bytes": len(invalid_pdf_payload),
                "md5": _md5(invalid_pdf_payload),
                "sha256": "0" * 64,
            },
            {
                "role": "article_text",
                "status": "downloaded",
                "local_path": str(unverified_text.relative_to(layout.root)),
                "source_url": "s3://pmc/article.txt",
                "bytes": len(unverified_text_payload),
                "md5": _md5(unverified_text_payload),
            },
        ],
    )

    summary = build_local_inventory(layout)

    with (layout.reports / "local_corpus_inventory.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    artifacts = {
        artifact["path"]: artifact for artifact in json.loads(row["article_artifacts_json"])
    }
    invalid_path = str(invalid_pdf.relative_to(layout.root))
    text_path = str(unverified_text.relative_to(layout.root))
    assert artifacts[invalid_path]["verification_status"] == "invalid"
    assert artifacts[text_path]["verification_status"] == "unverified"
    assert row["preferred_pdf_path"] == str((paper_dir / "paper.pdf").relative_to(layout.root))
    assert row["preferred_pdf_is_version_of_record"] == "False"
    assert row["version_of_record_pdf_count"] == "0"
    assert row["invalid_artifact_count"] == "1"
    assert row["unverified_artifact_count"] == "1"
    assert len(json.loads(row["artifact_issues_json"])) == 2
    assert summary["invalid_artifacts"] == 1
    assert summary["unverified_artifacts"] == 1
    assert summary["papers_with_artifact_issues"] == 1


def test_inventory_does_not_prefer_schema_v1_pmc_pdf(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, record: None)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record(
        "10.1234/legacy-pmc",
        2023,
        figures_total=1,
        figures_compatible=0,
        figures_compliant=0,
        figures_best_practices=0,
    )
    write_jsonl(layout.processed / "papers.jsonl", [record])
    paper_dir = layout.papers / "2023" / "10.1234__legacy-pmc"
    pdf_path = paper_dir / "pmc" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    payload = b"legacy PMC article"
    pdf_path.write_bytes(payload)
    package_metadata = {
        "doi": record["doi"],
        "pmcid": "PMC123",
        "version": 319,
        "is_manuscript": False,
        "is_pmc_openaccess": True,
    }
    write_json(
        paper_dir / "pmc.json",
        {
            "schema_version": 1,
            "status": "downloaded",
            "record_id": record["record_id"],
            "doi": record["doi"],
            "pmcid": "PMC123",
            "versioned_pmcid": "PMC123.319",
            "pmc_version": 319,
            "source_name": "PMC",
            "source_version": "published_version",
            "is_manuscript": False,
            "is_pmc_openaccess": True,
            "package_metadata_sha256": "a" * 64,
            "package_metadata": package_metadata,
            "artifacts": [
                {
                    "role": "article_pdf",
                    "status": "downloaded",
                    "local_path": str(pdf_path.relative_to(layout.root)),
                    "source_url": "s3://pmc/paper.pdf",
                    "bytes": len(payload),
                    "md5": _md5(payload),
                    "sha256": _sha256(payload),
                }
            ],
        },
    )

    build_local_inventory(layout)

    with (layout.reports / "local_corpus_inventory.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    artifact = json.loads(row["article_artifacts_json"])[0]
    assert artifact["verification_status"] == "unverified"
    assert "schema-v1 package metadata" in " ".join(artifact["verification_errors"])
    assert row["has_local_article_pdf"] == "True"
    assert row["article_pdf_count"] == "1"
    assert row["has_verified_local_article_pdf"] == "False"
    assert row["verified_article_pdf_count"] == "0"
    assert row["preferred_pdf_path"] == ""
    assert row["version_of_record_pdf_count"] == "0"
    assert int(row["unverified_artifact_count"]) >= 2
