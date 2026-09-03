from __future__ import annotations

from typing import Any

from sbol_visual_eval.figures.lines import group_words_into_lines


def _word(
    text: str, x0: float, top: float, *, width: float = 40.0, fontname: str = "Helvetica"
) -> dict[str, Any]:
    return {
        "text": text,
        "x0": x0,
        "x1": x0 + width,
        "top": top,
        "bottom": top + 10.0,
        "fontname": fontname,
    }


def test_words_group_into_lines_by_vertical_position() -> None:
    words = [
        _word("world", 60.0, 100.0),
        _word("hello", 10.0, 101.0),
        _word("below", 10.0, 120.0),
    ]
    lines = group_words_into_lines(words, page_width=612.0)
    assert [line.text for line in lines] == ["hello world", "below"]
    assert lines[0].x0 == 10.0
    assert lines[0].top == 100.0


def test_columns_are_grouped_independently() -> None:
    words = [
        _word("left", 10.0, 100.0),
        _word("column", 55.0, 100.0),
        _word("Figure", 320.0, 100.0),
        _word("1.", 365.0, 100.0, width=10.0),
        _word("Caption", 380.0, 100.0),
    ]
    lines = group_words_into_lines(words, page_width=612.0)
    assert [line.text for line in lines] == ["left column", "Figure 1. Caption"]


def test_bold_line_detection_uses_majority_of_characters() -> None:
    words = [
        _word("Figure", 10.0, 50.0, fontname="Helvetica-Bold"),
        _word("1.", 55.0, 50.0, width=10.0, fontname="Helvetica-Bold"),
        _word("x", 70.0, 50.0, width=5.0),
    ]
    lines = group_words_into_lines(words, page_width=612.0)
    assert lines[0].bold

    plain = group_words_into_lines([_word("Figure", 10.0, 50.0)], page_width=612.0)
    assert not plain[0].bold
