"""Bibliographic metadata resolution against Crossref, OpenAlex, and Europe PMC."""

from .crossref import fetch_crossref, match_crossref
from .europepmc import attach_europepmc, fetch_europepmc
from .openalex import attach_openalex, fetch_openalex

__all__ = [
    "attach_europepmc",
    "attach_openalex",
    "fetch_crossref",
    "fetch_europepmc",
    "fetch_openalex",
    "match_crossref",
]
