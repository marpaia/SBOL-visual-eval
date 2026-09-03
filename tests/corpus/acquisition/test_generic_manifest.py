from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sbol_visual_eval.corpus.acquisition import generic_manifest
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.util.storage import write_json, write_jsonl


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record() -> dict[str, Any]:
    return {
        "record_id": "doi:10.1234/example",
        "doi": "10.1234/EXAMPLE",
        "year": 2023,
        "title_source": "Source title",
        "title_crossref": "Canonical article title",
    }


def _layout_with_record(tmp_path: Path) -> tuple[Layout, dict[str, Any], Path]:
    layout = Layout(tmp_path)
    record = _record()
    layout.processed.mkdir(parents=True)
    write_jsonl(layout.processed / "papers.jsonl", [record])
    paper_dir = layout.papers / "2023" / "10.1234__EXAMPLE"
    paper_dir.mkdir(parents=True)
    return layout, record, paper_dir


def _fake_verification(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "article_pdf",
        "page_count": 7 if path.name == "paper.pdf" else 11,
        "identity_check": "doi" if path.name == "paper.pdf" else "title",
        "title_similarity": 1.0 if path.name == "paper.pdf" else 0.91,
    }


def test_backfill_enriches_current_and_archived_manifests(tmp_path: Path, monkeypatch: Any) -> None:
    layout, record, paper_dir = _layout_with_record(tmp_path)
    current_payload = b"%PDF-1.7\ncurrent article\n%%EOF\n"
    current_pdf = paper_dir / "paper.pdf"
    current_pdf.write_bytes(current_payload)
    current_provenance = {
        "status": "downloaded",
        "source_url": "https://repository.example/current.pdf",
        "source_name": "Example repository",
        "source_version": "acceptedVersion",
        "source_license": "cc-by",
        "repository_note": "preserve verbatim",
        "doi": "https://doi.org/10.1234/stale",
        "local_path": "stale/path.pdf",
    }
    write_json(paper_dir / "metadata.json", current_provenance)

    archive_dir = paper_dir / "versions" / "unknown_version"
    archive_dir.mkdir(parents=True)
    archive_payload = b"%PDF-1.7\narchived article\n%%EOF\n"
    archive_hash = _sha256(archive_payload)
    archive_pdf = archive_dir / f"paper__{archive_hash[:12]}.pdf"
    archive_pdf.write_bytes(archive_payload)
    archive_manifest_path = archive_dir / f"metadata__{archive_hash[:12]}.json"
    archive_provenance = {
        "source_url": "https://repository.example/archived.pdf",
        "source_metadata_source": "OpenAlex",
        "local_path": "data/papers/incorrect-archive.pdf",
    }
    write_json(archive_manifest_path, archive_provenance)

    verified: list[tuple[Path, dict[str, Any]]] = []

    def verify(path: Path, expected_record: dict[str, Any]) -> dict[str, Any]:
        verified.append((path, expected_record))
        return _fake_verification(path, expected_record)

    monkeypatch.setattr(pdf, "verify_pdf", verify)

    summary = generic_manifest.backfill_generic_manifests(layout)

    assert summary == {
        "manifests_considered": 2,
        "updated": 2,
        "unchanged": 0,
        "failed": 0,
        "results": [
            {
                "manifest_path": "data/papers/2023/10.1234__EXAMPLE/metadata.json",
                "local_path": "data/papers/2023/10.1234__EXAMPLE/paper.pdf",
                "record_id": record["record_id"],
                "doi": "10.1234/example",
                "status": "updated",
            },
            {
                "manifest_path": (
                    "data/papers/2023/10.1234__EXAMPLE/versions/unknown_version/"
                    f"metadata__{archive_hash[:12]}.json"
                ),
                "local_path": (
                    "data/papers/2023/10.1234__EXAMPLE/versions/unknown_version/"
                    f"paper__{archive_hash[:12]}.pdf"
                ),
                "record_id": record["record_id"],
                "doi": "10.1234/example",
                "status": "updated",
            },
        ],
    }
    assert {path for path, _ in verified} == {current_pdf, archive_pdf}
    assert all(expected == record for _, expected in verified)

    current = json.loads((paper_dir / "metadata.json").read_text())
    assert {
        key: current[key]
        for key in (
            "status",
            "source_url",
            "source_name",
            "source_version",
            "source_license",
            "repository_note",
        )
    } == {
        key: current_provenance[key]
        for key in (
            "status",
            "source_url",
            "source_name",
            "source_version",
            "source_license",
            "repository_note",
        )
    }
    assert (
        current.items()
        >= {
            "schema_version": 1,
            "record_id": record["record_id"],
            "doi": "10.1234/example",
            "year": 2023,
            "title": "Canonical article title",
            "local_path": "data/papers/2023/10.1234__EXAMPLE/paper.pdf",
            "bytes": len(current_payload),
            "sha256": _sha256(current_payload),
            "artifact_type": "article_pdf",
            "page_count": 7,
            "identity_check": "doi",
            "title_similarity": 1.0,
            "version_assertion_method": "upstream_metadata",
            "historical_evaluated_edition_relation": "not_established",
        }.items()
    )

    archived = json.loads(archive_manifest_path.read_text())
    assert (
        archived.items()
        >= {
            "schema_version": 1,
            "record_id": record["record_id"],
            "doi": "10.1234/example",
            "year": 2023,
            "title": "Canonical article title",
            "local_path": (
                "data/papers/2023/10.1234__EXAMPLE/versions/unknown_version/"
                f"paper__{archive_hash[:12]}.pdf"
            ),
            "bytes": len(archive_payload),
            "sha256": archive_hash,
            "artifact_type": "article_pdf",
            "page_count": 11,
            "identity_check": "title",
            "title_similarity": 0.91,
            "version_assertion_method": "unknown",
            "historical_evaluated_edition_relation": "not_established",
        }.items()
    )
    assert archived["source_url"] == archive_provenance["source_url"]
    assert archived["source_metadata_source"] == "OpenAlex"
    assert "source_version" not in archived
    assert "source_license" not in archived


def test_backfill_is_idempotent_and_preserves_future_schema(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, _, paper_dir = _layout_with_record(tmp_path)
    pdf_path = paper_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nidempotent article\n%%EOF\n")
    write_json(
        paper_dir / "metadata.json",
        {"schema_version": 2, "source_version": "submittedVersion"},
    )
    monkeypatch.setattr(pdf, "verify_pdf", _fake_verification)

    first = generic_manifest.backfill_generic_manifests(layout)
    first_bytes = (paper_dir / "metadata.json").read_bytes()
    second = generic_manifest.backfill_generic_manifests(layout)

    assert first["updated"] == 1
    assert first["failed"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 1
    assert second["failed"] == 0
    assert (paper_dir / "metadata.json").read_bytes() == first_bytes
    assert json.loads(first_bytes)["schema_version"] == 2


def test_backfill_reports_verification_failure_without_mutating_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout, _, paper_dir = _layout_with_record(tmp_path)
    (paper_dir / "paper.pdf").write_bytes(b"%PDF-1.7\nwrong paper\n%%EOF\n")
    manifest_path = paper_dir / "metadata.json"
    write_json(manifest_path, {"source_url": "https://repository.example/wrong.pdf"})
    original = manifest_path.read_bytes()

    def reject(path: Path, record: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("article identity mismatch")

    monkeypatch.setattr(pdf, "verify_pdf", reject)

    summary = generic_manifest.backfill_generic_manifests(layout)

    assert summary["updated"] == 0
    assert summary["unchanged"] == 0
    assert summary["failed"] == 1
    assert summary["results"][0]["status"] == "failed"
    assert "article identity mismatch" in summary["results"][0]["error"]
    assert manifest_path.read_bytes() == original
