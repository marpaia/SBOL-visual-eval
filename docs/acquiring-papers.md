# Acquiring source papers

Every acquisition backend reads `data/processed/papers.jsonl` — produced by the
[corpus build](corpus.md) — and records complete provenance for every artifact it
stores. This document covers the generic license-gated downloader and the local
coverage inventory; the source-specific backends are documented separately:
[PMC](acquiring-pmc.md), [bioRxiv predecessor preprints](acquiring-biorxiv-preprints.md),
and [publisher Versions of Record](acquiring-publisher-vor.md).

## Open-licensed downloads

To download source papers for which OpenAlex reports an explicit open license:

```bash
uv run sbol-visual-data download-papers
```

The downloader tries candidate locations in version-aware order, verifies that each response is a readable PDF, and checks its first pages for the title or DOI before accepting it. It does not bypass publisher access controls. Use `--allow-license-unknown` only after reviewing the rights for those sources.

Repository candidates receive one content-negotiation fallback when the normal `Accept: application/pdf` request returns HTTP 404 or a successful non-PDF response: the downloader retries the exact same URL using the client's default `Accept` header. This accommodates EPrints installations that otherwise return a false 404. It is never applied to journal or publisher candidates, and every use is recorded in the artifact manifest and acquisition report as `repository_request_retries`.

If you have separately confirmed institutional or project-specific access rights, attempt every discovered repository and publisher full-text link with:

```bash
uv run sbol-visual-data download-papers --allow-license-unknown --include-publisher
```

Every attempt and failure is recorded in `data/reports/downloads.csv`; reruns reuse verified local PDFs.

After upgrading a corpus created by an older downloader, normalize its repository-paper manifests once:

```bash
uv run sbol-visual-data backfill-generic-manifests
```

This command re-verifies every current and archived repository PDF, binds it to its processed DOI record, and records its actual path, checksum, identity evidence, and edition-provenance status without inventing a license or version.

## Local coverage inventory

After any acquisition pass, regenerate the one-row-per-paper local coverage report:

```bash
uv run sbol-visual-data inventory
```
