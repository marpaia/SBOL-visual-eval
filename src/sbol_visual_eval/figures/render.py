"""Rendering manuscript pages and figure regions to PNG images for judging.

The judge receives the full page containing a figure's caption: crops
tight enough to isolate one figure are unreliable across single- and
two-column layouts, while a page render plus the caption text lets a
vision model locate the named figure unambiguously.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pypdfium2

DEFAULT_DPI = 150
MAX_RENDER_PIXELS = 4_000_000

# PDFium is not thread-safe: concurrent rendering aborts the process inside the
# native library. Every render therefore holds this lock. Judging dominates the
# wall clock in parallel sweeps, so serializing rasterization costs little.
_RENDER_LOCK = threading.Lock()


def render_page_png(pdf_path: Path, page_number: int, dpi: int = DEFAULT_DPI) -> bytes:
    """Render one 1-based page to PNG bytes, capped to a sane pixel budget."""
    with _RENDER_LOCK:
        document = pypdfium2.PdfDocument(pdf_path)
        try:
            page = document[page_number - 1]
            scale = dpi / 72
            width, height = page.get_size()
            pixels = (width * scale) * (height * scale)
            if pixels > MAX_RENDER_PIXELS:
                # Slightly under-shoot so integer rounding of the bitmap
                # dimensions cannot exceed the budget.
                scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5 * 0.995
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
        finally:
            document.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
