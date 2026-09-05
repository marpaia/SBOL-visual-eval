from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.compatibility import COMPATIBILITY_FIELDS
from sbol_visual_eval.evaluation.compatibility_compare import compare_compatibility_reports


def _write_report(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPATIBILITY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "year": 2015,
                    "era": "2014-2016",
                    "benchmark_partition": "holdout",
                    "prompt_profile": "test",
                    "judging_mode": "per_figure",
                    "self_consistency_samples": 1,
                    "rationale": "test",
                    "judge_error": "",
                    **row,
                }
            )


def _row(doi: str, figure: int, expected: bool, predicted: bool) -> dict[str, object]:
    return {
        "doi": doi,
        "figure_number": figure,
        "expected_compatible": expected,
        "predicted_compatible": predicted,
        "correct": expected is predicted,
    }


def test_compare_reports_pairs_figures_and_paper_counts(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.csv"
    candidate_path = tmp_path / "candidate.csv"
    output_path = tmp_path / "comparison.json"
    _write_report(
        baseline_path,
        [
            _row("10.1/a", 1, False, True),
            _row("10.1/a", 2, False, False),
            _row("10.1/b", 1, True, True),
        ],
    )
    _write_report(
        candidate_path,
        [
            _row("10.1/a", 1, False, False),
            _row("10.1/a", 2, False, False),
            _row("10.1/b", 1, True, False),
        ],
    )

    summary = compare_compatibility_reports(
        Layout(tmp_path), baseline_path, candidate_path, output_path=output_path
    )

    assert summary["figure_transitions"] == {
        "fixed": 1,
        "regressed": 1,
        "unchanged_correct": 1,
        "unchanged_incorrect": 0,
    }
    assert summary["paper_exact_transitions"]["fixed"] == 1
    assert summary["paper_exact_transitions"]["regressed"] == 1
    assert summary["baseline"]["paper_count_agreement"]["exact"] == 1
    assert summary["candidate"]["paper_count_agreement"]["exact"] == 1
    assert json.loads(output_path.read_text(encoding="utf-8"))["figures"] == 3


def test_compare_reports_rejects_partial_papers(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.csv"
    candidate_path = tmp_path / "candidate.csv"
    _write_report(
        baseline_path,
        [_row("10.1/a", 1, False, False), _row("10.1/a", 2, False, False)],
    )
    _write_report(candidate_path, [_row("10.1/a", 1, False, False)])

    with pytest.raises(ValueError, match="every baseline figure"):
        compare_compatibility_reports(Layout(tmp_path), baseline_path, candidate_path)


def test_compare_reports_ignores_baseline_errors_outside_candidate_papers(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.csv"
    candidate_path = tmp_path / "candidate.csv"
    _write_report(
        baseline_path,
        [
            _row("10.1/a", 1, False, False),
            {**_row("10.1/b", 1, False, False), "judge_error": "provider refused"},
        ],
    )
    _write_report(candidate_path, [_row("10.1/a", 1, False, False)])

    summary = compare_compatibility_reports(Layout(tmp_path), baseline_path, candidate_path)

    assert summary["figures"] == 1
    assert summary["papers"] == 1
