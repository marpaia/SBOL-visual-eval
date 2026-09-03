"""Fixture-PDF construction for figure-extraction tests."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

PAGE_WIDTH, PAGE_HEIGHT = letter


def build_fixture_pdf(path: Path, pages: list[list[tuple[float, float, str, bool]]]) -> Path:
    """Write a PDF whose pages contain (x, y-from-top, text, bold) text runs."""
    canvas = Canvas(str(path), pagesize=letter)
    for page in pages:
        for x, y_from_top, text, bold in page:
            canvas.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
            canvas.drawString(x, PAGE_HEIGHT - y_from_top, text)
        canvas.showPage()
    canvas.save()
    return path
