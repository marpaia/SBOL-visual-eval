from __future__ import annotations

import csv
from pathlib import Path

from sbol_visual_eval.evaluation.adjudication import build_compatibility_adjudication
from sbol_visual_eval.evaluation.compatibility import (
    run_compatibility_benchmark,
    saturated_compatibility_papers,
    saturated_figures,
)

from ..evaluator.helpers import ScriptedJudge, verdict
from .test_compatibility import _prepare_layout


def test_adjudication_builds_new_layer_for_every_exact_disagreement(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    historical_before = (layout.processed / "papers.csv").read_bytes()
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    judge = ScriptedJudge({1: verdict(1, compatible=True), 2: verdict(2, compatible=True)})
    run_compatibility_benchmark(layout, judge, figures, workers=1, report_stem="source")

    summary = build_compatibility_adjudication(
        layout,
        layout.reports / "source.csv",
    )

    target = layout.data / "adjudicated" / "source"
    assert summary["disagreements"] == 2
    assert summary["historical_layer_modified"] is False
    assert len(list((target / "images").glob("*.png"))) == 2
    assert "scripted" in (target / "index.html").read_text(encoding="utf-8")
    with (target / "decisions.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["historical_compatible"] for row in rows} == {"False"}
    assert {row["evaluator_compatible"] for row in rows} == {"True"}
    assert {row["adjudicated_compatible"] for row in rows} == {""}
    assert (layout.processed / "papers.csv").read_bytes() == historical_before


def test_adjudication_regeneration_preserves_expert_fields(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    judge = ScriptedJudge({1: verdict(1, compatible=True), 2: verdict(2, compatible=True)})
    run_compatibility_benchmark(layout, judge, figures, workers=1, report_stem="source")
    build_compatibility_adjudication(layout, layout.reports / "source.csv")
    decisions_path = layout.data / "adjudicated" / "source" / "decisions.csv"
    with decisions_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["adjudicated_compatible"] = "False"
    rows[0]["adjudication_rationale"] = "Expert reviewed."
    with decisions_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

    build_compatibility_adjudication(layout, layout.reports / "source.csv")

    with decisions_path.open(newline="", encoding="utf-8") as handle:
        regenerated = list(csv.DictReader(handle))
    assert regenerated[0]["adjudicated_compatible"] == "False"
    assert regenerated[0]["adjudication_rationale"] == "Expert reviewed."
