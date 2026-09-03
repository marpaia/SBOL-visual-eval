"""Loading the historical review rubric that conditions every judgment.

The evaluator targets ``historical_2025_rubric_v1`` — the 31-rule reviewer
checklist with its documented exceptions — not the strict SBOL Visual 3.0
specification. Judgments must reproduce the historical panel's behavior,
leniency included, so the rules and their exception notes are passed to the
judge verbatim from ``data/processed/rubric.csv``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from ..corpus.layout import Layout

COMPLIANCE = "compliance"
BEST_PRACTICE = "best_practice"


@dataclass(frozen=True)
class RubricRule:
    """One normalized rule of the historical review checklist."""

    rule_key: str
    category: str
    section: str
    normative_keyword: str
    statement: str
    notes: tuple[str, ...]


def load_rubric(source: Layout | Path) -> list[RubricRule]:
    """Load ``data/processed/rubric.csv`` (or an explicit CSV path)."""
    path = source if isinstance(source, Path) else source.processed / "rubric.csv"
    rules = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rules.append(
                RubricRule(
                    rule_key=row["rule_key"],
                    category=row["category"],
                    section=row["section"],
                    normative_keyword=row["normative_keyword"],
                    statement=row["statement"],
                    notes=tuple(json.loads(row["notes_json"])),
                )
            )
    return rules


def compliance_rules(rules: list[RubricRule]) -> list[RubricRule]:
    return [rule for rule in rules if rule.category == COMPLIANCE]


def best_practice_rules(rules: list[RubricRule]) -> list[RubricRule]:
    return [rule for rule in rules if rule.category == BEST_PRACTICE]


def render_rule(rule: RubricRule) -> str:
    line = f"- [{rule.rule_key}] ({rule.normative_keyword}) {rule.statement}"
    for note in rule.notes:
        line += f"\n  Reviewer exception: {note}"
    return line


def render_rubric(rules: list[RubricRule]) -> str:
    """Render both rule sections exactly as the judge prompt embeds them."""
    sections = [
        "COMPLIANCE RULES (a compatible figure is compliant only if it violates none):",
        *(render_rule(rule) for rule in compliance_rules(rules)),
        "",
        "BEST-PRACTICE RULES (a compliant figure follows best practices only if it violates none):",
        *(render_rule(rule) for rule in best_practice_rules(rules)),
    ]
    return "\n".join(sections)
