"""Figure discovery and rendering for manuscript PDFs.

- :mod:`.lines` groups PDF word boxes into column-aware text lines.
- :mod:`.captions` detects ``Figure N`` caption anchors in those lines.
- :mod:`.scope` selects the main-manuscript page range from provenance
  manifests so appended Supporting Information never contributes figures.
- :mod:`.census` produces the per-paper :class:`FigureCensus`, the
  extraction-stage counterpart of the historical ``figures_total`` label.
- :mod:`.render` renders pages to PNG images for the figure judge.
"""

from .captions import Caption, find_captions
from .census import FigureCensus, census_pdf
from .lines import TextLine, group_words_into_lines
from .render import render_page_png
from .scope import FULL_DOCUMENT, PageScope, scope_for_pdf, scope_from_manifest

__all__ = [
    "FULL_DOCUMENT",
    "Caption",
    "FigureCensus",
    "PageScope",
    "TextLine",
    "census_pdf",
    "find_captions",
    "group_words_into_lines",
    "render_page_png",
    "scope_for_pdf",
    "scope_from_manifest",
]
