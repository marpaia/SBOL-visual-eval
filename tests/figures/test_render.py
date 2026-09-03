from __future__ import annotations

from pathlib import Path

from sbol_visual_eval.figures.render import render_page_png

from .helpers import build_fixture_pdf

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_render_page_png_produces_png_bytes(tmp_path: Path) -> None:
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [[(72, 200, "Figure 1. A rendered page.", True)]],
    )
    payload = render_page_png(pdf_path, page_number=1)
    assert payload.startswith(PNG_SIGNATURE)
    assert len(payload) > 1000


def test_render_page_png_caps_pixel_budget(tmp_path: Path) -> None:
    pdf_path = build_fixture_pdf(
        tmp_path / "paper.pdf",
        [[(72, 200, "Figure 1. A rendered page.", True)]],
    )
    payload = render_page_png(pdf_path, page_number=1, dpi=1200)
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    assert width * height <= 4_000_000
