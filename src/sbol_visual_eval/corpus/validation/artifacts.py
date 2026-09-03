"""Validation of every local article PDF against its provenance manifests."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..provenance.biorxiv import BIORXIV_PREPRINT_VARIANT_DIRECTORY
from ..provenance.generic import generic_manifest_identity_errors, generic_pdf_identity_record
from ..provenance.manifests import read_artifact_metadata, validate_article_pdf_artifact
from ..provenance.pmc_web import PMC_WEB_VARIANT_DIRECTORY
from ..provenance.publisher import publisher_manifest_identity_errors


def _validate_generic_identity(
    layout: Layout,
    manifest_path: Path,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
    errors: list[str],
) -> None:
    display_manifest_path = layout.display_path(manifest_path)
    errors.extend(
        f"{error}: {display_manifest_path}"
        for error in generic_manifest_identity_errors(layout, manifest, record, paper_dir, pdf_path)
    )


def _validate_publisher_identity(
    layout: Layout,
    manifest_path: Path,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
    errors: list[str],
) -> None:
    display_manifest_path = layout.display_path(manifest_path)
    errors.extend(
        f"{error}: {display_manifest_path}"
        for error in publisher_manifest_identity_errors(
            layout, manifest, record, paper_dir, pdf_path
        )
    )


def validate_local_article_pdfs(layout: Layout, records: Sequence[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    records_by_directory = {layout.paper_directory(record).resolve(): record for record in records}

    def record_for_path(paper_dir: Path, metadata_path: Path) -> dict[str, Any] | None:
        record = records_by_directory.get(paper_dir.resolve())
        if record is None:
            errors.append(
                f"paper artifact is not associated with a processed record: "
                f"{layout.display_path(metadata_path)}"
            )
        return record

    for metadata_path in layout.papers.glob("*/*/metadata.json"):
        paper_dir = metadata_path.parent
        record = record_for_path(paper_dir, metadata_path)
        metadata = read_artifact_metadata(layout, metadata_path, errors)
        if record is not None and metadata is not None:
            _validate_generic_identity(
                layout,
                metadata_path,
                metadata,
                record,
                paper_dir,
                paper_dir / "paper.pdf",
                errors,
            )
            validate_article_pdf_artifact(
                layout,
                record,
                paper_dir,
                paper_dir / "paper.pdf",
                metadata_path,
                metadata,
                errors,
                identity_record=generic_pdf_identity_record(
                    metadata,
                    record,
                    paper_dir,
                    paper_dir / "paper.pdf",
                ),
            )

    for metadata_path in layout.papers.glob("*/*/versions/*/metadata__*.json"):
        paper_dir = metadata_path.parents[2]
        record = record_for_path(paper_dir, metadata_path)
        metadata = read_artifact_metadata(layout, metadata_path, errors)
        pdf_path = metadata_path.with_name(
            metadata_path.name.replace("metadata__", "paper__", 1)
        ).with_suffix(".pdf")
        if record is not None and metadata is not None:
            _validate_generic_identity(
                layout,
                metadata_path,
                metadata,
                record,
                paper_dir,
                pdf_path,
                errors,
            )
            expected_sha256 = metadata.get("sha256")
            if isinstance(expected_sha256, str) and not pdf_path.stem.endswith(
                expected_sha256[:12]
            ):
                errors.append(
                    f"archived paper filename does not match its SHA-256: "
                    f"{layout.display_path(pdf_path)}"
                )
            validate_article_pdf_artifact(
                layout,
                record,
                paper_dir,
                pdf_path,
                metadata_path,
                metadata,
                errors,
                identity_record=generic_pdf_identity_record(
                    metadata,
                    record,
                    paper_dir,
                    pdf_path,
                ),
            )

    for pdf_path in layout.papers.glob("*/*/versions/*/paper__*.pdf"):
        metadata_path = pdf_path.with_name(
            pdf_path.name.replace("paper__", "metadata__", 1)
        ).with_suffix(".json")
        if not metadata_path.exists():
            errors.append(
                f"archived paper PDF has no provenance metadata: {layout.display_path(pdf_path)}"
            )

    for manifest_path in layout.papers.glob("*/*/publisher.json"):
        paper_dir = manifest_path.parent
        record = record_for_path(paper_dir, manifest_path)
        manifest = read_artifact_metadata(layout, manifest_path, errors)
        if record is None or manifest is None:
            continue
        expected_pdf_path = paper_dir / "publisher" / "paper.pdf"
        _validate_publisher_identity(
            layout,
            manifest_path,
            manifest,
            record,
            paper_dir,
            expected_pdf_path,
            errors,
        )
        validate_article_pdf_artifact(
            layout,
            record,
            paper_dir,
            expected_pdf_path,
            manifest_path,
            manifest,
            errors,
        )

    for pdf_path in layout.papers.glob("*/*/publisher/paper.pdf"):
        manifest_path = pdf_path.parents[1] / "publisher.json"
        if not manifest_path.exists():
            errors.append(
                f"publisher PDF has no provenance manifest: {layout.display_path(pdf_path)}"
            )

    for manifest_path in layout.papers.glob("*/*/publisher/replaced/publisher__*.json"):
        paper_dir = manifest_path.parents[2]
        record = record_for_path(paper_dir, manifest_path)
        manifest = read_artifact_metadata(layout, manifest_path, errors)
        pdf_path = manifest_path.with_name(
            manifest_path.name.replace("publisher__", "paper__", 1)
        ).with_suffix(".pdf")
        if record is None or manifest is None:
            continue
        _validate_publisher_identity(
            layout,
            manifest_path,
            manifest,
            record,
            paper_dir,
            pdf_path,
            errors,
        )
        expected_sha256 = manifest.get("sha256")
        if isinstance(expected_sha256, str) and not pdf_path.stem.endswith(expected_sha256[:12]):
            errors.append(
                f"archived publisher filename does not match its SHA-256: "
                f"{layout.display_path(pdf_path)}"
            )
        validate_article_pdf_artifact(
            layout,
            record,
            paper_dir,
            pdf_path,
            manifest_path,
            manifest,
            errors,
        )

    for pdf_path in layout.papers.glob("*/*/publisher/replaced/paper__*.pdf"):
        manifest_path = pdf_path.with_name(
            pdf_path.name.replace("paper__", "publisher__", 1)
        ).with_suffix(".json")
        if not manifest_path.exists():
            errors.append(
                f"archived publisher PDF has no provenance manifest: "
                f"{layout.display_path(pdf_path)}"
            )

    referenced_pmc_web_evidence: set[Path] = set()
    for metadata_path in layout.papers.glob(
        f"*/*/versions/{PMC_WEB_VARIANT_DIRECTORY}/metadata__*.json"
    ):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        for field_name in (
            "source_landing_page_snapshot_path",
            "pmc_manifest_path",
            "pmc_package_metadata_snapshot_path",
            "pmc_article_xml_snapshot_path",
        ):
            raw_path = metadata.get(field_name)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            evidence_path = Path(raw_path)
            if not evidence_path.is_absolute():
                evidence_path = layout.root / evidence_path
            referenced_pmc_web_evidence.add(evidence_path.resolve())
    evidence_globs = (
        "source_page__*.html",
        "pmc_manifest__*.json",
        "pmc_package_metadata__*.json",
        "pmc_article_xml__*.xml",
    )
    for pattern in evidence_globs:
        for evidence_path in layout.papers.glob(
            f"*/*/versions/{PMC_WEB_VARIANT_DIRECTORY}/{pattern}"
        ):
            if evidence_path.resolve() not in referenced_pmc_web_evidence:
                errors.append(
                    "PMC web evidence snapshot has no provenance manifest: "
                    f"{layout.display_path(evidence_path)}"
                )

    referenced_biorxiv_api_snapshots: set[Path] = set()
    for metadata_path in layout.papers.glob(
        f"*/*/versions/{BIORXIV_PREPRINT_VARIANT_DIRECTORY}/metadata__*.json"
    ):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        raw_path = metadata.get("biorxiv_api_snapshot_path") if isinstance(metadata, dict) else None
        if isinstance(raw_path, str) and raw_path:
            api_path = Path(raw_path)
            if not api_path.is_absolute():
                api_path = layout.root / api_path
            referenced_biorxiv_api_snapshots.add(api_path.resolve())
    for api_path in layout.papers.glob(
        f"*/*/versions/{BIORXIV_PREPRINT_VARIANT_DIRECTORY}/biorxiv_api__*.json"
    ):
        if api_path.resolve() not in referenced_biorxiv_api_snapshots:
            errors.append(
                f"bioRxiv API snapshot has no provenance manifest: {layout.display_path(api_path)}"
            )
    return errors
