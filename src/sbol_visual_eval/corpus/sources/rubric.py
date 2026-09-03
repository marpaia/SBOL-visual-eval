"""Parser for the historical compliance and best-practice rubric export."""

from __future__ import annotations

import re
from typing import Any

from ..config import RUBRIC_URL
from ..layout import Layout


def parse_rubric(layout: Layout) -> list[dict[str, Any]]:
    path = layout.rubric / "sbol_visual_diagram_rubric.txt"
    text = path.read_text(encoding="utf-8-sig")
    category: str | None = None
    rules: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "Compliance:":
            category = "compliance"
            continue
        if line == "Best practices:":
            category = "best_practice"
            continue
        match = re.match(r"^(5(?:\.\d+)+):\s*(.+)$", line)
        if match and category:
            statement = match.group(2).strip()
            normative = next(
                (
                    keyword
                    for keyword in ("MUST NOT", "MUST", "SHOULD NOT", "SHOULD")
                    if keyword in statement
                ),
                None,
            )
            rules.append(
                {
                    "rule_key": f"{category}:{match.group(1)}",
                    "rubric_id": "historical_2025_rubric_v1",
                    "category": category,
                    "section": match.group(1),
                    "historical_rule_id": match.group(1),
                    "canonical_specification_section": (
                        "5.4.6"
                        if category == "best_practice" and match.group(1) == "5.4.5"
                        else match.group(1)
                    ),
                    "normative_keyword": normative,
                    "statement": statement,
                    "notes": [],
                    "source_url": RUBRIC_URL,
                }
            )
        elif line.startswith("*") and rules:
            rules[-1]["notes"].append(line.removeprefix("*").strip())
    return rules
