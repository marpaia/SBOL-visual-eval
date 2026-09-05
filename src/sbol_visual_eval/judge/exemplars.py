"""Load image-backed compatibility references from the local corpus."""

from __future__ import annotations

from collections.abc import Sequence

from ..corpus.layout import Layout
from ..corpus.util.storage import sha256_file
from ..figures.render import render_page_png
from .prompt import COMPATIBILITY_EXEMPLAR_SPECS
from .schema import CompatibilityExemplar, CompatibilityExemplarSpec


def load_compatibility_exemplars(
    layout: Layout,
    specs: Sequence[CompatibilityExemplarSpec] = COMPATIBILITY_EXEMPLAR_SPECS,
) -> tuple[CompatibilityExemplar, ...]:
    """Render exact corpus pages after verifying their pinned PDF identities."""
    exemplars = []
    for spec in specs:
        pdf_path = layout.root / spec.pdf_path
        if not pdf_path.is_file():
            raise FileNotFoundError(
                f"compatibility exemplar PDF is unavailable: {layout.display_path(pdf_path)}"
            )
        actual_sha256 = sha256_file(pdf_path)
        if actual_sha256 != spec.pdf_sha256:
            raise ValueError(
                f"compatibility exemplar PDF checksum mismatch for {spec.identifier}: "
                f"expected {spec.pdf_sha256}, got {actual_sha256}"
            )
        exemplars.append(
            CompatibilityExemplar(
                identifier=spec.identifier,
                publication_year=spec.publication_year,
                figure_number=spec.figure_number,
                caption_text=spec.caption_text,
                expected_compatible=spec.expected_compatible,
                rationale=spec.rationale,
                page_png=render_page_png(pdf_path, spec.page_number),
            )
        )
    return tuple(exemplars)
