from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.util.storage import write_json
from sbol_visual_eval.corpus.validation import validate_local_article_pdfs


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record() -> dict[str, Any]:
    return {
        "record_id": "doi:10.1234/example",
        "doi": "10.1234/example",
        "year": 2023,
        "title_source": "Example paper",
        "title_crossref": "Example paper",
    }


def _write_pdf(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return _sha256(payload)


def _generic_manifest(
    layout: Layout,
    record: dict[str, Any],
    pdf_path: Path,
    sha256: str,
    *,
    source_version: str | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "record_id": record["record_id"],
        "doi": record["doi"],
        "year": record["year"],
        "title": record["title_crossref"],
        "artifact_type": "article_pdf",
        "local_path": str(pdf_path.relative_to(layout.root)),
        "source_url": "https://example.test/article.pdf",
        "bytes": pdf_path.stat().st_size,
        "sha256": sha256,
        "version_assertion_method": ("upstream_metadata" if source_version else "unknown"),
        "historical_evaluated_edition_relation": "not_established",
    }
    if source_version:
        manifest["source_version"] = source_version
    return manifest


def test_local_pdf_validation_covers_current_and_archived_sources(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _record()
    paper_dir = layout.papers / "2023" / "10.1234__example"

    root_pdf = paper_dir / "paper.pdf"
    root_hash = _write_pdf(root_pdf, b"generic current")
    write_json(
        paper_dir / "metadata.json",
        _generic_manifest(layout, record, root_pdf, root_hash),
    )

    generic_archive = paper_dir / "versions" / "acceptedVersion"
    generic_payload = b"generic archive"
    generic_hash = _write_pdf(
        generic_archive / f"paper__{_sha256(generic_payload)[:12]}.pdf",
        generic_payload,
    )
    write_json(
        generic_archive / f"metadata__{generic_hash[:12]}.json",
        _generic_manifest(
            layout,
            record,
            generic_archive / f"paper__{generic_hash[:12]}.pdf",
            generic_hash,
            source_version="acceptedVersion",
        ),
    )

    publisher_pdf = paper_dir / "publisher" / "paper.pdf"
    publisher_hash = _write_pdf(publisher_pdf, b"publisher current")
    publisher_relative_path = str(publisher_pdf.relative_to(layout.root))
    publisher_manifest = {
        "record_id": record["record_id"],
        "doi": record["doi"],
        "artifact_type": "article_pdf",
        "source_version": "publishedVersion",
        "local_path": publisher_relative_path,
        "bytes": len(b"publisher current"),
        "sha256": publisher_hash,
    }
    write_json(paper_dir / "publisher.json", publisher_manifest)

    publisher_archive = paper_dir / "publisher" / "replaced"
    archived_publisher_payload = b"publisher archive"
    archived_publisher_hash = _write_pdf(
        publisher_archive / f"paper__{_sha256(archived_publisher_payload)[:12]}.pdf",
        archived_publisher_payload,
    )
    write_json(
        publisher_archive / f"publisher__{archived_publisher_hash[:12]}.json",
        {
            **publisher_manifest,
            "local_path": str(
                (publisher_archive / f"paper__{archived_publisher_hash[:12]}.pdf").relative_to(
                    layout.root
                )
            ),
            "bytes": len(archived_publisher_payload),
            "sha256": archived_publisher_hash,
        },
    )

    verified: list[Path] = []
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: verified.append(path),
    )

    errors = validate_local_article_pdfs(layout, [record])

    assert errors == []
    assert set(verified) == {
        root_pdf,
        generic_archive / f"paper__{generic_hash[:12]}.pdf",
        publisher_pdf,
        publisher_archive / f"paper__{archived_publisher_hash[:12]}.pdf",
    }


def test_local_pdf_validation_rejects_bad_identity_path_hash_and_pdf(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _record()
    paper_dir = layout.papers / "2023" / "10.1234__example"

    generic_archive = paper_dir / "versions" / "acceptedVersion"
    archived_generic_pdf = generic_archive / f"paper__{'0' * 12}.pdf"
    _write_pdf(archived_generic_pdf, b"corrupt generic archive")
    write_json(
        generic_archive / f"metadata__{'0' * 12}.json",
        {
            "schema_version": 0,
            "record_id": "doi:10.1234/wrong",
            "doi": "10.1234/wrong",
            "year": 2022,
            "title": "Wrong title",
            "artifact_type": "supplementary_pdf",
            "local_path": "outside.pdf",
            "bytes": -1,
            "sha256": "0" * 64,
            "source_version": "acceptedVersion",
            "version_assertion_method": "unknown",
            "historical_evaluated_edition_relation": "unknown",
        },
    )

    publisher_pdf = paper_dir / "publisher" / "paper.pdf"
    publisher_hash = _write_pdf(publisher_pdf, b"publisher current")
    outside_pdf = layout.root / "outside.pdf"
    _write_pdf(outside_pdf, b"outside")
    write_json(
        paper_dir / "publisher.json",
        {
            "record_id": "doi:10.1234/wrong",
            "doi": "10.1234/wrong",
            "artifact_type": "supplementary_pdf",
            "source_version": "acceptedVersion",
            "local_path": str(outside_pdf.relative_to(layout.root)),
            "sha256": publisher_hash,
        },
    )
    orphan_generic = paper_dir / "versions" / "orphan" / "paper__orphan.pdf"
    _write_pdf(orphan_generic, b"generic archive without metadata")
    orphan_publisher = layout.papers / "2022" / "10.1234__orphan" / "publisher" / "paper.pdf"
    _write_pdf(orphan_publisher, b"publisher PDF without manifest")

    publisher_archive = paper_dir / "publisher" / "replaced"
    archived_publisher_payload = b"publisher archive with wrong identity"
    archived_publisher_hash = _write_pdf(
        publisher_archive / f"paper__{_sha256(archived_publisher_payload)[:12]}.pdf",
        archived_publisher_payload,
    )
    write_json(
        publisher_archive / f"publisher__{archived_publisher_hash[:12]}.json",
        {
            "record_id": record["record_id"],
            "doi": record["doi"],
            "artifact_type": "article_pdf",
            "source_version": "publishedVersion",
            "sha256": archived_publisher_hash,
        },
    )
    _write_pdf(publisher_archive / "paper__orphan.pdf", b"publisher archive orphan")

    def fake_verify_pdf(path: Path, expected_record: dict[str, Any]) -> None:
        if "replaced" in path.parts:
            raise ValueError("article identity mismatch")

    monkeypatch.setattr(pdf, "verify_pdf", fake_verify_pdf)

    errors = validate_local_article_pdfs(layout, [record])

    assert any("downloaded paper hash mismatch" in error for error in errors)
    assert any("generic manifest has an invalid schema_version" in error for error in errors)
    assert any("generic manifest record_id mismatch" in error for error in errors)
    assert any("generic manifest DOI mismatch" in error for error in errors)
    assert any("generic manifest year mismatch" in error for error in errors)
    assert any("generic manifest title mismatch" in error for error in errors)
    assert any("generic manifest artifact_type is not article_pdf" in error for error in errors)
    assert any(
        "generic manifest local_path escapes its paper directory" in error for error in errors
    )
    assert any("generic manifest version assertion is inconsistent" in error for error in errors)
    assert any(
        "generic manifest historical-edition relation is unrecognized" in error for error in errors
    )
    assert any("generic manifest byte count mismatch" in error for error in errors)
    assert any("publisher record_id mismatch" in error for error in errors)
    assert any("publisher DOI mismatch" in error for error in errors)
    assert any("publisher artifact type is not article_pdf" in error for error in errors)
    assert any("not explicitly identified as a Version of Record" in error for error in errors)
    assert any("publisher local_path escapes its paper directory" in error for error in errors)
    assert any("archived paper PDF has no provenance metadata" in error for error in errors)
    assert any("publisher PDF has no provenance manifest" in error for error in errors)
    assert any("archived publisher PDF has no provenance manifest" in error for error in errors)
    assert any(
        "downloaded paper validation failed" in error and "article identity mismatch" in error
        for error in errors
    )
