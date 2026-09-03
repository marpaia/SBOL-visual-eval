from __future__ import annotations

import json
from pathlib import Path

from sbol_visual_eval.figures.scope import (
    FULL_DOCUMENT,
    PageScope,
    scope_for_pdf,
    scope_from_manifest,
)


def test_full_document_scope_covers_every_page() -> None:
    assert list(FULL_DOCUMENT.pages(3)) == [1, 2, 3]


def test_manifest_without_appended_si_uses_full_document() -> None:
    assert scope_from_manifest({"artifact_type": "article_pdf"}) == FULL_DOCUMENT


def test_manifest_with_page_range_limits_extraction() -> None:
    scope = scope_from_manifest(
        {
            "artifact_scope": "main_manuscript_with_appended_supporting_information",
            "figure_extraction_pdf_page_range": {
                "start": 1,
                "end": 24,
                "inclusive": True,
                "page_numbering": "pdf_1_based",
            },
            "appended_supporting_information_pdf_page_start": 25,
        }
    )
    assert scope == PageScope(first_page=1, last_page=24)
    assert list(scope.pages(30)) == list(range(1, 25))
    assert list(scope.pages(20)) == list(range(1, 21))


def test_manifest_falls_back_to_si_start_page() -> None:
    scope = scope_from_manifest(
        {
            "artifact_scope": "main_manuscript_with_appended_supporting_information",
            "appended_supporting_information_pdf_page_start": 10,
        }
    )
    assert scope == PageScope(first_page=1, last_page=9)


def test_scope_for_pdf_reads_sibling_manifest(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    (tmp_path / "metadata.json").write_text(
        json.dumps(
            {
                "artifact_scope": "main_manuscript_with_appended_supporting_information",
                "figure_extraction_pdf_page_range": {"start": 1, "end": 8},
            }
        ),
        encoding="utf-8",
    )
    assert scope_for_pdf(pdf_path) == PageScope(first_page=1, last_page=8)


def test_scope_for_pdf_prefers_hash_named_manifest(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper__abc123def456.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    (tmp_path / "metadata__abc123def456.json").write_text(
        json.dumps(
            {
                "artifact_scope": "main_manuscript_with_appended_supporting_information",
                "figure_extraction_pdf_page_range": {"start": 2, "end": 5},
            }
        ),
        encoding="utf-8",
    )
    assert scope_for_pdf(pdf_path) == PageScope(first_page=2, last_page=5)


def test_scope_for_pdf_without_manifest_is_full_document(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    assert scope_for_pdf(pdf_path) == FULL_DOCUMENT
