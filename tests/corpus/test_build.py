from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from sbol_visual_eval.corpus.build import (
    _snapshot_metadata_payloads,
    aggregate_years,
    invariant_violations,
)
from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.corpus.metadata import attach_europepmc, attach_openalex, match_crossref
from sbol_visual_eval.corpus.metadata.candidates import (
    candidate_allowed,
    europepmc_pdf_candidates,
    is_supplementary_artifact_url,
    openalex_pdf_candidates,
)
from sbol_visual_eval.corpus.schema import PROCESSED_OUTPUT_FILENAMES
from sbol_visual_eval.corpus.sources import (
    extract_yearly_records,
    parse_rubric,
    parse_website,
    parse_yearly_cached_summaries,
)
from sbol_visual_eval.corpus.util.storage import write_json
from sbol_visual_eval.corpus.util.text import normalize_title
from sbol_visual_eval.corpus.validation import _validate_processed_outputs

COUNT_FIELDS = (
    "papers_total",
    "figures_total",
    "figures_sbol_visual_compatible",
    "figures_sbol_visual_compliant",
    "figures_best_practices",
)

EXPECTED_WORKBOOK_TOTALS = {
    2012: (63, 340, 87, 36, 18),
    2013: (80, 408, 99, 61, 36),
    2014: (118, 531, 113, 53, 27),
    2015: (145, 743, 177, 107, 38),
    2016: (169, 886, 217, 136, 90),
    2017: (242, 1266, 285, 187, 115),
    2018: (305, 1615, 429, 242, 98),
    2019: (287, 1512, 432, 219, 126),
    2020: (329, 1744, 441, 330, 149),
    2021: (327, 1759, 457, 382, 262),
    2022: (393, 2068, 595, 487, 245),
    2023: (344, 1766, 378, 279, 138),
}

EXPECTED_WEBSITE_TOTALS = {
    2012: (63, 340, 87, 36, 18),
    2013: (80, 408, 99, 61, 36),
    2014: (118, 531, 113, 53, 27),
    2015: (145, 743, 177, 107, 38),
    2016: (169, 886, 217, 136, 90),
    2017: (242, 1266, 285, 187, 115),
    2018: (305, 1615, 429, 242, 98),
    2019: (287, 1512, 432, 219, 126),
    2020: (302, 1455, 339, 275, 128),
    2021: (327, 1759, 457, 382, 262),
    2022: (393, 2068, 595, 487, 245),
    2023: (344, 1766, 378, 279, 138),
}

EXPECTED_WEBSITE_ROWS = {
    2012: 12,
    2013: 27,
    2014: 29,
    2015: 39,
    2016: 58,
    2017: 80,
    2018: 97,
    2019: 77,
    2020: 114,
    2021: 156,
    2022: 193,
    2023: 126,
}

KNOWN_FUZZY_MATCHES = {
    (
        "Engineering 1-Alkene Biosynthesis and Secretion by Dynamic Regulation in "
        "YeastYongjin J. Zhou*, Yating Hu"
    ): "10.1021/acssynbio.7b00338",
    (
        "A Highly Bioactive Lys-Deficient IFN Leads to a Site-Specific Di-PEGylated IFN "
        "with Equivalent Bioactivity to That of Unmodified IFN-α2b"
    ): "10.1021/acssynbio.8b00188",
    (
        "sessing the Flexibility of the Prochlorosin 2.8 Scaffold for Bioengineering Applications"
    ): "10.1021/acssynbio.9b00080",
    (
        "ynthetic Gene Circuits Enable Escherichia coli To Use Endogenous H2S as a "
        "Signaling Molecule for Quorum Sensing"
    ): "10.1021/acssynbio.9b00210",
}

CROSSREF_FUZZY_FIXTURE = {
    "items": [
        {
            "DOI": "10.1021/acssynbio.7b00338",
            "title": [
                "Engineering 1-Alkene Biosynthesis and Secretion by Dynamic Regulation in Yeast"
            ],
            "volume": "7",
            "issue": "2",
        },
        {
            "DOI": "10.1021/acssynbio.8b00188",
            "title": [
                (
                    "A Highly Bioactive Lys-Deficient IFN Leads to a Site-Specific "
                    "Di-PEGylated IFN with Equivalent Bioactivity to That of Unmodified IFN-?2b"
                )
            ],
            "volume": "7",
            "issue": "11",
        },
        {
            "DOI": "10.1021/acssynbio.9b00080",
            "title": [
                (
                    "Assessing the Flexibility of the Prochlorosin 2.8 Scaffold for "
                    "Bioengineering Applications"
                )
            ],
            "volume": "8",
            "issue": "5",
        },
        {
            "DOI": "10.1021/acssynbio.9b00210",
            "title": [
                (
                    "Synthetic Gene Circuits Enable Escherichia coli To Use Endogenous H2S as a "
                    "Signaling Molecule for Quorum Sensing"
                )
            ],
            "volume": "8",
            "issue": "9",
        },
    ]
}


@pytest.fixture(scope="session")
def layout() -> Layout:
    return Layout(Path(__file__).resolve().parents[2])


@pytest.fixture(scope="session")
def yearly_records(layout: Layout) -> list[dict[str, object]]:
    return extract_yearly_records(layout)


@pytest.fixture(scope="session")
def website_snapshot(
    layout: Layout,
) -> tuple[list[dict[str, object]], dict[int, dict[str, int]]]:
    return parse_website(layout)


def _count_tuple(values: dict[str, int]) -> tuple[int, ...]:
    return tuple(values[field] for field in COUNT_FIELDS)


@pytest.mark.parametrize(
    ("source", "equivalent"),
    [
        (
            "<i>IFN-α2b</i>: Café &amp; β-galactosidase",
            "IFN alpha2b cafe beta galactosidase",
        ),
        ("  Synthetic Biology—A Review!  ", "synthetic biology a review"),
        ("DNA φ λ μ", "dna phi lambda mu"),
    ],
)
def test_title_normalization_handles_markup_unicode_and_punctuation(
    source: str, equivalent: str
) -> None:
    assert normalize_title(source) == normalize_title(equivalent)


def test_crossref_matching_recovers_known_source_title_corruptions(
    yearly_records: list[dict[str, object]],
) -> None:
    records_by_title = {record["title_source"]: record for record in yearly_records}
    records = [dict(records_by_title[title]) for title in KNOWN_FUZZY_MATCHES]

    matched, review = match_crossref(records, CROSSREF_FUZZY_FIXTURE)

    assert {
        record["title_source"]: (record["doi"], record["crossref_match_method"])
        for record in matched
    } == {title: (doi, "fuzzy_unique") for title, doi in KNOWN_FUZZY_MATCHES.items()}
    assert len(review) == len(KNOWN_FUZZY_MATCHES)
    assert all(row["review_status"] == "accepted_manual_review" for row in review)
    assert all(row["review_rationale"] for row in review)
    assert all(record["crossref_title_score"] >= 0.85 for record in matched)
    assert all(record["crossref_match_margin"] >= 0.15 for record in matched)


def test_website_parser_reproduces_all_acquired_annual_snapshots(
    website_snapshot: tuple[list[dict[str, object]], dict[int, dict[str, int]]],
) -> None:
    rows, summaries = website_snapshot

    assert {year: _count_tuple(summary) for year, summary in summaries.items()} == (
        EXPECTED_WEBSITE_TOTALS
    )
    assert len(rows) == 1008
    assert Counter(row["year"] for row in rows) == EXPECTED_WEBSITE_ROWS
    assert all(row["source_path"] == f"data/raw/website/{row['year']}.html" for row in rows)


def test_yearly_workbooks_reproduce_exact_released_aggregates(
    yearly_records: list[dict[str, object]],
) -> None:
    totals = aggregate_years(yearly_records)

    assert len(yearly_records) == 2802
    assert {year: _count_tuple(total) for year, total in totals.items()} == (
        EXPECTED_WORKBOOK_TOTALS
    )
    assert tuple(sum(total[field] for total in totals.values()) for field in COUNT_FIELDS) == (
        2802,
        14638,
        3710,
        2519,
        1342,
    )


def test_processed_outputs_match_manifest_and_current_raw_sources(layout: Layout) -> None:
    source_manifest = json.loads((layout.raw / "SOURCES.json").read_text(encoding="utf-8"))
    qc = json.loads((layout.reports / "quality_control.json").read_text(encoding="utf-8"))
    manifest = json.loads((layout.processed / "MANIFEST.json").read_text(encoding="utf-8"))

    assert set(manifest["outputs"]) == set(PROCESSED_OUTPUT_FILENAMES)
    assert {
        filename: manifest["outputs"][filename]["rows"] for filename in PROCESSED_OUTPUT_FILENAMES
    } == {
        "papers.jsonl": 2802,
        "papers.csv": 2802,
        "rubric.csv": 31,
        "website_tables.csv": 1008,
        "yearly_totals.csv": 12,
    }
    assert _validate_processed_outputs(layout, qc, source_manifest) == []


def test_metadata_snapshot_refresh_preserves_acquisition_time_and_is_idempotent(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    layout.raw.mkdir(parents=True)
    layout.metadata.mkdir(parents=True)
    write_json(
        layout.raw / "SOURCES.json",
        {
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "manifest_updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    payloads = [
        {
            "retrieved_at": f"2026-01-01T0{index}:00:00+00:00",
            "request_url": f"https://example.test/{index}",
            "items": [{"id": index}],
        }
        for index in range(3)
    ]

    _snapshot_metadata_payloads(layout, *payloads)
    first_manifest_bytes = (layout.raw / "SOURCES.json").read_bytes()
    _snapshot_metadata_payloads(layout, *payloads)

    manifest = json.loads((layout.raw / "SOURCES.json").read_text())
    assert manifest["retrieved_at"] == "2026-01-01T00:00:00+00:00"
    assert manifest["manifest_updated_at"] == "2026-01-01T02:00:00+00:00"
    assert manifest["metadata_snapshots"]["europepmc"]["retrieved_at"] == (
        "2026-01-01T02:00:00+00:00"
    )
    assert (layout.raw / "SOURCES.json").read_bytes() == first_manifest_bytes


def test_2020_source_discrepancy_and_count_violation_are_preserved(
    layout: Layout,
    yearly_records: list[dict[str, object]],
    website_snapshot: tuple[list[dict[str, object]], dict[int, dict[str, int]]],
) -> None:
    workbook_2020 = aggregate_years(yearly_records)[2020]
    cached_summary_2020 = parse_yearly_cached_summaries(layout)[2020]
    website_2020 = website_snapshot[1][2020]

    assert _count_tuple(workbook_2020) == (329, 1744, 441, 330, 149)
    assert _count_tuple(cached_summary_2020) == (311, 1649, 405, 308, 139)
    assert _count_tuple(website_2020) == (302, 1455, 339, 275, 128)
    assert tuple(website_2020[field] - workbook_2020[field] for field in COUNT_FIELDS) == (
        -27,
        -289,
        -102,
        -55,
        -21,
    )

    violations = invariant_violations([dict(record) for record in yearly_records])
    assert len(violations) == 1
    assert violations[0] == {
        "record_id": violations[0]["record_id"],
        "doi": None,
        "title": (
            "Applications of CRISPR in a Microbial Cell Factory: From Genome "
            "Reconstruction to Metabolic Network Reprogramming"
        ),
        "year": 2020,
        "issue_month": "September",
        "annotation_sheet": "September",
        "annotation_row": 4,
        "figures_total": 2,
        "figures_sbol_visual_compatible": 3,
        "figures_sbol_visual_compliant": 3,
        "figures_best_practices": 0,
        "violation": "expected total >= compatible >= compliant >= best >= 0",
    }


def test_historical_rubric_rule_count_and_canonical_section_mapping(layout: Layout) -> None:
    rules = parse_rubric(layout)

    assert len(rules) == 31
    assert Counter(rule["category"] for rule in rules) == {
        "compliance": 13,
        "best_practice": 18,
    }
    historical_545 = [
        rule
        for rule in rules
        if rule["category"] == "best_practice" and rule["historical_rule_id"] == "5.4.5"
    ]
    assert len(historical_545) == 1
    assert historical_545[0]["section"] == "5.4.5"
    assert historical_545[0]["rule_key"] == "best_practice:5.4.5"
    assert historical_545[0]["canonical_specification_section"] == "5.4.6"


def test_pdf_candidate_discovery_and_default_selection_are_conservative() -> None:
    acs_open = {
        "pdf_url": "https://pubs.acs.org/doi/pdf/10.1234/example",
        "landing_page_url": "https://pubs.acs.org/doi/10.1234/example",
        "is_oa": True,
        "license": "cc-by",
        "version": "publishedVersion",
        "source": {"display_name": "ACS Synthetic Biology", "type": "journal"},
    }
    repository_unknown = {
        "pdf_url": "https://repository.example/paper-unknown.pdf",
        "landing_page_url": "https://repository.example/item/1",
        "is_oa": True,
        "license": None,
        "version": "acceptedVersion",
        "source": {"display_name": "Example Repository", "type": "repository"},
    }
    repository_open = {
        "pdf_url": "https://repository.example/paper-cc-by.pdf",
        "landing_page_url": "https://repository.example/item/2",
        "is_oa": True,
        "license": "cc-by",
        "version": "acceptedVersion",
        "source": {"display_name": "Example Repository", "type": "repository"},
    }
    closed_location = {
        "pdf_url": "https://repository.example/closed.pdf",
        "is_oa": False,
        "license": "cc-by",
    }
    work = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1234/example",
        "open_access": {"is_oa": True, "oa_status": "hybrid"},
        "locations": [acs_open, repository_unknown, repository_open, closed_location],
        "best_oa_location": repository_open,
    }

    candidates = openalex_pdf_candidates(work)
    assert [candidate["pdf_url"] for candidate in candidates] == [
        repository_open["pdf_url"],
        acs_open["pdf_url"],
        repository_unknown["pdf_url"],
    ]

    by_url = {candidate["pdf_url"]: candidate for candidate in candidates}
    open_candidate = by_url[repository_open["pdf_url"]]
    acs_candidate = by_url[acs_open["pdf_url"]]
    unknown_candidate = by_url[repository_unknown["pdf_url"]]

    assert candidate_allowed(open_candidate, allow_license_unknown=False, include_publisher=False)
    assert not candidate_allowed(
        acs_candidate, allow_license_unknown=False, include_publisher=False
    )
    assert not candidate_allowed(
        unknown_candidate, allow_license_unknown=False, include_publisher=False
    )
    assert candidate_allowed(unknown_candidate, allow_license_unknown=True, include_publisher=False)
    assert candidate_allowed(acs_candidate, allow_license_unknown=False, include_publisher=True)

    [record] = attach_openalex([{"doi": "10.1234/example"}], {"items": [work]})
    assert record["download_eligible"] is True
    assert record["selected_pdf_url"] == repository_open["pdf_url"]
    assert record["selected_pdf_license"] == "cc-by"


def test_europepmc_adds_only_external_unpaywall_pdfs_with_provenance() -> None:
    external_pdf = "https://www.biorxiv.org/content/10.1101/2023.01.01.123456v1.full.pdf"
    work = {
        "id": "12345678",
        "source": "MED",
        "doi": "10.1234/example",
        "pmcid": "PMC1234567",
        "inPMC": "Y",
        "inEPMC": "Y",
        "epmcAuthMan": "N",
        "isOpenAccess": "Y",
        "fullTextUrlList": {
            "fullTextUrl": [
                {
                    "availability": "Open access",
                    "availabilityCode": "OA",
                    "documentStyle": "pdf",
                    "site": "Unpaywall",
                    "url": external_pdf,
                },
                {
                    "documentStyle": "pdf",
                    "site": "Europe_PMC",
                    "url": "https://europepmc.org/articles/PMC1234567?pdf=render",
                },
                {
                    "documentStyle": "pdf",
                    "site": "Unpaywall",
                    "url": "https://pubs.acs.org/doi/pdf/10.1234/example",
                },
                {
                    "documentStyle": "pdf",
                    "site": "Unpaywall",
                    "url": "https://repository.example/example_si_001.pdf",
                },
                {
                    "documentStyle": "html",
                    "site": "Unpaywall",
                    "url": "https://repository.example/item/1",
                },
            ]
        },
    }

    candidates = europepmc_pdf_candidates(work)
    assert len(candidates) == 1
    assert candidates[0] == {
        "pdf_url": external_pdf,
        "landing_page_url": None,
        "license": None,
        "license_id": None,
        "has_explicit_open_license": False,
        "version": None,
        "source_name": "External full text at www.biorxiv.org",
        "source_type": "repository",
        "host_organization_name": None,
        "metadata_source": "Europe PMC REST API fullTextUrlList (Unpaywall)",
        "europepmc_availability": "Open access",
        "europepmc_availability_code": "OA",
        "europepmc_document_style": "pdf",
    }
    assert not candidate_allowed(
        candidates[0], allow_license_unknown=False, include_publisher=False
    )
    assert candidate_allowed(candidates[0], allow_license_unknown=True, include_publisher=False)

    [record] = attach_europepmc(
        [{"doi": "10.1234/example", "pdf_candidates": []}], {"items": [work]}
    )
    assert record["europepmc_id"] == "MED:12345678"
    assert record["europepmc_pmcid"] == "PMC1234567"
    assert record["europepmc_in_pmc"] is True
    assert record["europepmc_in_epmc"] is True
    assert record["europepmc_author_manuscript"] is False
    assert record["europepmc_is_open_access"] is True
    assert record["europepmc_external_pdf_candidates"] == 1
    assert [candidate["pdf_url"] for candidate in record["pdf_candidates"]] == [external_pdf]


def test_similarity_checking_publisher_link_is_not_a_default_pdf_candidate() -> None:
    candidate = {
        "pdf_url": "https://pubs.acs.org/doi/pdf/10.1021/sb200001q",
        "has_explicit_open_license": False,
    }

    assert not candidate_allowed(candidate, allow_license_unknown=False, include_publisher=False)
    assert not candidate_allowed(candidate, allow_license_unknown=True, include_publisher=False)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/acssynbio.1c00001_si_001.pdf",
        "https://example.org/suppl_file/acssynbio.1c00001/suppl.pdf",
        "https://example.org/paper/supporting-information.pdf",
    ],
)
def test_supplementary_pdf_urls_are_not_accepted_as_articles(url: str) -> None:
    assert is_supplementary_artifact_url(url)
    assert not is_supplementary_artifact_url(
        "https://pubs.acs.org/doi/pdf/10.1021/acssynbio.1c00001"
    )
