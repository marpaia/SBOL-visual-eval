"""Per-paper collection of every local artifact and its verification outcome."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..layout import Layout
from ..provenance.generic import generic_manifest_provenance, generic_pdf_identity_record
from ..provenance.pmc import (
    PMC_ARTIFACT_ROLES,
    PMC_ARTIFACT_STATUSES,
    pmc_manifest_provenance,
)
from ..provenance.publisher import publisher_manifest_identity_errors
from .artifacts import (
    _add_inventory_article_artifact,
    _inventory_issue,
    _inventory_nonarticle_file,
    _read_inventory_manifest,
)

ARTICLE_TEXT_KINDS = frozenset({"article_xml", "article_text"})


class PaperInventory:
    """Collects and verifies the local artifacts of one processed paper record."""

    def __init__(self, layout: Layout, record: dict[str, Any]) -> None:
        self.layout = layout
        self.record = record
        self.paper_dir = layout.paper_directory(record)
        self.artifacts_by_path: dict[tuple[str, str], dict[str, Any]] = {}
        self.issues: list[dict[str, Any]] = []
        self.media_paths: set[str] = set()
        self.image_paths: set[str] = set()
        self.pmc_referenced_paths: set[Path] = set()
        self.pmcid: Any = None
        self.pmc_status: Any = None

    def collect(self) -> None:
        self._collect_generic_manifest()
        self._collect_archived_variants()
        self._collect_publisher_manifest()
        self._collect_replaced_publisher_pairs()
        self._collect_pmc_manifest()
        self._collect_pmc_orphans()

    def _read_manifest(self, path: Path) -> dict[str, Any] | None:
        return _read_inventory_manifest(self.layout, path, self.issues)

    def _add_generic_article(
        self, metadata: dict[str, Any], manifest_path: Path, pdf_path: Path
    ) -> None:
        provenance_status, provenance_errors = generic_manifest_provenance(
            self.layout, metadata, self.record, self.paper_dir, pdf_path
        )
        _add_inventory_article_artifact(
            self.layout,
            self.record,
            self.paper_dir,
            self.artifacts_by_path,
            pdf_path,
            source=metadata.get("source_name"),
            version=metadata.get("source_version"),
            kind="article_pdf",
            expected_sha256=metadata.get("sha256"),
            source_url=metadata.get("source_url"),
            provenance_path=manifest_path,
            provenance_status=provenance_status,
            provenance_errors=provenance_errors,
            identity_record=generic_pdf_identity_record(
                metadata,
                self.record,
                self.paper_dir,
                pdf_path,
            ),
        )

    def _collect_generic_manifest(self) -> None:
        manifest_path = self.paper_dir / "metadata.json"
        if not manifest_path.exists():
            return
        metadata = self._read_manifest(manifest_path)
        if metadata is not None:
            self._add_generic_article(metadata, manifest_path, self.paper_dir / "paper.pdf")

    def _collect_archived_variants(self) -> None:
        for manifest_path in sorted((self.paper_dir / "versions").glob("*/metadata__*.json")):
            metadata = self._read_manifest(manifest_path)
            if metadata is None:
                continue
            pdf_path = manifest_path.with_name(
                manifest_path.name.replace("metadata__", "paper__", 1)
            ).with_suffix(".pdf")
            self._add_generic_article(metadata, manifest_path, pdf_path)

    def _add_publisher_article(
        self, manifest: dict[str, Any], manifest_path: Path, pdf_path: Path, local_file: Path
    ) -> None:
        provenance_errors = publisher_manifest_identity_errors(
            self.layout,
            manifest,
            self.record,
            self.paper_dir,
            pdf_path,
        )
        _add_inventory_article_artifact(
            self.layout,
            self.record,
            self.paper_dir,
            self.artifacts_by_path,
            local_file,
            source=manifest.get("source_name"),
            version=manifest.get("source_version"),
            kind=manifest.get("artifact_type") or "article_pdf",
            expected_sha256=manifest.get("sha256"),
            source_url=manifest.get("source_url"),
            provenance_path=manifest_path,
            provenance_status=("invalid" if provenance_errors else "verified"),
            provenance_errors=provenance_errors,
        )

    def _collect_publisher_manifest(self) -> None:
        manifest_path = self.paper_dir / "publisher.json"
        manifest = self._read_manifest(manifest_path) if manifest_path.exists() else None
        if manifest is None:
            return
        local_path = manifest.get("local_path")
        if not local_path:
            self.issues.append(
                _inventory_issue(
                    self.layout,
                    manifest_path,
                    kind="provenance_manifest",
                    status="invalid",
                    errors=["publisher manifest has no local_path"],
                )
            )
            return
        local_file = Path(local_path)
        if not local_file.is_absolute():
            local_file = self.layout.root / local_file
        expected_pdf = self.paper_dir / "publisher" / "paper.pdf"
        self._add_publisher_article(manifest, manifest_path, expected_pdf, local_file)

    def _collect_replaced_publisher_pairs(self) -> None:
        for manifest_path in sorted(
            (self.paper_dir / "publisher" / "replaced").glob("publisher__*.json")
        ):
            manifest = self._read_manifest(manifest_path)
            if manifest is None:
                continue
            pdf_path = manifest_path.with_name(
                manifest_path.name.replace("publisher__", "paper__", 1)
            ).with_suffix(".pdf")
            self._add_publisher_article(manifest, manifest_path, pdf_path, pdf_path)

    def _collect_pmc_manifest(self) -> None:
        manifest_path = self.paper_dir / "pmc.json"
        manifest = self._read_manifest(manifest_path) if manifest_path.exists() else None
        if manifest is None:
            return
        self.pmcid = manifest.get("pmcid")
        self.pmc_status = manifest.get("status")
        provenance_status, provenance_errors, package_file = pmc_manifest_provenance(
            self.layout, self.record, self.paper_dir, manifest
        )
        if provenance_status != "verified":
            self.issues.append(
                _inventory_issue(
                    self.layout,
                    manifest_path,
                    kind="pmc_manifest_provenance",
                    status=provenance_status,
                    errors=provenance_errors,
                )
            )
        if package_file is not None:
            self.pmc_referenced_paths.add(package_file.resolve())
            _, issue = _inventory_nonarticle_file(
                self.layout,
                self.paper_dir,
                package_file,
                kind="package_metadata",
                expected_sha256=manifest.get("package_metadata_sha256"),
                provenance_path=manifest_path,
                provenance_status=provenance_status,
                provenance_errors=provenance_errors,
            )
            if issue is not None:
                self.issues.append(issue)

        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            self.issues.append(
                _inventory_issue(
                    self.layout,
                    manifest_path,
                    kind="provenance_manifest",
                    status="invalid",
                    errors=["PMC artifacts is not an array"],
                )
            )
            artifacts = []
        for index, artifact in enumerate(artifacts):
            self._collect_pmc_artifact(
                manifest,
                manifest_path,
                provenance_status,
                provenance_errors,
                index,
                artifact,
            )

    def _collect_pmc_artifact(
        self,
        manifest: dict[str, Any],
        manifest_path: Path,
        provenance_status: str,
        provenance_errors: list[str],
        index: int,
        artifact: Any,
    ) -> None:
        if not isinstance(artifact, dict):
            self.issues.append(
                _inventory_issue(
                    self.layout,
                    manifest_path,
                    kind="provenance_manifest",
                    status="invalid",
                    errors=[f"PMC artifact {index} is not an object"],
                )
            )
            return
        role = artifact.get("role")
        status = artifact.get("status")
        local_path = artifact.get("local_path")
        local_file = None
        if isinstance(local_path, str) and local_path:
            local_file = Path(local_path)
            if not local_file.is_absolute():
                local_file = self.layout.root / local_file
            try:
                local_file.resolve().relative_to(self.paper_dir.resolve())
            except ValueError:
                pass
            else:
                self.pmc_referenced_paths.add(local_file.resolve())
        if role not in PMC_ARTIFACT_ROLES or status not in PMC_ARTIFACT_STATUSES:
            self.issues.append(
                _inventory_issue(
                    self.layout,
                    local_file or manifest_path,
                    kind=str(role or "unknown"),
                    status="invalid",
                    errors=[f"unrecognized PMC role/status: {role!r}/{status!r}"],
                )
            )
            return
        if status == "failed":
            return
        if local_file is None:
            self.issues.append(
                _inventory_issue(
                    self.layout,
                    manifest_path,
                    kind=str(role),
                    status="invalid",
                    errors=[f"PMC artifact {index} has no local_path"],
                )
            )
            return
        if role in {"article_pdf", *ARTICLE_TEXT_KINDS}:
            _add_inventory_article_artifact(
                self.layout,
                self.record,
                self.paper_dir,
                self.artifacts_by_path,
                local_file,
                source=manifest.get("source_name"),
                version=manifest.get("source_version"),
                kind=role,
                expected_sha256=artifact.get("sha256"),
                expected_md5=artifact.get("md5"),
                expected_bytes=artifact.get("bytes"),
                require_md5=True,
                require_bytes=True,
                source_url=artifact.get("source_url"),
                provenance_path=manifest_path,
                provenance_status=provenance_status,
                provenance_errors=provenance_errors,
            )
        elif role == "media":
            media_status, issue = _inventory_nonarticle_file(
                self.layout,
                self.paper_dir,
                local_file,
                kind="media",
                expected_sha256=artifact.get("sha256"),
                expected_md5=artifact.get("md5"),
                expected_bytes=artifact.get("bytes"),
                require_md5=True,
                require_bytes=True,
                provenance_path=manifest_path,
                provenance_status=provenance_status,
                provenance_errors=provenance_errors,
            )
            if media_status == "verified":
                relative_path = self.layout.display_path(local_file.resolve())
                self.media_paths.add(relative_path)
                if artifact.get("is_image_asset"):
                    self.image_paths.add(relative_path)
            elif issue is not None:
                self.issues.append(issue)

    def _collect_pmc_orphans(self) -> None:
        for pmc_directory in (self.paper_dir / "pmc", self.paper_dir / "media"):
            if not pmc_directory.exists():
                continue
            for local_file in pmc_directory.rglob("*"):
                if local_file.is_file() and local_file.resolve() not in self.pmc_referenced_paths:
                    self.issues.append(
                        _inventory_issue(
                            self.layout,
                            local_file,
                            kind="orphan_pmc_file",
                            status="invalid",
                            errors=["file is not referenced by pmc.json"],
                        )
                    )

    def row(self, orphan_root_pdfs: list[str]) -> dict[str, Any]:
        """Aggregate the collected artifacts into one inventory report row."""

        article_artifacts = sorted(
            self.artifacts_by_path.values(),
            key=lambda artifact: (artifact["path"], artifact["kind"]),
        )
        verified_article_artifacts = [
            artifact
            for artifact in article_artifacts
            if artifact["verification_status"] == "verified"
        ]
        local_article_artifacts = [
            artifact for artifact in article_artifacts if artifact["local_file_present"]
        ]
        local_pdf_artifacts = [
            artifact for artifact in local_article_artifacts if artifact["kind"] == "article_pdf"
        ]
        local_text_artifacts = [
            artifact
            for artifact in local_article_artifacts
            if artifact["kind"] in ARTICLE_TEXT_KINDS
        ]
        verified_pdf_artifacts = [
            artifact for artifact in verified_article_artifacts if artifact["kind"] == "article_pdf"
        ]
        verified_text_artifacts = [
            artifact
            for artifact in verified_article_artifacts
            if artifact["kind"] in ARTICLE_TEXT_KINDS
        ]
        source_versions = {
            artifact["version"]
            for artifact in verified_article_artifacts
            if artifact.get("version")
        }
        version_of_record_pdfs = [
            artifact for artifact in verified_pdf_artifacts if artifact["is_version_of_record"]
        ]
        preferred_pdf = min(
            verified_pdf_artifacts,
            key=lambda artifact: (
                not artifact["is_version_of_record"],
                "/replaced/" in artifact["path"] or "/versions/" in artifact["path"],
                artifact["path"],
            ),
            default=None,
        )
        pdf_paths = {artifact["path"] for artifact in local_pdf_artifacts}
        text_paths = {artifact["path"] for artifact in local_text_artifacts}
        verified_pdf_paths = {artifact["path"] for artifact in verified_pdf_artifacts}
        verified_text_paths = {artifact["path"] for artifact in verified_text_artifacts}
        version_of_record_pdf_paths = {artifact["path"] for artifact in version_of_record_pdfs}

        artifact_issues = list(self.issues)
        artifact_issues.extend(
            {
                key: artifact[key]
                for key in (
                    "path",
                    "kind",
                    "verification_status",
                    "verification_errors",
                    "provenance_path",
                )
            }
            for artifact in article_artifacts
            if artifact["verification_status"] != "verified"
        )

        root_pdf = self.paper_dir / "paper.pdf"
        all_article_paths = {artifact["path"] for artifact in article_artifacts}
        root_pdf_path = self.layout.display_path(root_pdf)
        if root_pdf.exists() and root_pdf_path not in pdf_paths:
            orphan_root_pdfs.append(root_pdf_path)
        physical_article_candidates = [
            root_pdf,
            self.paper_dir / "publisher" / "paper.pdf",
            *(self.paper_dir / "versions").glob("*/paper__*.pdf"),
            *(self.paper_dir / "publisher" / "replaced").glob("paper__*.pdf"),
        ]
        for candidate_path in physical_article_candidates:
            if (
                candidate_path.is_file()
                and self.layout.display_path(candidate_path.resolve()) not in all_article_paths
            ):
                artifact_issues.append(
                    _inventory_issue(
                        self.layout,
                        candidate_path,
                        kind="orphan_article_pdf",
                        status="unverified",
                        errors=["PDF has no readable provenance manifest"],
                    )
                )

        artifact_issues.sort(
            key=lambda issue: (issue["path"], issue["kind"], issue["verification_status"])
        )
        invalid_artifact_count = sum(
            issue["verification_status"] == "invalid" for issue in artifact_issues
        )
        unverified_artifact_count = sum(
            issue["verification_status"] == "unverified" for issue in artifact_issues
        )

        record = self.record
        return {
            "record_id": record["record_id"],
            "doi": record.get("doi"),
            "year": record["year"],
            "title": record.get("title_crossref") or record["title_source"],
            "has_local_article_pdf": bool(pdf_paths),
            "has_local_article_text": bool(text_paths),
            "has_local_source": bool(pdf_paths or text_paths),
            "has_verified_local_article_pdf": bool(verified_pdf_paths),
            "has_verified_local_article_text": bool(verified_text_paths),
            "has_verified_local_source": bool(verified_pdf_paths or verified_text_paths),
            "has_local_version_of_record_pdf": bool(version_of_record_pdfs),
            "article_pdf_count": len(pdf_paths),
            "verified_article_pdf_count": len(verified_pdf_paths),
            "verified_article_text_count": len(verified_text_paths),
            "version_of_record_pdf_count": len(version_of_record_pdf_paths),
            "preferred_pdf_path": preferred_pdf["path"] if preferred_pdf else None,
            "preferred_pdf_is_version_of_record": bool(
                preferred_pdf and preferred_pdf["is_version_of_record"]
            ),
            "article_pdf_paths_json": json.dumps(sorted(pdf_paths), ensure_ascii=False),
            "verified_article_pdf_paths_json": json.dumps(
                sorted(verified_pdf_paths), ensure_ascii=False
            ),
            "version_of_record_pdf_paths_json": json.dumps(
                sorted(version_of_record_pdf_paths), ensure_ascii=False
            ),
            "article_text_paths_json": json.dumps(sorted(text_paths), ensure_ascii=False),
            "verified_article_text_paths_json": json.dumps(
                sorted(verified_text_paths), ensure_ascii=False
            ),
            "article_artifacts_json": json.dumps(article_artifacts, ensure_ascii=False),
            "verified_article_artifact_count": len(verified_article_artifacts),
            "invalid_artifact_count": invalid_artifact_count,
            "unverified_artifact_count": unverified_artifact_count,
            "artifact_issues_json": json.dumps(artifact_issues, ensure_ascii=False),
            "source_versions_json": json.dumps(sorted(source_versions), ensure_ascii=False),
            "pmcid": self.pmcid,
            "pmc_status": self.pmc_status,
            "pmc_media_count": len(self.media_paths),
            "pmc_image_count": len(self.image_paths),
            "pmc_media_paths_json": json.dumps(sorted(self.media_paths), ensure_ascii=False),
            "figures_total": record["figures_total"],
            "figures_sbol_visual_compatible": record["figures_sbol_visual_compatible"],
            "figures_sbol_visual_compliant": record["figures_sbol_visual_compliant"],
            "figures_best_practices": record["figures_best_practices"],
            "has_compatible_figures": record["has_compatible_figures"],
            "all_compatible_figures_compliant": record["all_compatible_figures_compliant"],
        }
