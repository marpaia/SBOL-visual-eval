"""Column-aware grouping of PDF words into text lines.

ACS Synthetic Biology articles use a two-column layout, so a naive
full-page line extraction concatenates unrelated left- and right-column
text and destroys line-start anchoring. Words are therefore grouped per
column half: a caption anchored at the start of either column half keeps
its line-initial position regardless of layout, and single-column pages
lose nothing because their anchors always start in the left half.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VERTICAL_TOLERANCE = 3.0


@dataclass(frozen=True)
class TextLine:
    """One visual text line within a single column half of a page."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    bold: bool


def _is_bold(fontname: str) -> bool:
    return "bold" in fontname.lower()


def _flush(words: list[dict[str, Any]]) -> TextLine:
    ordered = sorted(words, key=lambda word: word["x0"])
    bold_characters = sum(
        len(word["text"]) for word in ordered if _is_bold(word.get("fontname", ""))
    )
    total_characters = sum(len(word["text"]) for word in ordered)
    return TextLine(
        text=" ".join(word["text"] for word in ordered),
        x0=min(word["x0"] for word in ordered),
        top=min(word["top"] for word in ordered),
        x1=max(word["x1"] for word in ordered),
        bottom=max(word["bottom"] for word in ordered),
        bold=total_characters > 0 and bold_characters * 2 >= total_characters,
    )


def _group_column(words: list[dict[str, Any]]) -> list[TextLine]:
    lines = []
    current: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda word: (word["top"], word["x0"])):
        if current and word["top"] - current[0]["top"] > VERTICAL_TOLERANCE:
            lines.append(_flush(current))
            current = []
        current.append(word)
    if current:
        lines.append(_flush(current))
    return lines


def group_words_into_lines(words: list[dict[str, Any]], page_width: float) -> list[TextLine]:
    """Group word boxes into per-column-half text lines in reading order."""
    midline = page_width / 2
    left = [word for word in words if word["x0"] < midline]
    right = [word for word in words if word["x0"] >= midline]
    grouped = _group_column(left) + _group_column(right)
    return sorted(grouped, key=lambda line: (line.x0 >= midline, line.top))
