"""PDF-candidate assembly, deduplication, and download-eligibility policy."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

EXPLICIT_OPEN_LICENSES = {
    "cc0",
    "public-domain",
    "cc-by",
    "cc-by-sa",
    "cc-by-nc",
    "cc-by-nc-sa",
    "cc-by-nd",
    "cc-by-nc-nd",
}


def is_publisher_host(url: str) -> bool:
    host = urlparse(url).netloc.lower().split(":", 1)[0]
    return host == "acs.org" or host.endswith(".acs.org")


def candidate_allowed(
    candidate: dict[str, Any], *, allow_license_unknown: bool, include_publisher: bool
) -> bool:
    if not include_publisher and is_publisher_host(candidate["pdf_url"]):
        return False
    return candidate.get("has_explicit_open_license", False) or allow_license_unknown


def is_supplementary_artifact_url(url: str) -> bool:
    path = urlparse(url).path.casefold()
    return any(
        marker in path
        for marker in (
            "_si_",
            "/si/",
            "/suppl_file/",
            "supplementary",
            "supporting-information",
            "supporting_information",
            "suppinfo",
        )
    )


def openalex_pdf_candidates(work: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    locations = list(work.get("locations") or [])
    if work.get("best_oa_location"):
        locations.append(work["best_oa_location"])
    for location in locations:
        pdf_url = location.get("pdf_url")
        if not pdf_url or pdf_url in seen or not location.get("is_oa"):
            continue
        seen.add(pdf_url)
        source = location.get("source") or {}
        license_name = (location.get("license") or "").lower() or None
        candidate = {
            "pdf_url": pdf_url,
            "landing_page_url": location.get("landing_page_url"),
            "license": license_name,
            "license_id": location.get("license_id"),
            "has_explicit_open_license": license_name in EXPLICIT_OPEN_LICENSES,
            "version": location.get("version"),
            "source_name": source.get("display_name"),
            "source_type": source.get("type"),
            "host_organization_name": source.get("host_organization_name"),
            "metadata_source": "OpenAlex",
        }
        candidates.append(candidate)

    version_rank = {"publishedVersion": 0, "acceptedVersion": 1, "submittedVersion": 2}

    def rank_candidate(candidate: dict[str, Any]) -> tuple[Any, ...]:
        host = urlparse(candidate["pdf_url"]).netloc.lower()
        publisher_host = host.endswith("acs.org")
        pmc_host = host.endswith("ncbi.nlm.nih.gov")
        return (
            not candidate["has_explicit_open_license"],
            publisher_host,
            version_rank.get(candidate.get("version"), 3),
            not pmc_host,
            candidate["pdf_url"],
        )

    candidates.sort(key=rank_candidate)
    return candidates


def crossref_pdf_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for link in record.get("crossref_full_text_links", []):
        url = link["url"]
        content_type = (link.get("content_type") or "").lower()
        if "pdf" not in content_type and "/pdf/" not in url.lower():
            continue
        candidates.append(
            {
                "pdf_url": url,
                "landing_page_url": record.get("publisher_url"),
                "license": None,
                "license_id": None,
                "has_explicit_open_license": False,
                "version": (
                    "publishedVersion"
                    if link.get("content_version") == "vor"
                    else link.get("content_version")
                ),
                "source_name": "ACS Synthetic Biology",
                "source_type": "journal",
                "host_organization_name": "American Chemical Society",
                "metadata_source": "Crossref full-text link",
                "intended_application": link.get("intended_application"),
            }
        )
    return candidates


def deduplicate_pdf_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = candidate["pdf_url"]
        parsed = urlparse(url)
        key = url
        if is_publisher_host(url) and "/doi/pdf/" in parsed.path.casefold():
            key = parsed._replace(query="", fragment="").geturl()
        if key not in seen:
            seen.add(key)
            output.append(candidate)
    return output


def europepmc_pdf_candidates(work: dict[str, Any]) -> list[dict[str, Any]]:
    """Return external PDFs that Europe PMC discovered through Unpaywall.

    Europe PMC's own article-rendering URLs are deliberately excluded. Its developer
    guidance directs bulk users to the supported OA download services; those packages
    are acquired separately by :mod:`sbol_visual_eval.corpus.pmc`. Publisher links are
    supplied by Crossref. This function therefore adds only external repository and
    preprint links, while retaining Europe PMC/Unpaywall provenance.
    """

    candidates: list[dict[str, Any]] = []
    full_text_urls = (work.get("fullTextUrlList") or {}).get("fullTextUrl", [])
    for link in full_text_urls:
        url = str(link.get("url") or "").strip()
        site = str(link.get("site") or "").strip()
        host = urlparse(url).netloc.casefold().split(":", 1)[0]
        if not url or str(link.get("documentStyle") or "").casefold() != "pdf":
            continue
        if site.casefold() != "unpaywall":
            continue
        if (
            is_publisher_host(url)
            or host in {"europepmc.org", "ncbi.nlm.nih.gov"}
            or host.endswith((".europepmc.org", ".ncbi.nlm.nih.gov"))
            or is_supplementary_artifact_url(url)
        ):
            continue
        candidates.append(
            {
                "pdf_url": url,
                "landing_page_url": None,
                "license": None,
                "license_id": None,
                "has_explicit_open_license": False,
                "version": None,
                "source_name": f"External full text at {host}",
                "source_type": "repository",
                "host_organization_name": None,
                "metadata_source": "Europe PMC REST API fullTextUrlList (Unpaywall)",
                "europepmc_availability": link.get("availability"),
                "europepmc_availability_code": link.get("availabilityCode"),
                "europepmc_document_style": link.get("documentStyle"),
            }
        )
    return deduplicate_pdf_candidates(candidates)
