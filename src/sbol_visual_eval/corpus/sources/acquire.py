"""Acquisition of the primary raw sources: supplements, rubric, and website."""

from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import (
    ANNIVERSARY_ROOT,
    RETROSPECTIVE_DOI,
    RUBRIC_URL,
    SBOL_VISUAL_3_URL,
    USER_AGENT,
    YEARS,
)
from ..layout import Layout
from ..util.net import download_to_path, request_with_retry
from ..util.storage import md5_file, sha256_file, utc_now, write_json

FIGSHARE_COLLECTION_ID = 8059641
FIGSHARE_API = "https://api.figshare.com/v2"


WEBSITE_REPOSITORY = "https://github.com/SynBioDex/SbolStandardWebsite"
WEBSITE_COMMIT_API = "https://api.github.com/repos/SynBioDex/SbolStandardWebsite/commits/master"
WEBSITE_RAW_ROOT = "https://raw.githubusercontent.com/SynBioDex/SbolStandardWebsite"


def acquire_sources(layout: Layout, *, force: bool = False) -> dict[str, Any]:
    layout.ensure_directories()
    retrieved_at = utc_now()
    source_manifest: dict[str, Any] = {
        "retrieved_at": retrieved_at,
        "manifest_updated_at": retrieved_at,
        "retrospective_doi": RETROSPECTIVE_DOI,
        "figshare_collection_id": FIGSHARE_COLLECTION_ID,
        "annotations": [],
        "rubric": {},
        "specification": {},
        "website": {},
    }

    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        collection_response = request_with_retry(
            client,
            "POST",
            f"{FIGSHARE_API}/collections/search",
            json={"resource_doi": RETROSPECTIVE_DOI, "limit": 10},
            timeout=30,
        )
        collection_response.raise_for_status()
        collections = collection_response.json()
        exact = [entry for entry in collections if entry.get("id") == FIGSHARE_COLLECTION_ID]
        if len(exact) != 1:
            raise RuntimeError("could not uniquely verify the pinned ACS Figshare collection")

        article_response = request_with_retry(
            client,
            "GET",
            f"{FIGSHARE_API}/collections/{FIGSHARE_COLLECTION_ID}/articles",
            params={"page_size": 100},
            timeout=30,
        )
        article_response.raise_for_status()
        article_refs = article_response.json()
        if len(article_refs) != 14:
            raise RuntimeError(f"expected 14 supplements, found {len(article_refs)}")

        details = []
        for article_ref in article_refs:
            detail_response = request_with_retry(client, "GET", article_ref["url"], timeout=30)
            detail_response.raise_for_status()
            details.append(detail_response.json())

        for detail in sorted(details, key=lambda item: item["doi"]):
            suffix_match = re.search(r"\.s(\d{3})$", detail["doi"])
            if not suffix_match or len(detail.get("files", [])) != 1:
                raise RuntimeError(f"unexpected supplement metadata for article {detail.get('id')}")
            supplement_number = int(suffix_match.group(1))
            file_info = detail["files"][0]
            destination = layout.annotations / file_info["name"]
            download_to_path(
                client,
                file_info["download_url"],
                destination,
                expected_md5=file_info["computed_md5"],
                force=force,
            )
            if supplement_number == 1:
                content_kind = "overview"
                year = None
            elif 2 <= supplement_number <= 13:
                content_kind = "yearly_annotations"
                year = supplement_number + 2010
            else:
                content_kind = "sbol_visual_citations"
                year = None
            source_manifest["annotations"].append(
                {
                    "article_id": detail["id"],
                    "supplement_doi": detail["doi"],
                    "content_kind": content_kind,
                    "year": year,
                    "filename": file_info["name"],
                    "path": str(destination.relative_to(layout.root)),
                    "download_url": file_info["download_url"],
                    "media_type": file_info["mimetype"],
                    "bytes": destination.stat().st_size,
                    "md5": md5_file(destination),
                    "sha256": sha256_file(destination),
                    "license": detail["license"]["name"],
                    "license_url": detail["license"]["url"],
                    "published_date": detail["published_date"],
                }
            )

        rubric_path = layout.rubric / "sbol_visual_diagram_rubric.txt"
        download_to_path(client, RUBRIC_URL, rubric_path, force=force)
        source_manifest["rubric"] = {
            "url": RUBRIC_URL,
            "path": str(rubric_path.relative_to(layout.root)),
            "bytes": rubric_path.stat().st_size,
            "sha256": sha256_file(rubric_path),
        }

        specification_path = layout.rubric / "SBOL-Visual-3.0.pdf"
        download_to_path(client, SBOL_VISUAL_3_URL, specification_path, force=force)
        source_manifest["specification"] = {
            "id": "sbol_visual_3_0_strict",
            "version": "3.0",
            "url": SBOL_VISUAL_3_URL,
            "path": str(specification_path.relative_to(layout.root)),
            "bytes": specification_path.stat().st_size,
            "sha256": sha256_file(specification_path),
        }

        website_urls = {
            "index": ANNIVERSARY_ROOT,
            "guidelines": f"{ANNIVERSARY_ROOT}guidelines/",
            **{str(year): f"{ANNIVERSARY_ROOT}acs-{year}/" for year in YEARS},
        }
        website_files: list[dict[str, Any]] = []
        for key, url in website_urls.items():
            filename = "index.html" if key == "index" else f"{key}.html"
            destination = layout.website / filename
            download_to_path(client, url, destination, force=force)
            website_files.append(
                {
                    "name": key,
                    "url": url,
                    "path": str(destination.relative_to(layout.root)),
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )

        commit_sha = None
        try:
            commit_response = request_with_retry(client, "GET", WEBSITE_COMMIT_API, timeout=30)
            commit_response.raise_for_status()
            commit_sha = commit_response.json().get("sha")
        except httpx.HTTPError:
            pass
        repository_license: dict[str, Any] | None = None
        if commit_sha:
            license_url = f"{WEBSITE_RAW_ROOT}/{commit_sha}/LICENSE"
            license_path = layout.website / "repository_LICENSE.txt"
            try:
                download_to_path(client, license_url, license_path, force=force)
                repository_license = {
                    "declared_license": "CC0 1.0",
                    "url": license_url,
                    "path": str(license_path.relative_to(layout.root)),
                    "sha256": sha256_file(license_path),
                }
            except httpx.HTTPError:
                pass
        source_manifest["website"] = {
            "repository": WEBSITE_REPOSITORY,
            "repository_commit_at_retrieval": commit_sha,
            "rendered_site_declared_license": "CC BY-NC-ND 4.0",
            "rendered_site_license_url": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
            "repository_license": repository_license,
            "license_conflict_note": (
                "The rendered site's footer and repository LICENSE declare different terms; "
                "clarify before redistributing a derived website dataset."
            ),
            "files": website_files,
        }

    write_json(layout.raw / "SOURCES.json", source_manifest)
    print(f"Acquired 14 supplements, the rubric, and {len(website_files)} website pages")
    return source_manifest
