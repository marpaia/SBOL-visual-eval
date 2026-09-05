"""Paired comparison of exact-label cascade benchmark reports."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import utc_now, write_json

STAGES = ("compatible", "compliant", "best_practice")


def _boolean(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"invalid boolean {value!r} in {field}")


def _load_report(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    rows = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for source_row in csv.DictReader(handle):
            key = (source_row["doi"], int(source_row["figure_number"]))
            if key in rows:
                raise ValueError(f"duplicate cascade row for {key[0]} Figure {key[1]}")
            if source_row.get("judge_error"):
                raise ValueError(f"cascade report contains a judge error for {key}")
            row: dict[str, Any] = {
                **source_row,
                "year": int(source_row["year"]),
                "figure_number": key[1],
            }
            for stage in STAGES:
                row[f"expected_{stage}"] = _boolean(
                    source_row[f"expected_{stage}"], field=f"expected_{stage}"
                )
                row[f"predicted_{stage}"] = _boolean(
                    source_row[f"predicted_{stage}"], field=f"predicted_{stage}"
                )
            rows[key] = row
    if not rows:
        raise ValueError(f"cascade report is empty: {path}")
    return rows


def _stage_summary(rows: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    expected = sum(row[f"expected_{stage}"] for row in rows)
    predicted = sum(row[f"predicted_{stage}"] for row in rows)
    correct = sum(row[f"expected_{stage}"] is row[f"predicted_{stage}"] for row in rows)
    return {
        "accuracy": round(correct / len(rows), 4),
        "expected_positive_figures": expected,
        "predicted_positive_figures": predicted,
        "net_count_bias": predicted - expected,
    }


def _paper_exact(rows: list[dict[str, Any]], stage: str) -> dict[str, bool]:
    by_doi: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_doi[str(row["doi"])].append(row)
    return {
        doi: sum(row[f"predicted_{stage}"] for row in paper_rows)
        == sum(row[f"expected_{stage}"] for row in paper_rows)
        for doi, paper_rows in by_doi.items()
    }


def _transitions(
    baseline: dict[tuple[str, int], dict[str, Any]],
    candidate: dict[tuple[str, int], dict[str, Any]],
    stage: str,
) -> dict[str, int]:
    states = []
    for key in baseline:
        expected = baseline[key][f"expected_{stage}"]
        states.append(
            (
                baseline[key][f"predicted_{stage}"] is expected,
                candidate[key][f"predicted_{stage}"] is expected,
            )
        )
    return {
        "fixed": sum(not before and after for before, after in states),
        "regressed": sum(before and not after for before, after in states),
        "unchanged_correct": sum(before and after for before, after in states),
        "unchanged_incorrect": sum(not before and not after for before, after in states),
    }


def _paper_transitions(
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    stage: str,
) -> dict[str, int]:
    baseline = _paper_exact(baseline_rows, stage)
    candidate = _paper_exact(candidate_rows, stage)
    return {
        "fixed": sum(not baseline[doi] and candidate[doi] for doi in baseline),
        "regressed": sum(baseline[doi] and not candidate[doi] for doi in baseline),
        "unchanged_exact": sum(baseline[doi] and candidate[doi] for doi in baseline),
        "unchanged_inexact": sum(not baseline[doi] and not candidate[doi] for doi in baseline),
    }


def compare_cascade_reports(
    layout: Layout,
    baseline_path: Path,
    candidate_path: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Compare two reports over the same figures and entailed stage labels."""
    baseline = _load_report(baseline_path)
    candidate = _load_report(candidate_path)
    if set(baseline) != set(candidate):
        raise ValueError("cascade reports must contain exactly the same figures")

    keys = sorted(baseline)
    for key in keys:
        for field in ("year", *(f"expected_{stage}" for stage in STAGES)):
            if baseline[key][field] != candidate[key][field]:
                raise ValueError(f"reports disagree on {field} for {key[0]} Figure {key[1]}")

    baseline_rows = [baseline[key] for key in keys]
    candidate_rows = [candidate[key] for key in keys]
    summary = {
        "generated_at": utc_now(),
        "baseline_report": layout.display_path(baseline_path),
        "candidate_report": layout.display_path(candidate_path),
        "figures": len(keys),
        "papers": len({doi for doi, _ in keys}),
        "stages": {
            stage: {
                "figure_transitions": _transitions(baseline, candidate, stage),
                "paper_exact_transitions": _paper_transitions(baseline_rows, candidate_rows, stage),
                "baseline": _stage_summary(baseline_rows, stage),
                "candidate": _stage_summary(candidate_rows, stage),
            }
            for stage in STAGES
        },
    }
    if output_path is None:
        output_path = layout.reports / (
            f"{baseline_path.stem}_vs_{candidate_path.stem}.comparison.json"
        )
    summary["comparison_path"] = layout.display_path(output_path)
    write_json(output_path, summary)
    return summary
