from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sbol_visual_eval.corpus.acquisition import biorxiv
from sbol_visual_eval.corpus.acquisition.generic_manifest import backfill_generic_manifests
from sbol_visual_eval.corpus.inventory import build_local_inventory
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import biorxiv as provenance_biorxiv
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.provenance.biorxiv import (
    BIORXIV_PREPRINT_CANDIDATES,
    BIORXIV_PREPRINT_DIRECT_TRANSPORT,
    BIORXIV_PREPRINT_IMPORTED_TRANSPORT,
    BIORXIV_PREPRINT_VARIANT_DIRECTORY,
    biorxiv_preprint_manifest_identity_errors,
    parse_biorxiv_api_payload,
    verify_biorxiv_preprint_pdf,
)
from sbol_visual_eval.corpus.provenance.generic import generic_manifest_provenance
from sbol_visual_eval.corpus.util.storage import write_json, write_jsonl
from sbol_visual_eval.corpus.validation import validate_local_article_pdfs

TARGET_DOI = "10.1021/acssynbio.8b00482"
PREPRINT_DOI = "10.1101/367045"
PREPRINT_TITLE = (
    "TnClone: high-throughput clonal analysis usingTn5-mediated library construction "
    "and de novo assembly"
)
PDF_URL = "https://www.biorxiv.org/content/biorxiv/early/2018/07/11/367045.full.pdf"
API_URL = "https://api.biorxiv.org/details/biorxiv/10.1101/367045"
SIXTH_TARGET_DOI = "10.1021/acssynbio.9b00275"
SIXTH_PREPRINT_DOI = "10.1101/694448"
SIXTH_TITLE = "Modular Thermal Control of Protein Dimerization"
SIXTH_DISCOVERY_URL = "https://authors.library.caltech.edu/97175/3/acssynbio.9b00275.pdf"
SIXTH_PDF_URL = "https://www.biorxiv.org/content/biorxiv/early/2019/07/13/694448.full.pdf"
SIXTH_API_URL = "https://api.biorxiv.org/details/biorxiv/10.1101/694448"
PDF_PAYLOAD = b"%PDF-1.7\nreviewed biorxiv preprint fixture\n%%EOF\n"
VERIFICATION = {
    "artifact_type": "article_pdf",
    "page_count": 10,
    "identity_check": "doi",
    "title_similarity": 1.0,
    "source_identity_check": "biorxiv_preprint_doi_title_and_posted_date",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record() -> dict[str, Any]:
    return {
        "record_id": f"doi:{TARGET_DOI}",
        "doi": TARGET_DOI,
        "year": 2019,
        "title_source": "Facilitated Large-Scale Sequence Validation Platform Using Tn5-Tagmented Cell Lysates",
        "title_crossref": "Facilitated Large-Scale Sequence Validation Platform Using Tn5-Tagmented Cell Lysates",
        "pdf_candidates": [{"pdf_url": PDF_URL}],
        "figures_total": 4,
        "figures_sbol_visual_compatible": 1,
        "figures_sbol_visual_compliant": 1,
        "figures_best_practices": 0,
        "has_compatible_figures": True,
        "all_compatible_figures_compliant": True,
    }


def _api_payload(**overrides: Any) -> bytes:
    row = {
        "title": PREPRINT_TITLE,
        "doi": PREPRINT_DOI,
        "date": "2018-07-11",
        "version": "1",
        "license": "cc_by_nc_nd",
        "jatsxml": "https://www.biorxiv.org/content/early/2018/07/11/367045.source.xml",
        "published": TARGET_DOI,
        "server": "bioRxiv",
    }
    row.update(overrides)
    return json.dumps(
        {"messages": [{"status": "ok", "category": "all"}], "collection": [row]},
        separators=(",", ":"),
    ).encode()


def _sixth_record() -> dict[str, Any]:
    return {
        "record_id": f"doi:{SIXTH_TARGET_DOI}",
        "doi": SIXTH_TARGET_DOI,
        "year": 2019,
        "title_source": SIXTH_TITLE,
        "title_crossref": SIXTH_TITLE,
        "pdf_candidates": [{"pdf_url": SIXTH_DISCOVERY_URL}],
        "figures_total": 4,
        "figures_sbol_visual_compatible": 3,
        "figures_sbol_visual_compliant": 3,
        "figures_best_practices": 3,
        "has_compatible_figures": True,
        "all_compatible_figures_compliant": True,
    }


def _sixth_api_payload(**overrides: Any) -> bytes:
    row = {
        "title": SIXTH_TITLE,
        "doi": SIXTH_PREPRINT_DOI,
        "date": "2019-07-13",
        "version": "1",
        "license": "cc_no",
        "jatsxml": "https://www.biorxiv.org/content/early/2019/07/13/694448.source.xml",
        "published": SIXTH_TARGET_DOI,
        "server": "bioRxiv",
    }
    row.update(overrides)
    return json.dumps(
        {"messages": [{"status": "ok", "category": "all"}], "collection": [row]},
        separators=(",", ":"),
    ).encode()


class FakeResponse:
    def __init__(self, url: str, content: bytes, content_type: str) -> None:
        self.url = url
        self.content = content
        self.status_code = 200
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None


class FakeClient:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        assert method == "GET"
        self.requests.append(url)
        return self.responses[url]


def _patch_pdf_verification(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        biorxiv,
        "verify_biorxiv_preprint_pdf",
        lambda path, api_row: dict(VERIFICATION),
    )
    monkeypatch.setattr(
        provenance_biorxiv,
        "verify_biorxiv_preprint_pdf",
        lambda path, api_row: dict(VERIFICATION),
    )
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, record: {
            key: value for key, value in VERIFICATION.items() if key != "source_identity_check"
        },
    )
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, record: {
            key: value for key, value in VERIFICATION.items() if key != "source_identity_check"
        },
    )


def _direct_fixture(tmp_path: Path, monkeypatch: Any) -> tuple[Layout, dict[str, Any]]:
    _patch_pdf_verification(monkeypatch)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record()
    write_jsonl(layout.processed / "papers.jsonl", [record])
    client = FakeClient(
        {
            API_URL: FakeResponse(API_URL, _api_payload(), "application/json"),
            PDF_URL: FakeResponse(PDF_URL, PDF_PAYLOAD, "application/pdf"),
        }
    )
    summary = biorxiv.acquire_biorxiv_preprints(
        layout,
        target_dois=[TARGET_DOI],
        client=client,
    )
    assert summary["downloaded"] == 1
    assert summary["transport_verified"] == 1
    assert client.requests == [API_URL, PDF_URL]
    return layout, record


def _artifact_paths(layout: Layout) -> tuple[Path, Path, Path]:
    variant = (
        layout.papers
        / "2019"
        / "10.1021__acssynbio.8b00482"
        / "versions"
        / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    )
    [manifest_path] = variant.glob("metadata__*.json")
    [pdf_path] = variant.glob("paper__*.pdf")
    [api_path] = variant.glob("biorxiv_api__*.json")
    return manifest_path, pdf_path, api_path


def test_direct_acquisition_pins_mapping_and_inventory_marks_verified_non_vor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _direct_fixture(tmp_path, monkeypatch)
    manifest_path, pdf_path, api_path = _artifact_paths(layout)
    manifest = json.loads(manifest_path.read_text())

    assert api_path.read_bytes() == _api_payload()
    assert manifest["preprint_doi"] == PREPRINT_DOI
    assert manifest["biorxiv_published_doi"] == TARGET_DOI
    assert manifest["source_version"] == "submittedVersion"
    assert manifest["historical_evaluated_edition_equivalence_asserted"] is False
    assert manifest["transport_provenance_verification_status"] == (
        BIORXIV_PREPRINT_DIRECT_TRANSPORT
    )
    assert validate_local_article_pdfs(layout, [record]) == []

    summary = build_local_inventory(layout)
    assert summary["papers_with_verified_local_article_pdf"] == 1
    with (layout.reports / "local_corpus_inventory.csv").open(newline="") as handle:
        [row] = list(csv.DictReader(handle))
    [artifact] = json.loads(row["article_artifacts_json"])
    assert artifact["path"] == str(pdf_path.relative_to(layout.root))
    assert artifact["verification_status"] == "verified"
    assert artifact["version"] == "submittedVersion"
    assert artifact["is_version_of_record"] is False
    assert row["preferred_pdf_path"] == artifact["path"]

    backfill = backfill_generic_manifests(layout)
    assert backfill["failed"] == 0
    assert backfill["unchanged"] == 1

    second_client = FakeClient({})
    second = biorxiv.acquire_biorxiv_preprints(
        layout,
        target_dois=[TARGET_DOI],
        client=second_client,
    )
    assert second["already_present"] == 1
    assert second_client.requests == []


def test_sixth_mapping_binds_caltech_discovery_to_direct_biorxiv(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _patch_pdf_verification(monkeypatch)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _sixth_record()
    write_jsonl(layout.processed / "papers.jsonl", [record])
    client = FakeClient(
        {
            SIXTH_API_URL: FakeResponse(SIXTH_API_URL, _sixth_api_payload(), "application/json"),
            SIXTH_PDF_URL: FakeResponse(SIXTH_PDF_URL, PDF_PAYLOAD, "application/pdf"),
        }
    )

    summary = biorxiv.acquire_biorxiv_preprints(
        layout,
        target_dois=[SIXTH_TARGET_DOI],
        client=client,
    )

    assert len(BIORXIV_PREPRINT_CANDIDATES) == 6
    assert summary["scope"] == "six_explicit_biorxiv_predecessor_mappings"
    assert summary["downloaded"] == 1
    assert summary["transport_verified"] == 1
    assert client.requests == [SIXTH_API_URL, SIXTH_PDF_URL]
    variant = (
        layout.papers
        / "2019"
        / "10.1021__acssynbio.9b00275"
        / "versions"
        / BIORXIV_PREPRINT_VARIANT_DIRECTORY
    )
    [manifest_path] = variant.glob("metadata__*.json")
    [pdf_path] = variant.glob("paper__*.pdf")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["preprint_doi"] == SIXTH_PREPRINT_DOI
    assert manifest["biorxiv_published_doi"] == SIXTH_TARGET_DOI
    assert manifest["source_url"] == SIXTH_PDF_URL
    assert manifest["source_license"] is None
    assert validate_local_article_pdfs(layout, [record]) == []

    wrong_discovery_record = {**record, "pdf_candidates": [{"pdf_url": SIXTH_PDF_URL}]}
    errors = biorxiv_preprint_manifest_identity_errors(
        layout,
        manifest,
        wrong_discovery_record,
        variant.parents[1],
        pdf_path,
    )
    assert any("exactly one reviewed discovery candidate" in error for error in errors)


def test_staged_import_is_identity_checked_but_transport_unverified(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _patch_pdf_verification(monkeypatch)
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    record = _record()
    write_jsonl(layout.processed / "papers.jsonl", [record])
    source_dir = tmp_path / "staged"
    source_dir.mkdir()
    (source_dir / "8b00482.api.json").write_bytes(_api_payload())
    (source_dir / "8b00482.pdf").write_bytes(PDF_PAYLOAD)

    summary = biorxiv.acquire_biorxiv_preprints(
        layout,
        source_dir=source_dir,
        target_dois=[TARGET_DOI],
    )
    assert summary["downloaded"] == 1
    assert summary["transport_verified"] == 0
    manifest_path, _, _ = _artifact_paths(layout)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["resolved_url"] is None
    assert manifest["transport_provenance_verification_status"] == (
        BIORXIV_PREPRINT_IMPORTED_TRANSPORT
    )
    assert validate_local_article_pdfs(layout, [record]) == []

    build_local_inventory(layout)
    with (layout.reports / "local_corpus_inventory.csv").open(newline="") as handle:
        [row] = list(csv.DictReader(handle))
    [artifact] = json.loads(row["article_artifacts_json"])
    assert artifact["verification_status"] == "unverified"
    assert row["preferred_pdf_path"] == ""


def test_offline_validation_rejects_semantic_api_and_url_tampering(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _direct_fixture(tmp_path, monkeypatch)
    manifest_path, pdf_path, api_path = _artifact_paths(layout)
    manifest = json.loads(manifest_path.read_text())

    tampered_payload = _api_payload(published="10.1021/acssynbio.wrong")
    tampered_hash = _sha256(tampered_payload)
    tampered_api = api_path.with_name(f"biorxiv_api__{tampered_hash[:12]}.json")
    tampered_api.write_bytes(tampered_payload)
    api_path.unlink()
    manifest.update(
        {
            "biorxiv_api_snapshot_path": str(tampered_api.relative_to(layout.root)),
            "biorxiv_api_snapshot_sha256": tampered_hash,
            "biorxiv_api_snapshot_bytes": len(tampered_payload),
            "source_url": "https://www.biorxiv.org.example/367045.full.pdf",
        }
    )
    write_json(manifest_path, manifest)

    errors = validate_local_article_pdfs(layout, [record])
    assert any("published field does not identify" in error for error in errors)
    assert any("source URL differs from the reviewed candidate" in error for error in errors)
    status, provenance_errors = generic_manifest_provenance(
        layout,
        manifest,
        record,
        manifest_path.parents[2],
        pdf_path,
    )
    assert status == "invalid"
    assert provenance_errors


def test_api_snapshot_duplicate_keys_symlink_and_orphan_are_rejected(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, record = _direct_fixture(tmp_path, monkeypatch)
    manifest_path, pdf_path, api_path = _artifact_paths(layout)
    manifest = json.loads(manifest_path.read_text())
    duplicate_payload = b'{"messages":[{"status":"ok"}],"collection":[],"collection":[]}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        parse_biorxiv_api_payload(duplicate_payload)

    alias_path = api_path.with_name("biorxiv_api__000000000000.json")
    alias_path.symlink_to(api_path.name)
    manifest["biorxiv_api_snapshot_path"] = str(alias_path.relative_to(layout.root))
    manifest["biorxiv_api_snapshot_sha256"] = "0" * 64
    write_json(manifest_path, manifest)
    errors = validate_local_article_pdfs(layout, [record])
    assert any("may not contain symlinks" in error for error in errors)

    orphan = api_path.with_name("biorxiv_api__111111111111.json")
    orphan.write_bytes(b"{}")
    errors = validate_local_article_pdfs(layout, [record])
    assert any("API snapshot has no provenance manifest" in error for error in errors)

    assert pdf_path.is_file()


def test_preprint_pdf_doi_check_rejects_prefix_attack(monkeypatch: Any, tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(PDF_PAYLOAD)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, record: {
            "artifact_type": "article_pdf",
            "page_count": 1,
            "identity_check": "doi",
            "title_similarity": 1.0,
        },
    )

    class FakePage:
        def __init__(self, text: str) -> None:
            self.text = text

        def extract_text(self) -> str:
            return self.text

    class FakeReader:
        def __init__(self, path: Path) -> None:
            self.pages = [
                FakePage(
                    "bioRxiv preprint https://doi.org/10.1101/3670450doi: "
                    "this version posted July 11, 2018"
                )
            ]

    monkeypatch.setattr(pdf, "PdfReader", FakeReader)
    api_row = json.loads(_api_payload())["collection"][0]
    with pytest.raises(ValueError, match="exact-boundary"):
        verify_biorxiv_preprint_pdf(pdf_path, api_row)
