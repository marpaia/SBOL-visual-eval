"""Identity checks binding a PMC-web manifest to its record and evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ...layout import Layout
from ..pmc import pmc_manifest_provenance
from .article_xml import _pmc_web_article_xml_evidence
from .evidence import (
    PMC_WEB_VARIANT_DIRECTORY,
    _pmc_web_evidence_snapshot,
    _pmc_web_page_evidence,
)
from .urls import _normalized_pmcid, _pmc_web_article_url_error, _pmc_web_pdf_url_error

PMC_WEB_SOURCE_NAME = "NIH NLM PubMed Central public article page"
PMC_WEB_SOURCE_ACCESS = "public PMC browser session with same-origin cookies"


PMC_WEB_TRANSPORT_STATUS = "browser_supplied_unverified"
PMC_WEB_TRANSPORT_LIMITATION = (
    "The loopback bridge verifies the PDF's DOI/title identity but cannot independently "
    "prove that browser-supplied bytes came from the recorded source URL."
)
PMC_WEB_AUTHOR_MANUSCRIPT_METHODS = frozenset(
    {
        "pmc_acquisition_manifest",
        "pmc_public_page_author_manuscript_banner",
        "pmc_nihms_pdf_basename",
        "pmc_public_page_banner_and_nihms_pdf_basename",
    }
)
PMC_WEB_EXPLICIT_LICENSE_CODES = frozenset(
    {
        "CC0",
        "CC BY",
        "CC BY-SA",
        "CC BY-NC",
        "CC BY-NC-SA",
        "CC BY-ND",
        "CC BY-NC-ND",
        "PUBLIC DOMAIN",
    }
)


def _pmc_web_explicit_license(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).upper()
    return value.strip() if normalized in PMC_WEB_EXPLICIT_LICENSE_CODES else None


def pmc_web_generic_manifest_identity_errors(
    layout: Layout,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    pdf_path: Path,
) -> list[str]:
    expected_variant_dir = paper_dir / "versions" / PMC_WEB_VARIANT_DIRECTORY
    source_specific_markers = (
        pdf_path.parent.resolve() == expected_variant_dir.resolve(),
        manifest.get("source_name") == PMC_WEB_SOURCE_NAME,
        manifest.get("source_access") == PMC_WEB_SOURCE_ACCESS,
        manifest.get("source_metadata_source")
        in {
            "PMC package metadata and public article HTML",
            "PMC package metadata and verified article XML",
            "PMC public article HTML",
        },
        any(
            isinstance(manifest.get(field_name), str)
            and (urlparse(manifest[field_name]).hostname or "").casefold() == "pmc.ncbi.nlm.nih.gov"
            for field_name in (
                "source_url",
                "resolved_url",
                "source_landing_page_url",
                "discovered_from_url",
            )
        ),
        "pmcid" in manifest,
        "source_version_assertion_method" in manifest,
        "pmc_manifest_path" in manifest,
        "source_landing_page_snapshot_path" in manifest,
        "pmc_package_metadata_snapshot_path" in manifest,
        "pmc_article_xml_snapshot_path" in manifest,
        "transport_provenance_verification_status" in manifest,
        "transport_provenance_limitation" in manifest,
    )
    if not any(source_specific_markers):
        return []

    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("PMC web manifest has an unsupported schema_version")
    if pdf_path.parent.resolve() != expected_variant_dir.resolve():
        errors.append("PMC web artifact is not in its dedicated variant directory")
    if manifest.get("status") != "downloaded":
        errors.append("PMC web manifest has an unrecognized status")
    if manifest.get("source_name") != PMC_WEB_SOURCE_NAME:
        errors.append("PMC web manifest has an unrecognized source_name")
    if manifest.get("source_type") != "repository":
        errors.append("PMC web manifest has an unrecognized source_type")
    if manifest.get("source_access") != PMC_WEB_SOURCE_ACCESS:
        errors.append("PMC web manifest has an unrecognized source_access")
    if manifest.get("source_intended_application") != "full_text":
        errors.append("PMC web manifest has an unrecognized intended application")
    if manifest.get("transport_provenance_verification_status") != (PMC_WEB_TRANSPORT_STATUS):
        errors.append("PMC web manifest has an unrecognized transport provenance status")
    if manifest.get("transport_provenance_limitation") != PMC_WEB_TRANSPORT_LIMITATION:
        errors.append("PMC web manifest has an inconsistent transport provenance limitation")
    if manifest.get("source_version") != "author_manuscript":
        errors.append("PMC web manifest does not identify an author manuscript")
    if manifest.get("source_version_assertion") != "author_manuscript":
        errors.append("PMC web source-version assertion is inconsistent")
    if manifest.get("historical_evaluated_edition_equivalence_asserted") is not False:
        errors.append("PMC web manifest does not withhold historical-edition equivalence")

    pmcid = _normalized_pmcid(record.get("europepmc_pmcid"))
    if pmcid is None:
        errors.append("processed paper has no valid PMCID for its PMC web artifact")
        return errors
    if manifest.get("pmcid") != pmcid:
        errors.append("PMC web manifest PMCID mismatch")

    for field_name in ("source_url", "resolved_url"):
        error = _pmc_web_pdf_url_error(manifest.get(field_name), pmcid, field_name)
        if error:
            errors.append(error)
    for field_name in ("source_landing_page_url", "discovered_from_url"):
        error = _pmc_web_article_url_error(manifest.get(field_name), pmcid, field_name)
        if error:
            errors.append(error)

    method = manifest.get("source_version_assertion_method")
    if method not in PMC_WEB_AUTHOR_MANUSCRIPT_METHODS:
        errors.append("PMC web manifest has an unrecognized source-version assertion method")
        return errors
    xml_backed_manifest = (
        method == "pmc_acquisition_manifest"
        and manifest.get("source_metadata_source")
        == "PMC package metadata and verified article XML"
    )

    source_page_payload: bytes | None = None
    if xml_backed_manifest:
        for field_name in (
            "source_landing_page_snapshot_path",
            "source_landing_page_snapshot_sha256",
            "source_landing_page_snapshot_bytes",
        ):
            if manifest.get(field_name) is not None:
                errors.append(f"PMC web XML-evidenced manifest unexpectedly sets {field_name}")
    else:
        _, source_page_payload = _pmc_web_evidence_snapshot(
            layout,
            manifest,
            paper_dir,
            path_field="source_landing_page_snapshot_path",
            sha256_field="source_landing_page_snapshot_sha256",
            bytes_field="source_landing_page_snapshot_bytes",
            filename_stem="source_page",
            filename_suffix=".html",
            errors=errors,
        )
    page_has_banner = False
    page_links_source_pdf = False
    if source_page_payload is not None:
        article_url = manifest.get("source_landing_page_url")
        source_url = manifest.get("source_url")
        if isinstance(article_url, str) and isinstance(source_url, str):
            page_has_banner, page_links_source_pdf = _pmc_web_page_evidence(
                source_page_payload,
                article_url=article_url,
                source_url=source_url,
            )
        if not page_links_source_pdf:
            errors.append("PMC web source-page snapshot does not identify its source PDF")

    source_url = manifest.get("source_url")
    basename = Path(unquote(urlparse(source_url).path)).name if isinstance(source_url, str) else ""
    filename_evidence = bool(
        re.fullmatch(
            r"nihms[-_]?\d+(?:[._-][a-z0-9]+)*\.pdf",
            basename,
            flags=re.IGNORECASE,
        )
    )
    banner_evidence = {
        "kind": "public_page_author_manuscript_banner",
        "value": "Author manuscript",
    }
    basename_evidence = {
        "kind": "pmc_pdf_basename_pattern",
        "value": basename,
    }
    if method == "pmc_acquisition_manifest":
        expected_evidence = [
            {
                "kind": "pmc_manifest_fields",
                "value": "is_manuscript=true; source_version=author_manuscript",
            }
        ]
    elif method == "pmc_public_page_author_manuscript_banner":
        expected_evidence = [banner_evidence]
        if not page_has_banner:
            errors.append("PMC web source-page snapshot has no explicit author-manuscript banner")
    elif method == "pmc_nihms_pdf_basename":
        expected_evidence = [basename_evidence]
        if not filename_evidence:
            errors.append("PMC web source URL does not support its NIHMS filename assertion")
    else:
        expected_evidence = [banner_evidence, basename_evidence]
        if not page_has_banner:
            errors.append("PMC web source-page snapshot has no explicit author-manuscript banner")
        if not filename_evidence:
            errors.append("PMC web source URL does not support its NIHMS filename assertion")
    if manifest.get("source_version_assertion_evidence") != expected_evidence:
        errors.append("PMC web source-version assertion evidence is inconsistent")

    if method == "pmc_acquisition_manifest":
        upstream_manifest: dict[str, Any] | None = None
        _, upstream_manifest_payload = _pmc_web_evidence_snapshot(
            layout,
            manifest,
            paper_dir,
            path_field="pmc_manifest_path",
            sha256_field="pmc_manifest_sha256",
            bytes_field=None,
            filename_stem="pmc_manifest",
            filename_suffix=".json",
            errors=errors,
        )
        package_snapshot_path, package_snapshot_payload = _pmc_web_evidence_snapshot(
            layout,
            manifest,
            paper_dir,
            path_field="pmc_package_metadata_snapshot_path",
            sha256_field="pmc_package_metadata_snapshot_sha256",
            bytes_field="pmc_package_metadata_snapshot_bytes",
            filename_stem="pmc_package_metadata",
            filename_suffix=".json",
            errors=errors,
        )
        original_manifest_path = manifest.get("pmc_manifest_original_path")
        if not isinstance(original_manifest_path, str) or not original_manifest_path:
            errors.append("PMC web manifest has no original upstream PMC manifest path")
        else:
            original_path = Path(original_manifest_path)
            if not original_path.is_absolute():
                original_path = layout.root / original_path
            if original_path.resolve() != (paper_dir / "pmc.json").resolve():
                errors.append("PMC web artifact identifies the wrong original PMC manifest")

        if upstream_manifest_payload is not None:
            try:
                upstream_manifest = json.loads(upstream_manifest_payload)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                errors.append(
                    "PMC web upstream PMC manifest snapshot cannot be read: "
                    f"{type(error).__name__}: {error}"
                )
            else:
                if not isinstance(upstream_manifest, dict):
                    errors.append("PMC web upstream PMC manifest snapshot is not an object")
                    upstream_manifest = None
                else:
                    status, provenance_errors, _ = pmc_manifest_provenance(
                        layout,
                        record,
                        paper_dir,
                        upstream_manifest,
                        package_metadata_override_path=package_snapshot_path,
                    )
                    if status != "verified":
                        errors.extend(
                            f"PMC web upstream provenance: {error}" for error in provenance_errors
                        )
                    if not (
                        upstream_manifest.get("is_manuscript") is True
                        and upstream_manifest.get("source_version") == "author_manuscript"
                    ):
                        errors.append(
                            "PMC web upstream manifest does not establish author-manuscript status"
                        )
                    if manifest.get("versioned_pmcid") != upstream_manifest.get("versioned_pmcid"):
                        errors.append("PMC web versioned PMCID differs from its upstream manifest")
                    if manifest.get("pmc_package_metadata_url") != upstream_manifest.get(
                        "package_metadata_url"
                    ):
                        errors.append(
                            "PMC web package metadata URL differs from its upstream manifest"
                        )
                    if manifest.get("upstream_license_code") != upstream_manifest.get(
                        "license_code"
                    ):
                        errors.append("PMC web license code differs from its upstream manifest")
                    expected_license = _pmc_web_explicit_license(
                        upstream_manifest.get("license_code")
                    )
                    if manifest.get("source_license") != expected_license:
                        errors.append("PMC web source license differs from its upstream manifest")
                    expected_license_source = (
                        "PMC AWS package metadata" if expected_license else None
                    )
                    if manifest.get("license_metadata_source") != expected_license_source:
                        errors.append("PMC web license metadata source is inconsistent")
                    if package_snapshot_payload is not None and (
                        hashlib.sha256(package_snapshot_payload).hexdigest()
                        != str(upstream_manifest.get("package_metadata_sha256") or "").casefold()
                    ):
                        errors.append(
                            "PMC web package metadata snapshot differs from its upstream manifest"
                        )
        if xml_backed_manifest:
            _pmc_web_article_xml_evidence(
                layout,
                manifest,
                record,
                paper_dir,
                upstream_manifest,
                pmcid,
                errors,
            )
        else:
            for field_name in (
                "pmc_article_xml_original_path",
                "pmc_article_xml_snapshot_path",
                "pmc_article_xml_snapshot_sha256",
                "pmc_article_xml_snapshot_bytes",
            ):
                if manifest.get(field_name) is not None:
                    errors.append(f"PMC web HTML-evidenced manifest unexpectedly sets {field_name}")
        if manifest.get("source_metadata_source") not in {
            "PMC package metadata and public article HTML",
            "PMC package metadata and verified article XML",
        }:
            errors.append("PMC web manifest has an inconsistent metadata source")
    else:
        for field_name in (
            "pmc_manifest_path",
            "pmc_manifest_original_path",
            "pmc_manifest_sha256",
            "pmc_package_metadata_url",
            "pmc_package_metadata_snapshot_path",
            "pmc_package_metadata_snapshot_sha256",
            "pmc_package_metadata_snapshot_bytes",
            "pmc_article_xml_original_path",
            "pmc_article_xml_snapshot_path",
            "pmc_article_xml_snapshot_sha256",
            "pmc_article_xml_snapshot_bytes",
            "versioned_pmcid",
            "upstream_license_code",
        ):
            if manifest.get(field_name) is not None:
                errors.append(f"PMC web page-evidenced manifest unexpectedly sets {field_name}")
        if manifest.get("source_license") is not None:
            errors.append("PMC web page-evidenced manifest unexpectedly asserts a license")
        if manifest.get("license_metadata_source") is not None:
            errors.append(
                "PMC web page-evidenced manifest unexpectedly sets a license metadata source"
            )
        if manifest.get("source_metadata_source") != "PMC public article HTML":
            errors.append("PMC web page-evidenced manifest has an inconsistent metadata source")
    return errors
