"""Isolated child-process verification of browser-supplied PDFs."""

from __future__ import annotations

from pathlib import Path

import pytest

from sbol_visual_eval.corpus.acquisition.pmc_web import (
    verifier as pmc_web_verifier,
)

from .helpers import (
    _record,
)


def test_browser_supplied_pdf_parser_failure_isolated_in_child(tmp_path: Path) -> None:
    pdf_path = tmp_path / "malformed.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nmalformed\n%%EOF\n")

    with pytest.raises(ValueError, match="isolated PDF verification failed"):
        pmc_web_verifier._verify_uploaded_pdf(pdf_path, _record("10.1234/malformed", "PMC999"))
