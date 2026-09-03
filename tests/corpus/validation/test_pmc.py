from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.provenance import pdf
from sbol_visual_eval.corpus.provenance.pmc import (
    PMC_PACKAGE_CANONICALIZATION,
    PMC_PACKAGE_HASH_SCOPE,
)
from sbol_visual_eval.corpus.util.storage import canonical_json_sha256, write_json
from sbol_visual_eval.corpus.validation import validate_pmc_manifests


def _record() -> dict[str, Any]:
    return {
        "record_id": "doi:10.1234/example",
        "doi": "10.1234/example",
        "year": 2023,
        "title_source": "Example paper",
        "title_crossref": "Example paper",
        "europepmc_pmcid": "PMC123",
    }


def _digests(payload: bytes) -> tuple[str, str]:
    return hashlib.md5(payload).hexdigest(), hashlib.sha256(payload).hexdigest()


def _write_artifact(
    layout: Layout,
    path: Path,
    *,
    role: str,
    payload: bytes,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    md5, sha256 = _digests(payload)
    return {
        "role": role,
        "status": "downloaded",
        "source_url": f"s3://pmc-oa-opendata/PMC123.1/{path.name}?md5={md5}",
        "local_path": str(path.relative_to(layout.root)),
        "bytes": len(payload),
        "md5": md5,
        "sha256": sha256,
        "is_image_asset": role == "media",
    }


def _write_valid_manifest(
    layout: Layout, record: dict[str, Any]
) -> tuple[Path, dict[str, Any], Path]:
    paper_dir = layout.papers / "2023" / "10.1234__example"
    package_metadata = {
        "doi": record["doi"],
        "pmcid": "PMC123",
        "version": 1,
        "is_manuscript": False,
        "is_pmc_openaccess": True,
    }
    package_bytes = json.dumps(package_metadata, sort_keys=True).encode()
    package_path = paper_dir / "pmc" / "package_metadata.json"
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(package_bytes)
    pdf_path = paper_dir / "paper.pdf"
    artifacts = [
        _write_artifact(
            layout,
            pdf_path,
            role="article_pdf",
            payload=b"%PDF-1.7\nexample article\n%%EOF\n",
        ),
        _write_artifact(
            layout,
            paper_dir / "pmc" / "article.xml",
            role="article_xml",
            payload=b"<article>example</article>",
        ),
        _write_artifact(
            layout,
            paper_dir / "media" / "figure.png",
            role="media",
            payload=b"figure pixels",
        ),
    ]
    manifest = {
        "schema_version": 2,
        "status": "downloaded",
        "record_id": record["record_id"],
        "doi": record["doi"],
        "pmcid": "PMC123",
        "versioned_pmcid": "PMC123.1",
        "pmc_version": 1,
        "source_version": "published_version",
        "is_manuscript": False,
        "is_pmc_openaccess": True,
        "package_metadata_url": ("https://pmc-oa-opendata.s3.amazonaws.com/metadata/PMC123.1.json"),
        "package_metadata_sha256": hashlib.sha256(package_bytes).hexdigest(),
        "package_metadata_sha256_scope": PMC_PACKAGE_HASH_SCOPE,
        "package_metadata_local_path": str(package_path.relative_to(layout.root)),
        "package_metadata_canonical_sha256": canonical_json_sha256(package_metadata),
        "package_metadata_canonicalization": PMC_PACKAGE_CANONICALIZATION,
        "package_metadata": package_metadata,
        "artifacts": artifacts,
    }
    manifest_path = paper_dir / "pmc.json"
    write_json(manifest_path, manifest)
    return manifest_path, manifest, pdf_path


def test_pmc_validation_accepts_fully_bound_schema_v2_manifest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _record()
    _, _, pdf_path = _write_valid_manifest(layout, record)
    verified: list[Path] = []
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: verified.append(path),
    )

    errors, warnings = validate_pmc_manifests(layout, [record])

    assert errors == []
    assert warnings == []
    assert verified == [pdf_path]


def test_pmc_validation_reports_malformed_manifest(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    record = _record()
    manifest_path = layout.papers / "2023" / "10.1234__example" / "pmc.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{not valid json", encoding="utf-8")

    errors, _ = validate_pmc_manifests(layout, [record])

    assert any("cannot read paper artifact metadata" in error for error in errors)


def test_pmc_validation_rejects_bad_identity_version_and_package_hashes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _record()
    manifest_path, manifest, _ = _write_valid_manifest(layout, record)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, expected_record: None)
    manifest.update(
        {
            "record_id": "doi:10.1234/wrong",
            "doi": "10.1234/wrong",
            "status": "mystery",
            "source_version": "unknown_version",
            "pmc_version": 2,
            "package_metadata_sha256": "0" * 64,
            "package_metadata_canonical_sha256": "0" * 64,
            "package_metadata_sha256_scope": "unknown_scope",
            "package_metadata_canonicalization": "unknown_canonicalization",
        }
    )
    write_json(manifest_path, manifest)

    errors, _ = validate_pmc_manifests(layout, [record])

    expected_fragments = (
        "PMC record_id mismatch",
        "PMC DOI mismatch",
        "unrecognized status",
        "unrecognized source_version",
        "PMC version number mismatch",
        "package metadata version mismatch",
        "source_version disagrees",
        "unrecognized scope",
        "canonicalization is unrecognized",
        "canonical package metadata hash mismatch",
        "raw package metadata hash mismatch",
    )
    for fragment in expected_fragments:
        assert any(fragment in error for error in errors), fragment


def test_pmc_validation_rejects_artifact_path_hash_role_identity_and_orphans(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _record()
    manifest_path, manifest, pdf_path = _write_valid_manifest(layout, record)
    outside_path = layout.root / "outside.pdf"
    outside_path.write_bytes(b"outside")
    manifest["artifacts"][0]["local_path"] = str(outside_path.relative_to(layout.root))
    manifest["artifacts"][0]["role"] = "unknown_role"
    manifest["artifacts"][0]["status"] = "unknown_status"
    identity_pdf = _write_artifact(
        layout,
        pdf_path,
        role="article_pdf",
        payload=b"%PDF-1.7\nwrong article\n%%EOF\n",
    )
    manifest["artifacts"].append(identity_pdf)
    missing_hash_xml = _write_artifact(
        layout,
        manifest_path.parent / "pmc" / "missing-hash.xml",
        role="article_xml",
        payload=b"<article />",
    )
    missing_hash_xml.pop("sha256")
    missing_hash_xml.pop("md5")
    manifest["artifacts"].append(missing_hash_xml)
    orphan_path = manifest_path.parent / "pmc" / "orphan.txt"
    orphan_path.write_text("orphan", encoding="utf-8")
    write_json(manifest_path, manifest)
    monkeypatch.setattr(
        pdf,
        "verify_pdf",
        lambda path, expected_record: (_ for _ in ()).throw(ValueError("article mismatch")),
    )

    errors, _ = validate_pmc_manifests(layout, [record])

    expected_fragments = (
        "unrecognized role",
        "unrecognized status",
        "local_path escapes its paper directory",
        "has no valid SHA-256",
        "has no valid MD5",
        "PMC article PDF identity failed",
        "article mismatch",
        "orphan PMC file",
    )
    for fragment in expected_fragments:
        assert any(fragment in error for error in errors), fragment


def test_pmc_validation_marks_schema_v1_raw_metadata_hash_as_legacy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    layout = Layout(tmp_path)
    record = _record()
    manifest_path, manifest, _ = _write_valid_manifest(layout, record)
    package_path = manifest_path.parent / "pmc" / "package_metadata.json"
    package_path.unlink()
    manifest["schema_version"] = 1
    for field in (
        "package_metadata_sha256_scope",
        "package_metadata_local_path",
        "package_metadata_canonical_sha256",
        "package_metadata_canonicalization",
    ):
        manifest.pop(field)
    write_json(manifest_path, manifest)
    monkeypatch.setattr(pdf, "verify_pdf", lambda path, expected_record: None)

    errors, warnings = validate_pmc_manifests(layout, [record])

    assert errors == []
    assert len(warnings) == 1
    assert "schema-v1 PMC manifests" in warnings[0]
