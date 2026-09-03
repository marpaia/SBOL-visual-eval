"""Re-deriving and cross-checking the PDF link from an immutable JATS snapshot."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree
from xml.parsers import expat

from ...layout import Layout
from ...util.text import normalize_doi
from ..manifests import is_hex_digest
from .evidence import PMC_WEB_MAX_EVIDENCE_BYTES, _pmc_web_evidence_snapshot
from .urls import _pmc_web_pdf_url_error

PMC_WEB_MAX_ARTICLE_XML_BYTES = PMC_WEB_MAX_EVIDENCE_BYTES


def _pmc_web_xml_local_name(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _pmc_web_preflight_article_xml(payload: bytes) -> None:
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    has_internal_subset = False

    def reject_internal_subset(
        _doctype_name: str,
        _system_id: str | None,
        _public_id: str | None,
        declared_internal_subset: int,
    ) -> None:
        nonlocal has_internal_subset
        has_internal_subset = bool(declared_internal_subset)

    def reject_entity_declaration(*_args: Any) -> None:
        raise ValueError("article XML contains an entity declaration")

    parser.StartDoctypeDeclHandler = reject_internal_subset
    parser.EntityDeclHandler = reject_entity_declaration
    parser.ExternalEntityRefHandler = lambda *_args: 1
    try:
        parser.Parse(payload, True)
    except ValueError:
        raise
    except expat.ExpatError as error:
        raise ValueError("article XML is not well formed") from error
    if has_internal_subset:
        raise ValueError("article XML contains a DOCTYPE internal subset")


def _pmc_web_article_xml_pdf_url(
    payload: bytes,
    record: dict[str, Any],
    pmcid: str,
    *,
    expected_manuscript_id: str | None = None,
) -> str:
    """Re-derive one identity-bound PMC PDF URL from an immutable JATS snapshot."""

    if not payload or len(payload) > PMC_WEB_MAX_ARTICLE_XML_BYTES:
        raise ValueError("article XML has an invalid byte count")
    try:
        decoded_payload = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("article XML is not strict UTF-8") from error
    declaration = re.match(
        r"\A\s*<\?xml\b(?P<attributes>.*?)\?>",
        decoded_payload,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if declaration is not None:
        encoding = re.search(
            r"\bencoding\s*=\s*(['\"])([^'\"]+)\1",
            declaration.group("attributes"),
            flags=re.IGNORECASE,
        )
        if encoding is not None and encoding.group(2).upper() not in {"UTF-8", "UTF8"}:
            raise ValueError("article XML declaration does not specify UTF-8")
    _pmc_web_preflight_article_xml(payload)
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise ValueError("article XML is not well formed") from error

    if _pmc_web_xml_local_name(root.tag) != "article":
        raise ValueError("article XML root is not article")
    fronts = [child for child in root if _pmc_web_xml_local_name(child.tag) == "front"]
    if len(fronts) != 1:
        raise ValueError("article XML does not have exactly one direct front")
    article_metadata = [
        child for child in fronts[0] if _pmc_web_xml_local_name(child.tag) == "article-meta"
    ]
    if len(article_metadata) != 1:
        raise ValueError("article XML does not have exactly one direct article-meta")

    pmcids: list[str] = []
    dois: list[str] = []
    manuscript_ids: list[str] = []
    pdf_hrefs: list[str | None] = []
    for element in article_metadata[0]:
        tag = _pmc_web_xml_local_name(element.tag)
        if tag == "article-id":
            identifier_type = str(element.attrib.get("pub-id-type") or "").strip().casefold()
            value = "".join(element.itertext()).strip()
            if identifier_type == "pmcid":
                pmcids.append(value)
            elif identifier_type == "doi":
                dois.append(value)
            elif identifier_type == "manuscript-id":
                manuscript_ids.append(value)
        elif (
            tag == "self-uri"
            and str(element.attrib.get("content-type") or "").strip().casefold() == "pmc-pdf"
        ):
            pdf_hrefs.append(element.attrib.get("{http://www.w3.org/1999/xlink}href"))

    if len(pmcids) != 1 or pmcids[0].strip().upper() != pmcid:
        raise ValueError("article XML does not uniquely identify the record PMCID")
    if len(dois) != 1 or normalize_doi(dois[0]) != normalize_doi(record.get("doi")):
        raise ValueError("article XML does not uniquely identify the record DOI")
    if len(manuscript_ids) != 1 or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9._-]{1,127}", manuscript_ids[0]
    ):
        raise ValueError("article XML has no unique safe manuscript identifier")
    if (
        expected_manuscript_id is not None
        and manuscript_ids[0].casefold() != expected_manuscript_id.casefold()
    ):
        raise ValueError("article XML manuscript identifier differs from package metadata")
    if len(pdf_hrefs) != 1 or not isinstance(pdf_hrefs[0], str):
        raise ValueError("article XML has no unique pmc-pdf self-uri")

    basename = pdf_hrefs[0].strip()
    if (
        len(basename) > 255
        or "%" in basename
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.pdf", basename)
    ):
        raise ValueError("article XML pmc-pdf self-uri is not a safe PDF basename")
    pdf_url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/{basename}"
    url_error = _pmc_web_pdf_url_error(pdf_url, pmcid, "article XML pmc-pdf self-uri")
    if url_error is not None:
        raise ValueError("article XML pmc-pdf self-uri is not a safe main PDF")
    return pdf_url


def _pmc_web_article_xml_evidence(
    layout: Layout,
    manifest: dict[str, Any],
    record: dict[str, Any],
    paper_dir: Path,
    upstream_manifest: dict[str, Any] | None,
    pmcid: str,
    errors: list[str],
) -> None:
    _, xml_payload = _pmc_web_evidence_snapshot(
        layout,
        manifest,
        paper_dir,
        path_field="pmc_article_xml_snapshot_path",
        sha256_field="pmc_article_xml_snapshot_sha256",
        bytes_field="pmc_article_xml_snapshot_bytes",
        filename_stem="pmc_article_xml",
        filename_suffix=".xml",
        errors=errors,
    )

    article_artifact: dict[str, Any] | None = None
    if upstream_manifest is not None:
        artifacts = upstream_manifest.get("artifacts")
        if not isinstance(artifacts, list):
            errors.append("PMC web upstream manifest has no artifact list")
        else:
            candidates = [
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict)
                and artifact.get("role") == "article_xml"
                and artifact.get("status") in {"downloaded", "already_present"}
            ]
            if len(candidates) != 1:
                errors.append(
                    "PMC web upstream manifest does not identify exactly one accepted article XML"
                )
            else:
                article_artifact = candidates[0]

    if article_artifact is not None:
        artifact_path = article_artifact.get("local_path")
        original_path = manifest.get("pmc_article_xml_original_path")
        versioned_pmcid = upstream_manifest.get("versioned_pmcid")
        original_filename: str | None = None
        canonical_artifact_path: str | None = None
        if isinstance(versioned_pmcid, str) and re.fullmatch(
            r"PMC\d+\.\d+", versioned_pmcid, flags=re.IGNORECASE
        ):
            expected_original = paper_dir / "pmc" / f"{versioned_pmcid}.xml"
            try:
                canonical_artifact_path = expected_original.relative_to(layout.root).as_posix()
            except ValueError:
                errors.append("PMC web article XML has no canonical repository-relative path")
            original_filename = expected_original.name
        if (
            not isinstance(artifact_path, str)
            or not artifact_path
            or Path(artifact_path).is_absolute()
        ):
            errors.append("PMC web upstream article XML has no repository-relative local path")
        else:
            if artifact_path != canonical_artifact_path:
                errors.append("PMC web upstream article XML local path is not canonical")
            if original_path != artifact_path:
                errors.append(
                    "PMC web article XML original path differs from its upstream manifest"
                )
            declared_path = layout.root / artifact_path
            candidate = declared_path
            while candidate != layout.root:
                if candidate.is_symlink():
                    errors.append("PMC web upstream article XML path may not contain symlinks")
                    break
                if candidate.parent == candidate:
                    break
                candidate = candidate.parent
            try:
                resolved_path = declared_path.resolve()
                resolved_path.relative_to(paper_dir.resolve())
            except ValueError:
                errors.append("PMC web upstream article XML path escapes its paper directory")
            else:
                if (
                    resolved_path.parent != (paper_dir / "pmc").resolve()
                    or resolved_path.suffix.casefold() != ".xml"
                ):
                    errors.append(
                        "PMC web upstream article XML is not directly in the paper's PMC directory"
                    )

        artifact_hash = article_artifact.get("sha256")
        if not is_hex_digest(artifact_hash, 64):
            errors.append("PMC web upstream article XML has no valid SHA-256")
        else:
            snapshot_hash = manifest.get("pmc_article_xml_snapshot_sha256")
            if (
                is_hex_digest(snapshot_hash, 64)
                and artifact_hash.casefold() != snapshot_hash.casefold()
            ):
                errors.append(
                    "PMC web article XML snapshot hash differs from its upstream manifest"
                )
        artifact_bytes = article_artifact.get("bytes")
        if (
            not isinstance(artifact_bytes, int)
            or isinstance(artifact_bytes, bool)
            or not 0 < artifact_bytes <= PMC_WEB_MAX_ARTICLE_XML_BYTES
        ):
            errors.append("PMC web upstream article XML has an invalid byte count")
        elif manifest.get("pmc_article_xml_snapshot_bytes") != artifact_bytes:
            errors.append(
                "PMC web article XML snapshot byte count differs from its upstream manifest"
            )

        artifact_md5 = article_artifact.get("md5")
        if not is_hex_digest(artifact_md5, 32):
            errors.append("PMC web upstream article XML has no valid MD5")
            artifact_md5 = None
        elif xml_payload is not None and (
            hashlib.md5(xml_payload, usedforsecurity=False).hexdigest() != artifact_md5.casefold()
        ):
            errors.append("PMC web article XML snapshot MD5 differs from its upstream manifest")

        package_metadata = upstream_manifest.get("package_metadata")
        package_xml_url = (
            package_metadata.get("xml_url") if isinstance(package_metadata, dict) else None
        )
        if not isinstance(package_xml_url, str) or not package_xml_url:
            errors.append("PMC web upstream package metadata has no article XML URL")
        elif article_artifact.get("source_url") != package_xml_url:
            errors.append("PMC web upstream article XML source URL differs from package metadata")
        if isinstance(package_xml_url, str) and package_xml_url:
            parsed_xml_url = urlparse(package_xml_url)
            expected_path = (
                f"/{versioned_pmcid}/{original_filename}"
                if isinstance(versioned_pmcid, str) and original_filename is not None
                else None
            )
            expected_query = f"md5={artifact_md5.casefold()}" if artifact_md5 is not None else None
            if (
                parsed_xml_url.scheme != "s3"
                or parsed_xml_url.netloc != "pmc-oa-opendata"
                or parsed_xml_url.username is not None
                or parsed_xml_url.password is not None
                or parsed_xml_url.path != expected_path
                or "%" in parsed_xml_url.path
                or parsed_xml_url.query != expected_query
                or parsed_xml_url.fragment
            ):
                errors.append("PMC web upstream package article XML URL is not canonical")
            else:
                expected_download_url = (
                    "https://pmc-oa-opendata.s3.amazonaws.com"
                    f"{parsed_xml_url.path}?{parsed_xml_url.query}"
                )
                if article_artifact.get("download_url") != expected_download_url:
                    errors.append("PMC web upstream article XML download URL is not canonical")

    if xml_payload is None:
        return
    package_metadata = upstream_manifest.get("package_metadata") if upstream_manifest else None
    expected_manuscript_id = (
        package_metadata.get("mid") if isinstance(package_metadata, dict) else None
    )
    if not isinstance(expected_manuscript_id, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9._-]{1,127}", expected_manuscript_id
    ):
        errors.append("PMC web upstream package metadata has no safe manuscript identifier")
        expected_manuscript_id = None
    try:
        derived_pdf_url = _pmc_web_article_xml_pdf_url(
            xml_payload,
            record,
            pmcid,
            expected_manuscript_id=expected_manuscript_id,
        )
    except ValueError as error:
        errors.append(f"PMC web {error}")
        return
    if manifest.get("source_url") != derived_pdf_url:
        errors.append("PMC web source URL differs from its article XML pmc-pdf self-uri")
    if manifest.get("resolved_url") != derived_pdf_url:
        errors.append("PMC web resolved URL differs from its article XML pmc-pdf self-uri")
