"""Validation of ACS publisher origins and per-DOI PDF endpoint URLs."""

from __future__ import annotations

from urllib.parse import quote, urlparse

PUBLISHER_ORIGIN = "https://pubs-acs-org.colorado.idm.oclc.org"


ACS_PUBLISHER_HOSTS = frozenset(
    {
        "pubs-acs-org.colorado.idm.oclc.org",
        "pubs.acs.org",
    }
)


def _validated_publisher_origin(origin: str) -> str:
    parsed = urlparse(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"invalid authenticated ACS origin: {origin!r}") from error
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or host not in ACS_PUBLISHER_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "authenticated publisher origin must be the HTTPS ACS Publications "
            "origin or the configured CU Boulder ACS EZProxy origin"
        )
    return f"https://{host}"


def _validated_publisher_url(
    value: str | None,
    *,
    field_name: str,
    allow_none: bool = False,
) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is missing")
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} is not a valid URL") from error
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.casefold() != "https"
        or host not in ACS_PUBLISHER_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ValueError(
            f"{field_name} must resolve on the ACS Publications host or the "
            "configured CU Boulder ACS EZProxy host"
        )
    return value


def _publisher_url(doi: str, origin: str) -> str:
    origin = _validated_publisher_origin(origin)
    encoded = "/".join(quote(part, safe="") for part in doi.split("/"))
    return f"{origin}/doi/pdf/{encoded}"
