"""PMC public-article-page manifest vocabulary and identity checks."""

from .article_xml import PMC_WEB_MAX_ARTICLE_XML_BYTES
from .evidence import PMC_WEB_MAX_EVIDENCE_BYTES, PMC_WEB_VARIANT_DIRECTORY
from .identity import (
    PMC_WEB_AUTHOR_MANUSCRIPT_METHODS,
    PMC_WEB_EXPLICIT_LICENSE_CODES,
    PMC_WEB_SOURCE_ACCESS,
    PMC_WEB_SOURCE_NAME,
    PMC_WEB_TRANSPORT_LIMITATION,
    PMC_WEB_TRANSPORT_STATUS,
    pmc_web_generic_manifest_identity_errors,
)

__all__ = [
    "PMC_WEB_AUTHOR_MANUSCRIPT_METHODS",
    "PMC_WEB_EXPLICIT_LICENSE_CODES",
    "PMC_WEB_MAX_ARTICLE_XML_BYTES",
    "PMC_WEB_MAX_EVIDENCE_BYTES",
    "PMC_WEB_SOURCE_ACCESS",
    "PMC_WEB_SOURCE_NAME",
    "PMC_WEB_TRANSPORT_LIMITATION",
    "PMC_WEB_TRANSPORT_STATUS",
    "PMC_WEB_VARIANT_DIRECTORY",
    "pmc_web_generic_manifest_identity_errors",
]
