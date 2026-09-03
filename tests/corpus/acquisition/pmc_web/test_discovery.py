"""Main-PDF discovery, challenge detection, and page version evidence."""

from __future__ import annotations

import pytest

from sbol_visual_eval.corpus.acquisition import pmc_web
from sbol_visual_eval.corpus.acquisition.pmc_web import (
    discovery as pmc_web_discovery,
)

from .helpers import (
    _article_html,
    _challenge_html,
)


def test_discovery_accepts_one_main_pdf_and_excludes_supplements() -> None:
    pmcid = "PMC1234567"
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/{pmcid}/"

    discovered = pmc_web_discovery._discover_main_pdf_url(_article_html(pmcid), article_url, pmcid)

    assert discovered == f"{article_url}pdf/nihms-main.pdf"


@pytest.mark.parametrize(
    "html",
    [
        b'<a href="https://evil.test/articles/PMC123/pdf/main.pdf">PDF</a>',
        b'<a href="pdf/article_s1.pdf">Supplementary PDF</a>',
        (b'<a href="pdf/main-one.pdf">PDF</a><a href="pdf/main-two.pdf">PDF</a>'),
    ],
)
def test_discovery_rejects_untrusted_supplementary_or_ambiguous_links(
    html: bytes,
) -> None:
    article_url = f"{pmc_web.PMC_ORIGIN}/articles/PMC123/"

    with pytest.raises(ValueError):
        pmc_web_discovery._discover_main_pdf_url(html, article_url, "PMC123")


def test_challenge_detection_is_prominent_and_does_not_match_incidental_prose() -> None:
    assert pmc_web_discovery._pmc_challenge_reason(_challenge_html()) == "checking_your_browser"
    assert (
        pmc_web_discovery._pmc_challenge_reason(
            b"<html><head><title>Example paper</title></head>"
            b"<body><p>We discuss reCAPTCHA in this article.</p></body></html>"
        )
        is None
    )


def test_arbitrary_page_text_does_not_establish_author_manuscript_version() -> None:
    assertion = pmc_web_discovery._page_author_manuscript_assertion(
        (
            b"<article><p>This reference links to an author manuscript elsewhere.</p>"
            b'<a href="pdf/main.pdf">Download PDF</a></article>'
        ),
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/main.pdf",
    )

    assert assertion is None
