from __future__ import annotations

from pathlib import Path

from sbol_visual_eval.figures.census import census_pdf
from sbol_visual_eval.figures.scope import PageScope

from .helpers import build_fixture_pdf


def test_census_detects_captions_across_columns_and_pages(tmp_path: Path) -> None:
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [
            [
                (72, 100, "A study of promoter design in engineered circuits.", False),
                (72, 400, "Figure 1. The reporter construct layout.", True),
                (330, 500, "Figure 2. Expression under induction.", True),
                (72, 500, "As shown in Figure 1 the construct is stable.", False),
            ],
            [
                (72, 300, "Figure 3. Full pathway design.", True),
                (72, 320, "Figure S1. Supplementary gel image.", True),
            ],
        ],
    )
    census = census_pdf(pdf_path)
    assert census.page_count == 2
    assert census.figure_numbers == (1, 2, 3)
    assert census.figure_count == 3
    assert census.contiguous
    assert [caption.page_number for caption in census.captions] == [1, 1, 2]


def test_census_reports_missing_figure_numbers(tmp_path: Path) -> None:
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [
            [
                (72, 200, "Figure 1. First design.", True),
                (72, 400, "Figure 3. Third design.", True),
            ]
        ],
    )
    census = census_pdf(pdf_path)
    assert census.figure_numbers == (1, 3)
    assert census.missing_figure_numbers == (2,)
    assert not census.contiguous
    assert census.max_figure_number == 3


def test_census_honors_page_scope(tmp_path: Path) -> None:
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [
            [(72, 200, "Figure 1. Main manuscript figure.", True)],
            [(72, 200, "Figure 2. Appended supporting figure.", True)],
        ],
    )
    census = census_pdf(pdf_path, scope=PageScope(first_page=1, last_page=1))
    assert census.figure_numbers == (1,)
    assert census.scoped_first_page == 1
    assert census.scoped_last_page == 1
    assert census.page_count == 2
