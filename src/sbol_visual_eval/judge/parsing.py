"""Parsing model replies into figure verdicts.

Replies are requested as bare JSON but tolerated with surrounding prose
or code fences; anything without one well-formed verdict object fails
loudly so a judging sweep never silently records garbage.
"""

from __future__ import annotations

import json
from typing import Any

from .schema import FigureVerdict, RuleFinding, RuleVerdict


class JudgeParseError(ValueError):
    """The model reply did not contain a well-formed verdict object."""


def _extract_json_object(reply: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for start, character in enumerate(reply):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(reply[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise JudgeParseError("no JSON object found in judge reply")


def parse_verdict(reply: str, figure_number: int) -> FigureVerdict:
    payload = _extract_json_object(reply)
    if "compatible" not in payload:
        raise JudgeParseError("judge reply lacks a 'compatible' field")

    findings = []
    for entry in payload.get("findings") or []:
        if not isinstance(entry, dict) or "rule_key" not in entry or "verdict" not in entry:
            raise JudgeParseError(f"malformed rule finding: {entry!r}")
        try:
            verdict = RuleVerdict(entry["verdict"])
        except ValueError as error:
            raise JudgeParseError(f"unknown rule verdict {entry['verdict']!r}") from error
        findings.append(
            RuleFinding(
                rule_key=str(entry["rule_key"]),
                verdict=verdict,
                evidence=str(entry.get("evidence", "")),
            )
        )

    return FigureVerdict(
        figure_number=figure_number,
        compatible=bool(payload["compatible"]),
        rationale=str(payload.get("rationale", "")),
        findings=tuple(findings),
    )
