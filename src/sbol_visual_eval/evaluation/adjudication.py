"""Expert-review artifacts for exact compatibility-stage disagreements."""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import atomic_write_bytes, utc_now, write_csv, write_json
from ..figures import render_page_png
from .compatibility import saturated_compatibility_papers, saturated_figures

ADJUDICATION_FIELDS = (
    "doi",
    "year",
    "figure_number",
    "image_path",
    "historical_compatible",
    "evaluator_compatible",
    "evaluator_rationale",
    "adjudicated_compatible",
    "adjudication_rationale",
    "reviewer",
    "reviewed_at",
)

REQUIRED_REPORT_FIELDS = {
    "doi",
    "year",
    "figure_number",
    "expected_compatible",
    "predicted_compatible",
    "correct",
    "rationale",
    "judge_error",
}


def build_compatibility_adjudication(
    layout: Layout,
    report_path: Path,
    *,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Render exact saturated-label disagreements into a separate review layer."""
    report_path = report_path.resolve()
    target = (
        output_dir.resolve()
        if output_dir is not None
        else layout.data / "adjudicated" / report_path.stem
    )
    image_dir = target / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    existing_decisions = _load_existing_decisions(target / "decisions.csv")

    with report_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        missing = REQUIRED_REPORT_FIELDS - fields
        if missing:
            raise ValueError(f"compatibility report lacks required fields: {sorted(missing)}")
        report_rows = list(reader)

    pool = {
        (figure.doi, figure.figure_number): figure
        for figure in saturated_figures(layout, saturated_compatibility_papers(layout))
    }
    decisions = []
    seen: set[tuple[str, int]] = set()
    judge_errors = 0
    for row in report_rows:
        if row["judge_error"]:
            judge_errors += 1
            continue
        if row["correct"] == "True":
            continue
        key = (row["doi"], int(row["figure_number"]))
        if key in seen:
            raise ValueError(f"compatibility report repeats {key[0]} Figure {key[1]}")
        seen.add(key)
        figure = pool.get(key)
        if figure is None:
            raise ValueError(f"report figure is not in the saturated VOR pool: {key}")
        expected = row["expected_compatible"] == "True"
        if expected is not figure.expected_compatible:
            raise ValueError(f"report label disagrees with saturated counts for {key}")

        filename = f"{_slug(figure.doi)}__figure-{figure.figure_number}.png"
        image_path = image_dir / filename
        atomic_write_bytes(
            image_path,
            render_page_png(layout.root / figure.pdf_path, figure.page_number),
        )
        expert_fields = {
            field: existing_decisions.get(key, {}).get(field, "")
            for field in (
                "adjudicated_compatible",
                "adjudication_rationale",
                "reviewer",
                "reviewed_at",
            )
        }
        decisions.append(
            {
                "doi": figure.doi,
                "year": figure.year,
                "figure_number": figure.figure_number,
                "image_path": f"images/{filename}",
                "historical_compatible": expected,
                "evaluator_compatible": row["predicted_compatible"] == "True",
                "evaluator_rationale": row["rationale"],
                **expert_fields,
            }
        )

    current_keys = {(str(row["doi"]), int(row["figure_number"])) for row in decisions}
    removed_review_keys = set(existing_decisions) - current_keys
    if removed_review_keys:
        raise ValueError(
            "refusing to discard existing adjudication rows absent from the new report: "
            f"{sorted(removed_review_keys)}"
        )
    write_csv(target / "decisions.csv", decisions, ADJUDICATION_FIELDS)
    generated_at = utc_now()
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_report": layout.display_path(report_path),
        "source_rows": len(report_rows),
        "judge_errors_excluded": judge_errors,
        "disagreements": len(decisions),
        "historical_layer_modified": False,
        "decisions_path": layout.display_path(target / "decisions.csv"),
        "review_path": layout.display_path(target / "index.html"),
    }
    write_json(target / "manifest.json", manifest)
    atomic_write_bytes(target / "index.html", _render_gallery(decisions, report_path.name).encode())
    return manifest


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "__", value)


def _load_existing_decisions(path: Path) -> dict[tuple[str, int], dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    decisions: dict[tuple[str, int], dict[str, str]] = {}
    for row in rows:
        key = (row["doi"], int(row["figure_number"]))
        if key in decisions:
            raise ValueError(f"existing adjudication layer repeats {key}")
        decisions[key] = row
    return decisions


def _render_gallery(decisions: list[dict[str, Any]], report_name: str) -> str:
    cards = []
    for row in decisions:
        historical = "compatible" if row["historical_compatible"] else "not compatible"
        evaluator = "compatible" if row["evaluator_compatible"] else "not compatible"
        cards.append(
            f"""<article>
  <img src="{html.escape(str(row["image_path"]))}" alt="{html.escape(str(row["doi"]))} Figure {row["figure_number"]}">
  <div class="body">
    <h2>{html.escape(str(row["doi"]))} · Figure {row["figure_number"]} · {row["year"]}</h2>
    <p><strong>History:</strong> {historical} · <strong>Evaluator:</strong> {evaluator}</p>
    <p>{html.escape(str(row["evaluator_rationale"]))}</p>
  </div>
</article>"""
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SBOL Visual disagreement adjudication</title>
<style>
body {{ margin: 0 auto; max-width: 1200px; padding: 2rem; font: 16px/1.5 system-ui; color: #17202a; background: #f5f7f8; }}
header {{ margin-bottom: 2rem; }}
article {{ overflow: hidden; margin: 0 0 2rem; border: 1px solid #ccd6dd; border-radius: 12px; background: white; box-shadow: 0 4px 16px #0001; }}
img {{ display: block; width: 100%; height: auto; border-bottom: 1px solid #ccd6dd; }}
.body {{ padding: 1rem 1.25rem; }}
h1, h2 {{ line-height: 1.2; }}
h2 {{ font-size: 1.1rem; }}
</style>
</head>
<body>
<header>
  <h1>SBOL Visual compatibility disagreements</h1>
  <p>Source report: {html.escape(report_name)}. These are exact labels entailed by saturated paper counts. Record expert decisions in <code>decisions.csv</code>; this artifact does not modify historical data.</p>
</header>
{"".join(cards)}
</body>
</html>
"""
