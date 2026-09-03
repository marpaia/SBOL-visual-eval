"""Acquisition of official PMC article packages from the PMC Article Datasets.

DOIs resolve to PMCIDs through the NCBI ID Converter; package metadata and
objects come only from the official ``pmc-oa-opendata`` S3 bucket.
"""

from .acquire import acquire_pmc

__all__ = ["acquire_pmc"]
