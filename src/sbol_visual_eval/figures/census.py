"""The per-paper figure census: which numbered figures a manuscript PDF contains.

The census walks the scoped main-manuscript pages, groups words into
column-aware lines, and detects caption anchors. Its figure count is the
extraction-stage prediction of the historical ``figures_total`` label,
so census quality is directly measurable against every annotated paper.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .captions import Caption, find_captions
from .lines import group_words_into_lines
from .scope import FULL_DOCUMENT, PageScope


@dataclass(frozen=True)
class FigureCensus:
    """Every detected figure caption in one PDF's scoped pages."""

    pdf_path: Path
    page_count: int
    scoped_first_page: int
    scoped_last_page: int
    captions: tuple[Caption, ...]

    @property
    def figure_numbers(self) -> tuple[int, ...]:
        return tuple(caption.figure_number for caption in self.captions)

    @property
    def figure_count(self) -> int:
        return len(self.captions)

    @property
    def max_figure_number(self) -> int:
        return max(self.figure_numbers, default=0)

    @property
    def missing_figure_numbers(self) -> tuple[int, ...]:
        """Gaps in 1..max, a signal that caption detection missed a figure."""
        present = set(self.figure_numbers)
        return tuple(
            number for number in range(1, self.max_figure_number + 1) if number not in present
        )

    @property
    def contiguous(self) -> bool:
        return not self.missing_figure_numbers


def census_pdf(pdf_path: Path, scope: PageScope = FULL_DOCUMENT) -> FigureCensus:
    """Detect numbered figure captions in the scoped pages of a manuscript PDF."""
    pages = {}
    with pdfplumber.open(pdf_path) as document:
        page_count = len(document.pages)
        scoped = scope.pages(page_count)
        for page_number in scoped:
            page = document.pages[page_number - 1]
            words = page.extract_words(extra_attrs=["fontname"])
            pages[page_number] = group_words_into_lines(words, float(page.width))
    return FigureCensus(
        pdf_path=pdf_path,
        page_count=page_count,
        scoped_first_page=scoped.start,
        scoped_last_page=scoped.stop - 1,
        captions=tuple(find_captions(pages)),
    )
