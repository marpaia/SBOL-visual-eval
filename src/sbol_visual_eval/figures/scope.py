"""Selecting the main-manuscript page range for figure extraction.

Some repository PDFs append Supporting Information to the main
manuscript; their provenance manifests declare
``artifact_scope: main_manuscript_with_appended_supporting_information``
with an inclusive, 1-based ``figure_extraction_pdf_page_range``. The
historical counts cover only main-manuscript figures, so extraction must
honor that range and never scan appended Supporting Information pages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APPENDED_SI_SCOPE = "main_manuscript_with_appended_supporting_information"


@dataclass(frozen=True)
class PageScope:
    """The inclusive 1-based page range to scan for main-manuscript figures."""

    first_page: int
    last_page: int | None

    def pages(self, page_count: int) -> range:
        last = min(self.last_page, page_count) if self.last_page is not None else page_count
        return range(self.first_page, last + 1)


FULL_DOCUMENT = PageScope(first_page=1, last_page=None)


def scope_from_manifest(manifest: dict[str, Any]) -> PageScope:
    if manifest.get("artifact_scope") != APPENDED_SI_SCOPE:
        return FULL_DOCUMENT
    page_range = manifest.get("figure_extraction_pdf_page_range")
    if isinstance(page_range, dict) and "start" in page_range and "end" in page_range:
        return PageScope(first_page=int(page_range["start"]), last_page=int(page_range["end"]))
    si_start = manifest.get("appended_supporting_information_pdf_page_start")
    if si_start is not None:
        return PageScope(first_page=1, last_page=int(si_start) - 1)
    return FULL_DOCUMENT


def scope_for_pdf(pdf_path: Path) -> PageScope:
    """Read the scope from the sibling manifest of a paper PDF, if one exists.

    Root-level article PDFs keep their manifest at ``metadata.json`` next to
    the file; hash-suffixed variants use ``metadata__<sha12>.json``.
    """
    stem_suffix = pdf_path.stem.split("__", 1)
    candidates = [pdf_path.with_name("metadata.json")]
    if len(stem_suffix) == 2:
        candidates.insert(0, pdf_path.with_name(f"metadata__{stem_suffix[1]}.json"))
    for candidate in candidates:
        if candidate.exists():
            manifest = json.loads(candidate.read_text(encoding="utf-8"))
            return scope_from_manifest(manifest)
    return FULL_DOCUMENT
