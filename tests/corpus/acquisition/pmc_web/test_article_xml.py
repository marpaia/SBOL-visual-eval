"""Identity-bound PDF-link derivation from verified JATS article XML."""

from __future__ import annotations

import pytest

from sbol_visual_eval.corpus.acquisition.pmc_web import (
    article_xml as pmc_web_article_xml,
)

from .helpers import (
    _article_xml,
    _record,
)


def test_verified_article_xml_provides_one_identity_bound_pdf_link() -> None:
    record = _record("10.1234/xml-link", "PMC123")
    payload = (
        b'<!DOCTYPE article PUBLIC "-//NLM//DTD JATS 1.4//EN" "JATS-archivearticle1-4.dtd">'
        + _article_xml(record, pdf_names=("emss-67706.pdf",), manuscript_id="EMS67706")
    )

    link = pmc_web_article_xml._article_xml_pdf_link(
        payload,
        record,
        "PMC123",
    )

    assert link == {
        "pdf_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/emss-67706.pdf",
        "pdf_basename": "emss-67706.pdf",
        "manuscript_id": "EMS67706",
    }


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            _article_xml(
                _record("10.1234/xml-link", "PMC123"),
                pdf_names=("nihms-1.pdf", "nihms-2.pdf"),
            ),
            "unique pmc-pdf self-uri",
        ),
        (
            _article_xml(
                _record("10.1234/xml-link", "PMC123"),
                pdf_names=("../evil.pdf",),
            ),
            "safe PDF basename",
        ),
        (
            _article_xml(_record("10.1234/xml-link", "PMC999")),
            "record PMCID",
        ),
        (
            _article_xml(_record("10.1234/xml-link", "PMC123.evil")),
            "record PMCID",
        ),
        (
            _article_xml(_record("10.1234/wrong-doi", "PMC123")),
            "record DOI",
        ),
        (
            b'<!DOCTYPE article [<!ENTITY x "expanded">]><article>&x;</article>',
            "internal DTD subset",
        ),
        (
            (
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<!DOCTYPE article [<!ENTITY d "10.1234/xml-link">]>'
                "<article><front><article-meta>"
                '<article-id pub-id-type="pmcid">PMC123</article-id>'
                '<article-id pub-id-type="doi">&d;</article-id>'
                '<article-id pub-id-type="manuscript-id">NIHMS123</article-id>'
                '<self-uri xmlns:xlink="http://www.w3.org/1999/xlink" '
                'content-type="pmc-pdf" xlink:href="nihms-123.pdf"/>'
                "</article-meta></front></article>"
            ).encode("utf-16"),
            "not UTF-8",
        ),
        (
            (
                b'<?xml version="1.0" encoding="UTF-16"?>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "declares a non-UTF-8 encoding",
        ),
        (
            (
                b'<!DOCTYPE article SYSTEM "foo>bar" [<!ELEMENT article ANY>]>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "internal DTD subset",
        ),
        (
            (
                b"<!-- <!DOCTYPE fake> -->"
                b'<!DOCTYPE article SYSTEM "jats.dtd" [<!ELEMENT article ANY>]>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "internal DTD subset",
        ),
        (
            (
                ("<!--" + "ß" * 10 + "-->").encode()
                + b'<!DOCTYPE article SYSTEM "jats.dtd" [<!ELEMENT article ANY>]>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "internal DTD subset",
        ),
        (
            (
                b"<!-- "
                + b"<!DOCTYPE " * 2_000
                + b" -->"
                + b'<!DOCTYPE article SYSTEM "jats.dtd" [<!ELEMENT article ANY>]>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "internal DTD subset",
        ),
        (
            (
                b'<!-- <!DOCTYPE " -->'
                b'<!DOCTYPE article SYSTEM "jats.dtd" [<!ELEMENT article ANY>]>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "internal DTD subset",
        ),
        (
            (
                b"<!-- <!DOCTYPE ' -->"
                b'<!DOCTYPE article SYSTEM "jats.dtd" [<!ELEMENT article ANY>]>'
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
            ),
            "internal DTD subset",
        ),
        (
            (
                b"<not-an-article>"
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
                + b"</not-an-article>"
            ),
            "root is not article",
        ),
        (
            (
                b"<article><sub-article>"
                + _article_xml(_record("10.1234/xml-link", "PMC123"))
                + b"</sub-article></article>"
            ),
            "exactly one direct front",
        ),
    ],
)
def test_article_xml_pdf_link_rejects_ambiguous_unsafe_or_wrong_record_evidence(
    payload: bytes,
    expected_error: str,
) -> None:
    record = _record("10.1234/xml-link", "PMC123")

    with pytest.raises(ValueError, match=expected_error):
        pmc_web_article_xml._article_xml_pdf_link(payload, record, "PMC123")
