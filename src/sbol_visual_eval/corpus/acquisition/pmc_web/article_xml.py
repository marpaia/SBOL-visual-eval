"""Deriving the canonical PDF link from a locally verified JATS article XML."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree
from xml.parsers import expat

from ...layout import Layout
from .archive import _safe_paper_directory
from .urls import (
    _article_url,
    _is_supplement_link,
    _normalized_doi,
    _normalized_pmcid,
    _validated_pmc_pdf_url,
)

MAX_ARTICLE_XML_BYTES = 16 * 1024 * 1024


def _xml_local_name(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _safe_article_xml_metadata(payload: bytes) -> ElementTree.Element:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("PMC article XML is not UTF-8") from error
    if "\x00" in text:
        raise ValueError("PMC article XML contains a null character")
    declaration = re.match(r"<\?xml\b(.*?)\?>", text, flags=re.IGNORECASE | re.DOTALL)
    if declaration is not None:
        declared_encodings = re.findall(
            r"\bencoding\s*=\s*(['\"])([^'\"]+)\1",
            declaration.group(1),
            flags=re.IGNORECASE,
        )
        if any(value.casefold() not in {"utf-8", "utf8"} for _, value in declared_encodings):
            raise ValueError("PMC article XML declares a non-UTF-8 encoding")
    parser = expat.ParserCreate()
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    def reject_internal_subset(
        _doctype_name: str,
        _system_id: str | None,
        _public_id: str | None,
        has_internal_subset: int,
    ) -> None:
        if has_internal_subset:
            raise ValueError("PMC article XML may not contain an internal DTD subset")

    def reject_entity_declaration(*_args: Any) -> None:
        raise ValueError("PMC article XML may not contain entity declarations")

    parser.StartDoctypeDeclHandler = reject_internal_subset
    parser.EntityDeclHandler = reject_entity_declaration
    parser.ExternalEntityRefHandler = lambda *_args: 1
    try:
        parser.Parse(payload, True)
    except ValueError:
        raise
    except expat.ExpatError as error:
        raise ValueError("PMC article XML is not well formed") from error

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise ValueError("PMC article XML is not well formed") from error
    if _xml_local_name(root.tag) != "article":
        raise ValueError("PMC article XML root is not article")
    fronts = [child for child in root if _xml_local_name(child.tag) == "front"]
    if len(fronts) != 1:
        raise ValueError("PMC article XML does not have exactly one direct front")
    article_metadata = [
        child for child in fronts[0] if _xml_local_name(child.tag) == "article-meta"
    ]
    if len(article_metadata) != 1:
        raise ValueError("PMC article XML does not have exactly one direct article-meta")
    return article_metadata[0]


def _article_xml_pdf_link(
    payload: bytes,
    record: dict[str, Any],
    pmcid: str,
) -> dict[str, str]:
    """Derive one canonical PMC PDF URL from a verified local JATS article XML."""

    normalized_pmcid = _normalized_pmcid(pmcid)
    if normalized_pmcid is None:
        raise ValueError("record has an invalid PMCID")
    record_doi = _normalized_doi(record.get("doi"))
    if record_doi is None:
        raise ValueError("record has an invalid DOI")
    if not payload or len(payload) > MAX_ARTICLE_XML_BYTES:
        raise ValueError("PMC article XML exceeds the response safety limit")
    article_metadata = _safe_article_xml_metadata(payload)

    pmcids: list[str] = []
    dois: list[str] = []
    manuscript_ids: list[str] = []
    pdf_hrefs: list[str | None] = []
    for element in article_metadata:
        tag = _xml_local_name(element.tag)
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

    if len(pmcids) != 1 or pmcids[0].strip().upper() != normalized_pmcid:
        raise ValueError("PMC article XML does not uniquely identify the record PMCID")
    if len(dois) != 1 or _normalized_doi(dois[0]) != record_doi:
        raise ValueError("PMC article XML does not uniquely identify the record DOI")
    if len(manuscript_ids) != 1 or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9._-]{1,127}", manuscript_ids[0]
    ):
        raise ValueError("PMC article XML has no unique safe manuscript identifier")
    if len(pdf_hrefs) != 1 or not isinstance(pdf_hrefs[0], str):
        raise ValueError("PMC article XML has no unique pmc-pdf self-uri")

    basename = pdf_hrefs[0].strip()
    if (
        len(basename) > 255
        or "%" in basename
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.pdf", basename)
        or _is_supplement_link(basename)
    ):
        raise ValueError("PMC article XML pmc-pdf self-uri is not a safe PDF basename")
    pdf_url = _validated_pmc_pdf_url(
        f"{_article_url(normalized_pmcid)}pdf/{basename}",
        normalized_pmcid,
        field_name="PMC article XML pmc-pdf self-uri",
    )
    return {
        "pdf_url": pdf_url,
        "pdf_basename": basename,
        "manuscript_id": manuscript_ids[0],
    }


def _verified_article_xml_artifact(
    layout: Layout,
    record: dict[str, Any],
    manifest: dict[str, Any],
) -> tuple[Path, bytes]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise TypeError("eligible PMC manifest has no artifact list")
    candidates = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict)
        and artifact.get("role") == "article_xml"
        and artifact.get("status") in {"downloaded", "already_present"}
    ]
    if len(candidates) != 1:
        raise ValueError("eligible PMC manifest must identify exactly one article XML artifact")
    artifact = candidates[0]
    local_path = artifact.get("local_path")
    expected_hash = artifact.get("sha256")
    expected_bytes = artifact.get("bytes")
    if not isinstance(local_path, str) or not local_path or Path(local_path).is_absolute():
        raise ValueError("eligible PMC article XML has no repository-relative local path")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise ValueError("eligible PMC article XML has no valid SHA-256")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or not 0 < expected_bytes <= MAX_ARTICLE_XML_BYTES
    ):
        raise ValueError("eligible PMC article XML has an invalid byte count")

    paper_directory = _safe_paper_directory(layout, record).resolve()
    pmc_directory = paper_directory / "pmc"
    path = layout.root / local_path
    if pmc_directory.is_symlink() or path.is_symlink():
        raise ValueError("eligible PMC article XML path may not contain symlinks")
    try:
        resolved_path = path.resolve(strict=True)
    except OSError as error:
        raise ValueError("eligible PMC article XML is missing or unreadable") from error
    if resolved_path.parent != pmc_directory.resolve() or resolved_path.suffix.casefold() != ".xml":
        raise ValueError("eligible PMC article XML is outside the paper's PMC directory")
    try:
        canonical_local_path = resolved_path.relative_to(layout.root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("eligible PMC article XML escapes the repository root") from error
    if local_path != canonical_local_path:
        raise ValueError("eligible PMC article XML local path is not canonical")
    if resolved_path.stat().st_size != expected_bytes:
        raise ValueError("eligible PMC article XML byte count does not match its manifest")
    with resolved_path.open("rb") as handle:
        payload = handle.read(expected_bytes + 1)
    if len(payload) != expected_bytes:
        raise ValueError("eligible PMC article XML changed while it was being read")
    if hashlib.sha256(payload).hexdigest() != expected_hash.casefold():
        raise ValueError("eligible PMC article XML SHA-256 does not match its manifest")

    package_metadata = manifest.get("package_metadata")
    xml_url = package_metadata.get("xml_url") if isinstance(package_metadata, dict) else None
    source_url = artifact.get("source_url")
    download_url = artifact.get("download_url")
    artifact_md5 = artifact.get("md5")
    versioned_pmcid = manifest.get("versioned_pmcid")
    if not isinstance(xml_url, str) or source_url != xml_url:
        raise ValueError("eligible PMC article XML is not bound to package metadata xml_url")
    parsed_xml_url = urlparse(xml_url)
    if (
        not isinstance(versioned_pmcid, str)
        or parsed_xml_url.scheme != "s3"
        or parsed_xml_url.netloc != "pmc-oa-opendata"
        or parsed_xml_url.path != f"/{versioned_pmcid}/{versioned_pmcid}.xml"
        or parsed_xml_url.fragment
        or resolved_path.name != f"{versioned_pmcid}.xml"
    ):
        raise ValueError("eligible PMC article XML has a noncanonical package metadata URL")
    if not isinstance(artifact_md5, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", artifact_md5):
        raise ValueError("eligible PMC article XML has no valid MD5")
    normalized_md5 = artifact_md5.casefold()
    if parsed_xml_url.query != f"md5={normalized_md5}":
        raise ValueError("eligible PMC article XML URL MD5 differs from its artifact")
    expected_download_url = (
        f"https://pmc-oa-opendata.s3.amazonaws.com{parsed_xml_url.path}?{parsed_xml_url.query}"
    )
    if download_url != expected_download_url:
        raise ValueError("eligible PMC article XML has a noncanonical HTTPS download URL")
    if hashlib.md5(payload, usedforsecurity=False).hexdigest() != normalized_md5:
        raise ValueError("eligible PMC article XML MD5 does not match its bytes")
    return resolved_path, payload
