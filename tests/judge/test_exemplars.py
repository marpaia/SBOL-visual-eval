from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.judge.exemplars import load_compatibility_exemplars
from sbol_visual_eval.judge.schema import CompatibilityExemplarSpec


def _spec(pdf_path: str, payload: bytes) -> CompatibilityExemplarSpec:
    return CompatibilityExemplarSpec(
        identifier="10.1/reference",
        publication_year=2016,
        figure_number=2,
        caption_text="Figure 2. Reference.",
        expected_compatible=False,
        rationale="Historical negative.",
        pdf_path=pdf_path,
        pdf_sha256=hashlib.sha256(payload).hexdigest(),
        page_number=3,
    )


def test_exemplar_loader_verifies_pdf_and_renders_pinned_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"pinned PDF"
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(payload)
    rendered = []

    def fake_render(path: Path, page_number: int) -> bytes:
        rendered.append((path, page_number))
        return b"reference image"

    monkeypatch.setattr("sbol_visual_eval.judge.exemplars.render_page_png", fake_render)

    exemplars = load_compatibility_exemplars(Layout(tmp_path), (_spec("paper.pdf", payload),))

    assert exemplars[0].page_png == b"reference image"
    assert exemplars[0].expected_compatible is False
    assert rendered == [(pdf_path, 3)]


def test_exemplar_loader_rejects_changed_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"changed")

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_compatibility_exemplars(Layout(tmp_path), (_spec("paper.pdf", b"expected"),))
