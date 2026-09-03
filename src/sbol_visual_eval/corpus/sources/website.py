"""Parser for the anniversary-website yearly table snapshots."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from ..config import ANNIVERSARY_ROOT, MONTH_NUMBER, YEARS
from ..layout import Layout

SUMMARY_KEYS = {
    "Total Articles Analyzed": "papers_total",
    "Total Figures in All Articles": "figures_total",
    "Figures Compatible with SBOL Visual": "figures_sbol_visual_compatible",
    "Figures Compatible with SBOL Visual & Fully Compliant": "figures_sbol_visual_compliant",
    "Figures Compatible with SBOL Visual & Following Best Practices": "figures_best_practices",
}


def parse_website(layout: Layout) -> tuple[list[dict[str, Any]], dict[int, dict[str, int]]]:
    table_records: list[dict[str, Any]] = []
    summaries: dict[int, dict[str, int]] = {}
    for year in YEARS:
        path = layout.website / f"{year}.html"
        soup = BeautifulSoup(path.read_bytes(), "html.parser")
        article = soup.select_one("article .article-style")
        if article is None:
            raise ValueError(f"cannot locate article content in {path}")
        summary: dict[str, int] = {}
        for item in article.select("#summary + ul li"):
            text = item.get_text(" ", strip=True)
            for label, field in SUMMARY_KEYS.items():
                match = re.match(rf"^{re.escape(label)}\s*:\s*([0-9,]+)", text)
                if match:
                    summary[field] = int(match.group(1).replace(",", ""))
        if set(summary) != set(SUMMARY_KEYS.values()):
            raise ValueError(f"incomplete website summary for {year}: {summary}")
        summaries[year] = summary

        for table in article.select("table"):
            heading = table.find_previous("h2")
            month = heading.get_text(" ", strip=True) if heading else ""
            if month not in MONTH_NUMBER:
                raise ValueError(f"cannot determine month for a {year} table")
            for row_index, row in enumerate(table.select("tbody tr"), start=1):
                cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
                if len(cells) < 3:
                    continue
                table_records.append(
                    {
                        "year": year,
                        "issue_month": month,
                        "website_row": row_index,
                        "title_website": cells[0],
                        "figures_sbol_visual_compliant": int(cells[1]),
                        "figures_best_practices": int(cells[2]),
                        "star": len(cells) > 3 and "⭐" in cells[3],
                        "source_url": f"{ANNIVERSARY_ROOT}acs-{year}/",
                        "source_path": str(path.relative_to(layout.root)),
                    }
                )
    return table_records, summaries
