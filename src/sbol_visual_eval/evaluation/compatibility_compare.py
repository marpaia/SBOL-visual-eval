"""Paired comparison of compatibility benchmark reports."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..corpus.layout import Layout
from ..corpus.util.storage import utc_now, write_json
from .compatibility import _paper_count_summary, _summary


def _boolean(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"invalid boolean {value!r} in {field}")


def _load_report(
    path: Path, *, allow_errors: bool = False
) -> dict[tuple[str, int], dict[str, Any]]:
    rows = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for source_row in csv.DictReader(handle):
            key = (source_row["doi"], int(source_row["figure_number"]))
            if key in rows:
                raise ValueError(f"duplicate compatibility row for {key[0]} Figure {key[1]}")
            if source_row.get("judge_error") and not allow_errors:
                raise ValueError(f"compatibility report contains a judge error for {key}")
            expected = _boolean(source_row["expected_compatible"], field="expected_compatible")
            predicted = (
                _boolean(source_row["predicted_compatible"], field="predicted_compatible")
                if not source_row.get("judge_error")
                else False
            )
            rows[key] = {
                **source_row,
                "year": int(source_row["year"]),
                "figure_number": key[1],
                "expected_compatible": expected,
                "predicted_compatible": predicted,
                "correct": predicted is expected,
                "judge_error": "",
            }
    if not rows:
        raise ValueError(f"compatibility report is empty: {path}")
    return rows


def _paper_exact(rows: list[dict[str, Any]]) -> dict[str, bool]:
    by_doi: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_doi[str(row["doi"])].append(row)
    return {
        doi: sum(row["predicted_compatible"] for row in paper_rows)
        == sum(row["expected_compatible"] for row in paper_rows)
        for doi, paper_rows in by_doi.items()
    }


def compare_compatibility_reports(
    layout: Layout,
    baseline_path: Path,
    candidate_path: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Compare candidate judgments with the same baseline figures and labels."""
    baseline = _load_report(baseline_path, allow_errors=True)
    candidate = _load_report(candidate_path)
    missing = sorted(set(candidate) - set(baseline))
    if missing:
        raise ValueError(f"baseline report lacks {len(missing)} candidate figure(s)")

    candidate_dois = {doi for doi, _ in candidate}
    baseline_scope = {key: row for key, row in baseline.items() if key[0] in candidate_dois}
    if set(baseline_scope) != set(candidate):
        raise ValueError("candidate report does not contain every baseline figure for its papers")
    baseline_errors = [key for key, row in baseline_scope.items() if row["judge_error"]]
    if baseline_errors:
        raise ValueError(f"baseline report contains a judge error for {baseline_errors[0]}")

    keys = sorted(candidate)
    for key in keys:
        baseline_row = baseline[key]
        candidate_row = candidate[key]
        invariants = ("year", "era", "benchmark_partition", "expected_compatible")
        for field in invariants:
            if baseline_row[field] != candidate_row[field]:
                raise ValueError(f"reports disagree on {field} for {key[0]} Figure {key[1]}")

    baseline_rows = [baseline[key] for key in keys]
    candidate_rows = [candidate[key] for key in keys]
    fixed = sum(not baseline[key]["correct"] and candidate[key]["correct"] for key in keys)
    regressed = sum(baseline[key]["correct"] and not candidate[key]["correct"] for key in keys)
    unchanged_incorrect = sum(
        not baseline[key]["correct"] and not candidate[key]["correct"] for key in keys
    )
    baseline_paper_exact = _paper_exact(baseline_rows)
    candidate_paper_exact = _paper_exact(candidate_rows)
    paper_fixed = sum(
        not baseline_paper_exact[doi] and candidate_paper_exact[doi]
        for doi in candidate_paper_exact
    )
    paper_regressed = sum(
        baseline_paper_exact[doi] and not candidate_paper_exact[doi]
        for doi in candidate_paper_exact
    )

    summary = {
        "generated_at": utc_now(),
        "baseline_report": layout.display_path(baseline_path),
        "candidate_report": layout.display_path(candidate_path),
        "figures": len(keys),
        "papers": len(candidate_dois),
        "figure_transitions": {
            "fixed": fixed,
            "regressed": regressed,
            "unchanged_correct": len(keys) - fixed - regressed - unchanged_incorrect,
            "unchanged_incorrect": unchanged_incorrect,
        },
        "paper_exact_transitions": {
            "fixed": paper_fixed,
            "regressed": paper_regressed,
            "unchanged_exact": sum(
                baseline_paper_exact[doi] and candidate_paper_exact[doi]
                for doi in candidate_paper_exact
            ),
            "unchanged_inexact": sum(
                not baseline_paper_exact[doi] and not candidate_paper_exact[doi]
                for doi in candidate_paper_exact
            ),
        },
        "baseline": {
            **_summary(baseline_rows),
            "paper_count_agreement": _paper_count_summary(baseline_rows),
        },
        "candidate": {
            **_summary(candidate_rows),
            "paper_count_agreement": _paper_count_summary(candidate_rows),
        },
    }
    if output_path is None:
        output_path = layout.reports / (
            f"{baseline_path.stem}_vs_{candidate_path.stem}.comparison.json"
        )
    summary["comparison_path"] = layout.display_path(output_path)
    write_json(output_path, summary)
    return summary
