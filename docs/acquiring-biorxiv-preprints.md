# bioRxiv predecessor preprints

Six reviewed bioRxiv PDFs are predecessor preprints of the corresponding ACS articles. Five use titles that changed substantially before publication, while one was discovered through its Caltech repository record and retains the same title. They have a separate, deliberately finite acquisition path:

```bash
uv run sbol-visual-data acquire-biorxiv-preprints
```

This command is limited to six reviewed ACS DOI/bioRxiv DOI pairs. For each pair it requires the bioRxiv details API to return exactly one version-1 record whose `published` field is the target ACS DOI, rederives the reviewed canonical PDF URL from the API DOI and posting date, and verifies the PDF's exact bioRxiv DOI banner, API title, and posting date. The original five mappings remain bound to their exact processed bioRxiv candidate URL; `10.1021/acssynbio.9b00275` is instead bound to its exact processed Caltech discovery candidate and the API-derived direct bioRxiv PDF. It retains the raw API response and PDF as immutable hash-named files under `versions/biorxiv_preprint_v1/`; the companion manifest calls the artifact a `submittedVersion` and explicitly withholds equivalence to the historically evaluated edition. Direct HTTPS responses record their request and final URLs and count as verified provenance. A staged recovery import is also available with `--source-dir`, but because the builder did not observe that earlier HTTP response, imported bytes are explicitly transport-unverified and cannot become the preferred verified PDF.
