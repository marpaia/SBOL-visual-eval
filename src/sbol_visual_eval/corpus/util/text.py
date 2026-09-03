"""Text normalization for titles, DOIs, and markup-bearing metadata fields."""

from __future__ import annotations

import html as html_lib
import re
import unicodedata


def clean_markup(text: str | None) -> str:
    value = re.sub(r"<[^>]+>", "", html_lib.unescape(text or ""))
    return re.sub(r"\s+", " ", value).strip()


def normalize_title(text: str | None) -> str:
    value = clean_markup(text)
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(character for character in value if not unicodedata.combining(character))
    replacements = {
        "β": "beta",
        "α": "alpha",
        "ϕ": "phi",
        "φ": "phi",
        "γ": "gamma",
        "δ": "delta",
        "λ": "lambda",
        "μ": "mu",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "", value)


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    value = doi.strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return value or None
