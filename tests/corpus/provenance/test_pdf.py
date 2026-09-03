from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sbol_visual_eval.corpus.provenance import pdf


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text

    def extract_text(self) -> str:
        return self.text


def _mock_reader(monkeypatch: Any, pages: list[str], *, title: str = "Article") -> None:
    reader = SimpleNamespace(
        pages=[_Page(text) for text in pages],
        metadata=SimpleNamespace(title=title),
    )
    monkeypatch.setattr(pdf, "PdfReader", lambda path: reader)


def _candidate(tmp_path: Path) -> Path:
    path = tmp_path / "candidate.pdf"
    path.write_bytes(b"%PDF-1.7\nfixture\n%%EOF\n")
    return path


def test_verify_pdf_rejects_thesis_even_when_article_title_appears(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _mock_reader(
        monkeypatch,
        [
            (
                "Submitted to the Department of Biological Engineering in partial "
                "fulfillment of the requirements for the degree of Doctor of Philosophy"
            ),
            "Thesis supervisor: Example Adviser",
            "Example Paper About Synthetic Biology",
        ],
        title="Microsoft Word - Thesis-Example Author",
    )

    with pytest.raises(ValueError, match="thesis or dissertation"):
        pdf.verify_pdf(
            _candidate(tmp_path),
            {
                "doi": "10.1234/example",
                "title_source": "Example Paper About Synthetic Biology",
            },
        )


def test_verify_pdf_does_not_treat_scientific_use_of_thesis_as_document_type(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _mock_reader(
        monkeypatch,
        [
            "Example Paper About Synthetic Biology DOI 10.1234/example",
            "These results support the thesis that the circuit is robust.",
        ],
    )

    result = pdf.verify_pdf(
        _candidate(tmp_path),
        {
            "doi": "10.1234/example",
            "title_source": "Example Paper About Synthetic Biology",
        },
    )

    assert result["identity_check"] == "doi"
    assert result["page_count"] == 2


def test_verify_pdf_rejects_repository_cover_plus_figure_only_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _mock_reader(
        monkeypatch,
        [
            (
                "Downloaded from example repository. Example Paper About Synthetic "
                "Biology. Published in: ACS Synthetic Biology. Link to article, DOI: "
                "10.1234/example. Document Version: peer reviewed version. Citation (APA)."
            ),
            "Figure 1 Reporter output No inducer 4 mM inducer",
        ],
    )

    with pytest.raises(ValueError, match="figure-only partial file"):
        pdf.verify_pdf(
            _candidate(tmp_path),
            {
                "doi": "10.1234/example",
                "title_source": "Example Paper About Synthetic Biology",
            },
        )


def test_verify_pdf_accepts_strongly_identified_explicit_single_page_article(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _mock_reader(
        monkeypatch,
        [("DNA 21 Collection. ACS Synthetic Biology. https://doi.org/10.1021/acssynbio.6b00217")],
        title="DNA 21 Collection",
    )

    result = pdf.verify_pdf(
        _candidate(tmp_path),
        {
            "doi": "10.1021/acssynbio.6b00217",
            "title_source": "DNA 21 Collection",
            "pages": "877-877",
        },
    )

    assert result["identity_check"] == "doi"
    assert result["page_count"] == 1


@pytest.mark.parametrize("pages", [None, "877-878"])
def test_verify_pdf_rejects_one_page_partial_without_single_page_pagination(
    tmp_path: Path, monkeypatch: Any, pages: str | None
) -> None:
    _mock_reader(
        monkeypatch,
        [("DNA 21 Collection. ACS Synthetic Biology. https://doi.org/10.1021/acssynbio.6b00217")],
        title="DNA 21 Collection",
    )

    with pytest.raises(ValueError, match="one-page PDF"):
        pdf.verify_pdf(
            _candidate(tmp_path),
            {
                "doi": "10.1021/acssynbio.6b00217",
                "title_source": "DNA 21 Collection",
                "pages": pages,
            },
        )


def test_verify_pdf_rejects_weakly_identified_single_page_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _mock_reader(monkeypatch, ["Figure 1. Reporter output."], title="Figure 1")

    with pytest.raises(ValueError, match="one-page PDF"):
        pdf.verify_pdf(
            _candidate(tmp_path),
            {
                "doi": "10.1021/acssynbio.6b00217",
                "title_source": "DNA 21 Collection",
                "pages": "877-877",
            },
        )
