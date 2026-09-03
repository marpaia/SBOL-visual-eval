"""Corpus construction for the SBOL Visual evaluation dataset.

The pipeline runs in stages, each owned by a subpackage or module:

- :mod:`.sources` acquires and parses the raw study artifacts (supplemental
  workbooks, rubric, anniversary-website snapshots).
- :mod:`.metadata` resolves each paper against Crossref, OpenAlex, and
  Europe PMC and assembles candidate PDF locations.
- :mod:`.build` deterministically renders the processed outputs
  (:mod:`.schema`) and the quality-control report.
- :mod:`.acquisition` downloads article PDFs and media through per-source
  backends with strict identity checks.
- :mod:`.provenance` defines the manifest vocabulary and identity checks
  every acquired artifact must satisfy.
- :mod:`.inventory` reports local per-paper coverage; :mod:`.validation`
  audits the whole corpus, from raw checksums to artifact provenance.
"""

from .acquisition.downloads import download_papers
from .build import build_corpus
from .inventory import build_local_inventory
from .layout import Layout
from .sources import acquire_sources
from .validation import validate_corpus

__all__ = [
    "Layout",
    "acquire_sources",
    "build_corpus",
    "build_local_inventory",
    "download_papers",
    "validate_corpus",
]
