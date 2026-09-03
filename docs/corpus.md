# The validation corpus

The corpus is derived from the published supporting information for *A Decade of SBOL Visual: Growing Adoption of a Diagram Standard for Engineering Biology* (DOI [`10.1021/acssynbio.5c00417`](https://doi.org/10.1021/acssynbio.5c00417)). The source workbooks cover every ACS Synthetic Biology paper reviewed from 2012 through 2023 and give four paper-level counts: total figures, figures appropriate for SBOL Visual, compliant figures, and figures following the study's best-practice rubric.

## Ground-truth limitation

The released ground truth is **paper-level count supervision**, not figure-level supervision. It does not identify which figure numbers received each label and it does not provide rule-by-rule violations. An evaluator can be scored on its per-paper counts immediately, but image-level training or evaluation requires either weak/count supervision or a separately adjudicated figure-level layer. Never infer figure identities from the aggregate counts and record them as historical ground truth.

## Building and validating

```bash
uv sync --extra dev
uv run sbol-visual-data prepare
uv run sbol-visual-data validate
uv run pytest
```

`prepare` downloads the CC BY-NC 4.0 supplements and rubric, snapshots the public anniversary pages, normalizes the paper-level labels, resolves DOI metadata through Crossref, and discovers full-text locations through OpenAlex and Europe PMC.

[Europe PMC enrichment](https://europepmc.org/RestfulWebService) uses its core metadata API and adds only external PDF links that Europe PMC attributes to Unpaywall. Direct Europe PMC/PMC rendering links are excluded here because PMC content is acquired through the [supported article-data path](https://europepmc.org/developers) (see [PMC acquisition](acquiring-pmc.md)); ACS links are already supplied by Crossref. Europe PMC does not provide a license for these external candidates, so they remain ineligible under the default download policy and retain their Europe PMC/Unpaywall provenance for an authorized acquisition pass.

`validate` verifies every raw-source checksum, checks the normalized table hashes recorded in `data/processed/MANIFEST.json`, and deterministically reconstructs all five processed outputs from the raw workbooks, webpages, rubric, and frozen metadata snapshots. A processed file edited or left stale after an input change therefore fails validation even when its row count is still plausible.

## Data layout

```text
data/
  raw/
    annotations/       published supplemental XLSX files
    rubric/            the study rubric exported from its linked Google Doc
                       plus the versioned SBOL Visual 3.0 specification
    metadata/          compressed Crossref/OpenAlex/Europe PMC response snapshots
    website/           unmodified anniversary-page HTML snapshots
    SOURCES.json       source URLs, licenses, checksums, and retrieval metadata
  processed/
    MANIFEST.json      source-manifest binding, row counts, and output checksums
    papers.csv         flat paper-level labels plus resolved source metadata
    papers.jsonl       complete records, including all candidate PDF locations
    rubric.csv         normalized compliance and best-practice rules
    yearly_totals.csv  totals recomputed directly from yearly workbooks
    website_tables.csv rows published on the anniversary year pages
  reports/
    quality_control.json
    metadata_match_review.csv
    downloads.csv
    pmc_acquisition.csv
    pmc_web_acquisition.csv
    pmc_web_attempts.jsonl
    biorxiv_preprint_acquisition.csv
    publisher_acquisition.csv
    publisher_attempts.jsonl
    local_corpus_inventory.csv
  papers/              downloaded PDFs and per-paper metadata; gitignored
```

See [`data/README.md`](../data/README.md) for field semantics, source precedence, known inconsistencies, and licensing boundaries.
