from __future__ import annotations

from sbol_visual_eval.figures.captions import find_captions
from sbol_visual_eval.figures.lines import TextLine


def _line(text: str, *, top: float = 100.0, bold: bool = False) -> TextLine:
    return TextLine(text=text, x0=10.0, top=top, x1=300.0, bottom=top + 10.0, bold=bold)


def test_caption_anchors_require_punctuation_after_number() -> None:
    captions = find_captions(
        {
            1: [
                _line("Figure 1. A promoter drives expression.", bold=True),
                _line("Figure 2 shows that expression increases."),
                _line("as reported previously (Figure 1)."),
            ]
        }
    )
    assert [caption.figure_number for caption in captions] == [1]
    assert captions[0].page_number == 1


def test_supplementary_figures_are_ignored() -> None:
    captions = find_captions({1: [_line("Figure S1. Supplementary layout.", bold=True)]})
    assert captions == []


def test_bold_anchor_preferred_over_earlier_plain_mention() -> None:
    captions = find_captions(
        {
            1: [_line("Figure 2. is discussed below in detail.")],
            3: [_line("Figure 2. The final construct design.", bold=True)],
        }
    )
    assert len(captions) == 1
    assert captions[0].page_number == 3
    assert captions[0].bold


def test_first_anchor_wins_between_equal_candidates() -> None:
    captions = find_captions(
        {
            2: [_line("Figure 3. Original caption.", bold=True)],
            5: [_line("Figure 3. Repeated in appendix.", bold=True)],
        }
    )
    assert captions[0].page_number == 2


def test_caption_variants_and_ordering() -> None:
    captions = find_captions(
        {
            1: [_line("FIGURE 2: uppercase style.", bold=True)],
            2: [_line("Fig. 1. abbreviated style.", bold=True)],
        }
    )
    assert [caption.figure_number for caption in captions] == [1, 2]
