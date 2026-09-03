from __future__ import annotations

from pathlib import Path

from sbol_visual_eval.evaluation.groundtruth import (
    StageLabels,
    ground_truth_by_doi,
    load_ground_truth,
    saturated_labels,
)
from sbol_visual_eval.evaluation.schema import PaperScore

PAPERS_CSV_HEADER = (
    "record_id,doi,year,title_source,"
    "figures_total,figures_sbol_visual_compatible,"
    "figures_sbol_visual_compliant,figures_best_practices,"
    "historical_count_invariant_valid,requires_adjudication\n"
)


def _write_papers_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(PAPERS_CSV_HEADER + "".join(row + "\n" for row in rows), encoding="utf-8")
    return path


def test_load_ground_truth_parses_counts_and_flags(tmp_path: Path) -> None:
    path = _write_papers_csv(
        tmp_path / "papers.csv",
        [
            "doi:10.1/a,10.1/a,2015,Paper A,4,2,1,0,True,False",
            "doi:10.1/b,10.1/b,2020,Paper B,2,3,3,3,False,True",
        ],
    )
    papers = load_ground_truth(path)
    assert len(papers) == 2

    first = papers[0]
    assert first.doi == "10.1/a"
    assert first.year == 2015
    assert first.score == PaperScore(4, 2, 1, 0)
    assert first.scoreable

    anomalous = papers[1]
    assert not anomalous.historical_count_invariant_valid
    assert anomalous.requires_adjudication
    assert not anomalous.scoreable

    assert ground_truth_by_doi(papers)["10.1/a"] is first


def test_saturated_labels_zero_compatible_labels_every_figure() -> None:
    labels = saturated_labels(PaperScore(5, 0, 0, 0))
    assert labels.compatible is StageLabels.ALL_FALSE
    assert labels.compliant is StageLabels.EMPTY
    assert labels.best_practice is StageLabels.EMPTY


def test_saturated_labels_full_cascade() -> None:
    labels = saturated_labels(PaperScore(3, 3, 3, 3))
    assert labels.compatible is StageLabels.ALL_TRUE
    assert labels.compliant is StageLabels.ALL_TRUE
    assert labels.best_practice is StageLabels.ALL_TRUE


def test_saturated_labels_mixed_stages() -> None:
    labels = saturated_labels(PaperScore(6, 3, 3, 1))
    assert labels.compatible is StageLabels.MIXED
    assert labels.compliant is StageLabels.ALL_TRUE
    assert labels.best_practice is StageLabels.MIXED


def test_saturated_labels_zero_compliant_over_compatible() -> None:
    labels = saturated_labels(PaperScore(4, 2, 0, 0))
    assert labels.compatible is StageLabels.MIXED
    assert labels.compliant is StageLabels.ALL_FALSE
    assert labels.best_practice is StageLabels.EMPTY


def test_saturated_labels_paper_without_figures() -> None:
    labels = saturated_labels(PaperScore(0, 0, 0, 0))
    assert labels.compatible is StageLabels.EMPTY
