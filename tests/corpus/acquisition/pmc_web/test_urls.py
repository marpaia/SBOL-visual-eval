"""Strict PMC PDF URL validation."""

from __future__ import annotations

import pytest

from sbol_visual_eval.corpus.acquisition.pmc_web import (
    urls as pmc_web_urls,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/%2F..%2Fevil.pdf",
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/%252F..%252Fevil.pdf",
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/main.pdf?session=secret",
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/main.pdf#fragment",
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC999/pdf/main.pdf",
    ],
)
def test_pdf_url_validation_rejects_encoded_paths_queries_and_wrong_pmcid(url: str) -> None:
    with pytest.raises(ValueError):
        pmc_web_urls._validated_pmc_pdf_url(url, "PMC123", field_name="source_url")
