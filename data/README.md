# Validation data

## Source layers

The corpus keeps three historical source layers separate:

1. The twelve yearly supplemental workbooks (`s002`–`s013`) are the most detailed released annotations. They enumerate every analyzed paper and its four figure counts. The normalized values are recomputed from each monthly sheet's static `C:F` cells; the cached `Summary` formulas are retained as a separate comparison layer because some formulas point to obsolete row ranges.
2. The anniversary webpages provide yearly headline totals and list only papers for which every SBOL-compatible figure was compliant. A star marks papers for which all compatible figures also followed best practices. This filtering rule is implicit in the page tables.
3. The supplemental overview workbook (`s001`) contains an earlier set of yearly totals for several years. It is retained verbatim and compared in the quality-control report, not silently substituted for the yearly workbooks.

The normalized corpus uses the yearly workbooks as its row-level source of truth and preserves the other two layers as independent comparison targets.

The operational reviewer checklist and the normative standard are also separate targets. `historical_2025_rubric_v1` is the 31-rule checklist, including the reviewers' documented exceptions, that should be used to reproduce the retrospective. `sbol_visual_3_0_strict` is the complete SBOL Visual 3.0 specification. A strict specification checker can disagree with the historical target where the reviewers deliberately accepted older or degenerate glyphs. Both source files and hashes are recorded in `raw/SOURCES.json`.

## Reproducibility and integrity

`processed/MANIFEST.json` binds the normalized release to the SHA-256 hash of `raw/SOURCES.json` and records the byte size, row count, and SHA-256 hash of `papers.jsonl`, `papers.csv`, `rubric.csv`, `website_tables.csv`, and `yearly_totals.csv`. Run `uv run sbol-visual-data validate` after acquiring or rebuilding data. Validation checks the manifest and semantic row/count invariants, then reconstructs all five files from the raw sources and compressed Crossref, OpenAlex, and Europe PMC snapshots and compares their exact deterministic bytes. `reports/quality_control.json` records the processed-manifest hash, so changing either the raw inputs or a normalized output requires a new build.

## Core labels

For each paper:

- `figures_total`: figures reviewed in the main manuscript.
- `figures_sbol_visual_compatible`: figures judged appropriate for representation with SBOL Visual.
- `figures_sbol_visual_compliant`: compatible figures judged compliant with the study rubric.
- `figures_best_practices`: compatible/compliant figures judged to follow the study's best practices.

The released workbooks do not identify figure numbers. `label_granularity` is therefore always `paper_aggregate_counts`, and `figure_identities_available` is always false.

Derived paper flags are recomputed from the four counts rather than copied from workbook formulas:

- `has_compatible_figures`: compatible > 0.
- `has_compliant_figures`: compliant > 0.
- `all_compatible_figures_compliant`: compatible > 0 and compatible = compliant.
- `has_best_practice_figures`: best-practice > 0.
- `all_compatible_figures_best_practice`: compatible > 0 and compatible = compliant = best-practice.

## Source precedence and anomalies

Raw values are immutable. The build reports disagreements among sources and invariant violations without correcting them. In particular, one 2020 workbook row records three compatible figures for a paper with two total figures. This is retained as historical ground truth and flagged for adjudication.

The 2020 anniversary page, the monthly rows, the yearly workbook's cached Summary cells, and the overview workbook disagree on their yearly totals. Consumers must select a named source layer rather than blending them.

## Full text and licensing

The annotation supplements are published through ACS Figshare under CC BY-NC 4.0. Paper full text has heterogeneous licenses. `papers.jsonl` records license metadata per candidate location. The default downloader accepts only candidates for which OpenAlex reports an explicit Creative Commons or public-domain license, does not bypass authentication or access controls, and records the exact source/version/checksum of every accepted PDF.

OpenAlex license metadata is a discovery signal, not legal advice. Recheck the original repository or publisher terms before redistributing PDFs or using them beyond the allowed scope. Accepted or submitted manuscripts can also differ from the Version of Record, including figure numbering or layout; retain `source_version` in every downstream figure record.

Authenticated ACS files are acquired through the loopback browser bridge described in the repository README. They are kept at `papers/<year>/<doi-slug>/publisher/paper.pdf` with a sibling `publisher.json` provenance manifest. Repository, PMC, archived manuscript, and publisher variants are never collapsed into a single undocumented file.

Some repository PDFs append Supporting Information to the main manuscript. When a generic `metadata.json` contains `artifact_scope: main_manuscript_with_appended_supporting_information`, downstream figure extraction must honor its inclusive, one-based `figure_extraction_pdf_page_range` and exclude pages beginning at `appended_supporting_information_pdf_page_start`. This keeps Supporting Information figures out of the historical `figures_total` target, which covers only the main manuscript.

Rejected downloads are preserved recoverably but excluded from coverage. The generic scan quarantined a 144-page MIT thesis returned for `10.1021/sb4000417` and a two-page repository citation cover plus isolated Figure 1 returned for `10.1021/acssynbio.9b00333` under each paper's `rejected/` directory. A Caltech file for `10.1021/sb5002196` was correctly reclassified under `supplementary/`. These cases are regression fixtures for article-role detection, not usable main-manuscript PDFs.

Six reviewed predecessor preprints have a bioRxiv variant under `versions/biorxiv_preprint_v1/`. Five are title-drift cases; `10.1021/acssynbio.9b00275` retains its published title and is linked through its processed Caltech discovery candidate plus bioRxiv's explicit publication mapping. Each hash-named PDF is paired with a hash-named manifest and an immutable raw `biorxiv_api__<sha12>.json` details-API snapshot. Validation follows a conjunctive chain: the exact reviewed discovery candidate, allowlisted preprint DOI and version, the API `published` field matching the processed ACS DOI, canonical API/JATS/PDF URLs derived from the API date and DOI, and the PDF's exact-boundary preprint DOI, strong API-title match, and posting-date banner. These files are `submittedVersion` preprints, never Versions of Record, and their equivalence to the edition evaluated historically is not established. `reports/biorxiv_preprint_acquisition.csv` and `.json` record acquisition outcomes. Direct controlled HTTPS downloads may have verified transport; staged imports retain physical coverage but remain transport-unverified.

`reports/local_corpus_inventory.csv` is the DOI-keyed join across those artifact stores. `article_artifacts_json` maps every article PDF/XML/text path to its source, version, SHA-256 hash, provenance manifest, local-presence flag, and verification status. The `has_local_*`, `article_*_count`, and unprefixed path fields report physical files even when their provenance is invalid or unverified; parallel `has_verified_local_*`, `verified_article_*_count`, and `verified_*_paths_json` fields report the independently verified subset. `preferred_pdf_path` is selected only from verified article PDFs and chooses an explicitly identified Version of Record first. `has_local_version_of_record_pdf` and the VOR counts likewise require verified provenance and never infer publication status from filename or location alone.

Europe PMC is a second discovery layer. The build snapshots its core API response and records PMCID coverage, but adds only external PDF URLs attributed to Unpaywall. It excludes Europe PMC and NCBI article-rendering URLs (PMC acquisition uses the official article-data service), ACS publisher URLs already represented through Crossref, HTML-only links, and recognizable supplementary-information filenames. Because the API's external-link record does not state an artifact license or manuscript version, these fields remain unknown rather than inferred; the downloader requires `--allow-license-unknown` and still performs PDF and article-identity validation.
