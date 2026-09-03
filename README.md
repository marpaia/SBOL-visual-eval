# SBOL Visual Evaluation

`sbol_visual_eval` is a system for machine evaluation of [SBOL Visual](https://sbolstandard.org/visual/) compliance: detecting genetic-design diagrams in scientific figures and assessing how well they follow the diagram standard. The evaluator is scored against a provenance-preserving historical corpus — the published record of every ACS Synthetic Biology paper reviewed for SBOL Visual adoption from 2012 through 2023 — and this repository contains both the evaluator's home package and the complete tooling that builds and audits that corpus.

## Repository layout

```text
src/sbol_visual_eval/
  cli.py               the sbol-visual-data command
  corpus/              validation-corpus construction and auditing
    sources/           raw study artifacts: workbooks, rubric, website snapshots
    metadata/          Crossref / OpenAlex / Europe PMC resolution and PDF candidates
    acquisition/       per-source article-PDF acquisition backends
    provenance/        manifest vocabulary and artifact identity checks
    inventory/         per-paper local coverage reporting
    validation/        end-to-end corpus integrity checks
data/                  the corpus itself (see data/README.md)
docs/                  corpus build and acquisition guides
tests/                 mirrors src/sbol_visual_eval/corpus
```

Evaluation models and metrics build on top of the `corpus` package; the corpus supplies their ground truth and the local PDF/figure artifacts they run against.

## Quickstart

```bash
uv sync --extra dev
uv run sbol-visual-data prepare
uv run sbol-visual-data validate
uv run pytest
```

This acquires the raw study sources, builds the normalized corpus, and verifies every checksum with a deterministic rebuild. The released ground truth is **paper-level count supervision**, not figure-level supervision — see the [ground-truth limitation](docs/corpus.md#ground-truth-limitation) before designing any evaluation on top of it.

## Documentation

- [The validation corpus](docs/corpus.md) — sources, ground-truth semantics, deterministic build and validation, and the `data/` layout
- [Acquiring source papers](docs/acquiring-papers.md) — the license-gated PDF downloader, manifest backfill, and local coverage inventory
- [bioRxiv predecessor preprints](docs/acquiring-biorxiv-preprints.md) — the six reviewed preprint mappings and their verification
- [PMC article packages and the browser bridge](docs/acquiring-pmc.md) — the official article-data path and the public-page author-manuscript fallback
- [Publisher Versions of Record](docs/acquiring-publisher-vor.md) — authenticated ACS acquisition through EZProxy
- [`data/README.md`](data/README.md) — field semantics, source precedence, known inconsistencies, and licensing boundaries
