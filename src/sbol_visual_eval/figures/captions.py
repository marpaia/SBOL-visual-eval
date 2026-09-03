"""Detecting main-manuscript figure captions from grouped text lines.

A caption anchor is a line beginning ``Figure N`` (or ``Fig. N`` /
``FIGURE N``) with punctuation immediately after the number, the style
every ACS caption uses. Tight caption kerning often merges the word and
number (``Figure1.``), so the space is optional. Supplementary figures
(``Figure S1``) never match, in-text references almost always continue
with a panel letter or word instead of punctuation, and the remaining
ambiguity is resolved per figure number: bold anchors win over plain
ones, then earlier reading order wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .lines import TextLine

CAPTION_ANCHOR = re.compile(r"^(?:Figure|FIGURE|Fig\.?)\s*(\d{1,3})\s*[.:|]")


@dataclass(frozen=True)
class Caption:
    """One detected figure caption anchor."""

    figure_number: int
    page_number: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    bold: bool


def _candidates(page_number: int, lines: list[TextLine]) -> list[Caption]:
    captions = []
    for line in lines:
        match = CAPTION_ANCHOR.match(line.text)
        if match:
            captions.append(
                Caption(
                    figure_number=int(match.group(1)),
                    page_number=page_number,
                    text=line.text,
                    x0=line.x0,
                    top=line.top,
                    x1=line.x1,
                    bottom=line.bottom,
                    bold=line.bold,
                )
            )
    return captions


def find_captions(pages: dict[int, list[TextLine]]) -> list[Caption]:
    """Detect one caption per figure number across the given pages.

    ``pages`` maps 1-based page numbers to their grouped text lines.
    """
    candidates: list[Caption] = []
    for page_number in sorted(pages):
        candidates.extend(_candidates(page_number, pages[page_number]))

    best: dict[int, Caption] = {}
    for caption in candidates:
        current = best.get(caption.figure_number)
        if current is None or (caption.bold and not current.bold):
            best[caption.figure_number] = caption
    return [best[number] for number in sorted(best)]
