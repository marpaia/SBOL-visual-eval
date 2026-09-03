# Publisher Versions of Record

For ACS Version of Record files available through a confirmed CU Boulder subscription, first open one ACS article through the [CU Boulder EZProxy login](https://colorado.idm.oclc.org/login?url=https://pubs.acs.org/) and authenticate in the browser. Copy only the `ezproxy` cookie value into a temporary environment variable, then run:

```bash
read -s "SBOL_EZPROXY_COOKIE?EZProxy cookie value: "
export SBOL_EZPROXY_COOKIE
uv run sbol-visual-data download-publisher-authenticated --delay 1
unset SBOL_EZPROXY_COOKIE
```

The downloader uses a browser-compatible TLS client because ACS rejects ordinary command-line clients even with a valid proxy session. On macOS it exports the required public SSL.com root certificate from the system keychain into a temporary directory; another platform can supply `--ca-bundle`. The cookie is read from `SBOL_EZPROXY_COOKIE`, never accepted as a command-line argument, and never written to a corpus file or report. Both the configured origin and every resolved download URL are restricted to the HTTPS ACS Publications host or the configured CU Boulder ACS EZProxy host. HTTP 429 responses honor `Retry-After` or use bounded backoff, and repeated authentication failures stop the run.

Automated download volume may trigger publisher or institutional access limits. An HTTP-success response containing HTML instead of a PDF is treated as a fatal access challenge; the known CU Libraries suspended-account page is identified without retaining its contents, and three consecutive fatal responses stop the run. If the proxy reports that the off-campus account is suspended or has lost access, do not resume automated acquisition or obtain a new session to work around the restriction. Resolve the suspension with the University Libraries before making any further publisher requests.

If copying the ephemeral cookie is undesirable, use the browser-only bridge instead:

```bash
uv run sbol-visual-data serve-publisher-bridge
```

Replace the token in `scripts/publisher_browser_runner.js` with the token printed by the bridge, then paste the script into the authenticated ACS tab's developer console. Authentication remains entirely in the browser: the loopback bridge receives only PDF bytes and public source URLs, verifies each file against its DOI/title, and stores it under the corresponding paper's `publisher/` directory. The runner is resumable, paced, and stops after five consecutive failures rather than consuming the queue during an outage or access challenge.

Publisher files are stored separately from repository and PMC variants. A successful `publisher.json` records `source_version=publishedVersion`, `retrieved_version_scope=current_version_of_record_at_retrieval`, and `version_assertion_method=authenticated_acs_version_of_record_pdf_endpoint`. This asserts that the retrieved file is the current ACS Version of Record at download time. It does not assert that its bytes or edition are identical to the edition inspected by the historical evaluators; `historical_evaluated_edition_relation=not_established` records that distinction explicitly. Replaced publisher files are retained with their actual archived `local_path` and reciprocal hash-based replacement links. No browser cookies or credentials are written to disk.
