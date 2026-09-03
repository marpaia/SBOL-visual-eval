"""Bridge error types, including the transient PMC access-challenge signal."""

from __future__ import annotations

PMC_CHALLENGE_RETRY_SECONDS = 15 * 60


class AuthorManuscriptVersionUnestablishedError(ValueError):
    """The page exposes a PDF but does not establish that it is a manuscript."""


class QueueClaimError(ValueError):
    """A bridge upload or failure does not match one live one-shot queue item."""


class PmcTransientChallengeError(RuntimeError):
    """PMC returned a temporary browser/interstitial response instead of article content."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: int = PMC_CHALLENGE_RETRY_SECONDS,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = min(max(1, retry_after_seconds), 60 * 60)
