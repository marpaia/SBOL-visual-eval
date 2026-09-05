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


def _verdict_from_payload(payload: dict[str, Any], figure_number: int) -> FigureVerdict:
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
        borderline=bool(payload.get("borderline", False)),
    )


def parse_verdict(reply: str, figure_number: int) -> FigureVerdict:
    return _verdict_from_payload(_extract_json_object(reply), figure_number)


def parse_verdicts(reply: str, figure_numbers: tuple[int, ...]) -> tuple[FigureVerdict, ...]:
    """Parse one whole-paper response and require exactly the requested figures."""
    payload = _extract_json_object(reply)
    entries = payload.get("figures")
    if not isinstance(entries, list):
        raise JudgeParseError("whole-paper judge reply lacks a 'figures' list")

    verdicts_by_number: dict[int, FigureVerdict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or "figure_number" not in entry:
            raise JudgeParseError(f"malformed whole-paper figure verdict: {entry!r}")
        try:
            figure_number = int(entry["figure_number"])
        except (TypeError, ValueError) as error:
            raise JudgeParseError(f"invalid figure number: {entry['figure_number']!r}") from error
        if figure_number in verdicts_by_number:
            raise JudgeParseError(f"duplicate verdict for Figure {figure_number}")
        verdicts_by_number[figure_number] = _verdict_from_payload(entry, figure_number)

    requested = set(figure_numbers)
    returned = set(verdicts_by_number)
    if returned != requested:
        raise JudgeParseError(
            f"whole-paper verdict figures {sorted(returned)} do not match requested "
            f"figures {sorted(requested)}"
        )
    return tuple(verdicts_by_number[number] for number in figure_numbers)
