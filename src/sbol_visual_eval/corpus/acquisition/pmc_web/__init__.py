"""Browser-bridge fallback for public PMC author-manuscript PDFs.

A loopback HTTP bridge hands one queued record at a time to a browser-side
runner, which fetches the PDF inside the user's same-origin PMC session and
uploads only the bytes. The bridge verifies identity in a resource-limited
child process and records full evidence provenance.
"""

from .bridge import PmcWebBridge
from .errors import (
    PMC_CHALLENGE_RETRY_SECONDS,
    AuthorManuscriptVersionUnestablishedError,
    PmcTransientChallengeError,
    QueueClaimError,
)
from .server import serve_pmc_web_bridge
from .urls import PMC_ORIGIN

__all__ = [
    "PMC_CHALLENGE_RETRY_SECONDS",
    "PMC_ORIGIN",
    "AuthorManuscriptVersionUnestablishedError",
    "PmcTransientChallengeError",
    "PmcWebBridge",
    "QueueClaimError",
    "serve_pmc_web_bridge",
]
