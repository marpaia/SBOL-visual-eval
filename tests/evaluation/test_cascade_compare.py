from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.cascade_bench import CASCADE_FIELDS
from sbol_visual_eval.evaluation.cascade_compare import compare_cascade_reports


def _row(
    doi: str,
    figure: int,
    expected: tuple[bool, bool, bool],
    predicted: tuple[bool, bool, bool],
) -> dict[str, object]:
    stages = ("compatible", "compliant", "best_practice")
    return {
        "doi": doi,
        "year": 2017,
        "prompt_profile": "test",
        "judging_mode": "per_figure",
        "self_consistency_samples": 1,
        "figure_number": figure,
        **{f"expected_{stage}": value for stage, value in zip(stages, expected, strict=True)},
        **{f"predicted_{stage}": value for stage, value in zip(stages, predicted, strict=True)},
        "judge_error": "",
    }


def _write_report(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CASCADE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_compare_cascade_reports_counts_stage_transitions(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.csv"
    candidate_path = tmp_path / "candidate.csv"
    output_path = tmp_path / "comparison.json"
    _write_report(
        baseline_path,
        [
            _row("10.1/a", 1, (True, True, True), (True, True, False)),
            _row("10.1/a", 2, (True, True, True), (True, False, False)),
            _row("10.1/b", 1, (True, True, False), (True, True, False)),
        ],
    )
    _write_report(
        candidate_path,
        [
            _row("10.1/a", 1, (True, True, True), (True, True, True)),
            _row("10.1/a", 2, (True, True, True), (True, True, False)),
            _row("10.1/b", 1, (True, True, False), (False, False, False)),
        ],
    )

    summary = compare_cascade_reports(
        Layout(tmp_path), baseline_path, candidate_path, output_path=output_path
    )

    assert summary["stages"]["compatible"]["figure_transitions"]["regressed"] == 1
    assert summary["stages"]["compliant"]["figure_transitions"] == {
        "fixed": 1,
        "regressed": 1,
        "unchanged_correct": 1,
        "unchanged_incorrect": 0,
    }
    assert summary["stages"]["best_practice"]["figure_transitions"]["fixed"] == 1
    assert summary["stages"]["best_practice"]["candidate"]["net_count_bias"] == -1
    assert json.loads(output_path.read_text(encoding="utf-8"))["figures"] == 3


def test_compare_cascade_reports_requires_the_same_figures(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.csv"
    candidate_path = tmp_path / "candidate.csv"
    _write_report(baseline_path, [_row("10.1/a", 1, (True, True, True), (True, True, True))])
    _write_report(candidate_path, [_row("10.1/b", 1, (True, True, True), (True, True, True))])

    with pytest.raises(ValueError, match="exactly the same figures"):
        compare_cascade_reports(Layout(tmp_path), baseline_path, candidate_path)
