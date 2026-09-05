from __future__ import annotations

import csv
import json
from pathlib import Path

from sbol_visual_eval.corpus.layout import Layout
from sbol_visual_eval.evaluation.compatibility import (
    SaturatedFigure,
    assign_era_stratified_partitions,
    compatibility_era,
    run_compatibility_benchmark,
    sample_saturated_figures,
    saturated_compatibility_papers,
    saturated_figures,
)

from ..evaluator.helpers import ScriptedJudge, verdict
from ..figures.helpers import build_fixture_pdf

PAPERS_CSV_HEADER = (
    "record_id,doi,year,title_source,"
    "figures_total,figures_sbol_visual_compatible,"
    "figures_sbol_visual_compliant,figures_best_practices,"
    "historical_count_invariant_valid,requires_adjudication\n"
)

INVENTORY_HEADER = "record_id,doi,year,preferred_pdf_path,preferred_pdf_is_version_of_record\n"


def _prepare_layout(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path)
    layout.processed.mkdir(parents=True)
    layout.reports.mkdir(parents=True)

    (layout.processed / "papers.csv").write_text(
        PAPERS_CSV_HEADER
        # zero compatible: every figure is a certain negative
        + "doi:10.1/neg,10.1/neg,2015,Negative,2,0,0,0,True,False\n"
        # all compatible: every figure is a certain positive
        + "doi:10.1/pos,10.1/pos,2016,Positive,1,1,1,0,True,False\n"
        # mixed: entails no figure-level label
        + "doi:10.1/mix,10.1/mix,2016,Mixed,2,1,1,0,True,False\n"
        # census disagrees with figures_total: untrustworthy labels
        + "doi:10.1/drift,10.1/drift,2017,Drift,5,0,0,0,True,False\n",
        encoding="utf-8",
    )

    for slug, year, pages in (
        (
            "10.1__neg",
            2015,
            [
                [
                    (72, 300, "Figure 1. A gel image.", True),
                    (72, 600, "Figure 2. A bar chart.", True),
                ]
            ],
        ),
        ("10.1__pos", 2016, [[(72, 300, "Figure 1. A genetic circuit.", True)]]),
        (
            "10.1__mix",
            2016,
            [[(72, 300, "Figure 1. Circuit.", True), (72, 600, "Figure 2. Plot.", True)]],
        ),
        ("10.1__drift", 2017, [[(72, 300, "Figure 1. Only one detected.", True)]]),
    ):
        directory = layout.papers / str(year) / slug
        directory.mkdir(parents=True)
        build_fixture_pdf(directory / "paper.pdf", pages)

    (layout.reports / "local_corpus_inventory.csv").write_text(
        INVENTORY_HEADER
        + "doi:10.1/neg,10.1/neg,2015,data/papers/2015/10.1__neg/paper.pdf,True\n"
        + "doi:10.1/pos,10.1/pos,2016,data/papers/2016/10.1__pos/paper.pdf,True\n"
        + "doi:10.1/mix,10.1/mix,2016,data/papers/2016/10.1__mix/paper.pdf,True\n"
        + "doi:10.1/drift,10.1/drift,2017,data/papers/2017/10.1__drift/paper.pdf,True\n",
        encoding="utf-8",
    )
    return layout


def test_saturated_papers_exclude_mixed_stages(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    papers = saturated_compatibility_papers(layout)
    assert sorted(entry.paper.doi for entry in papers) == ["10.1/drift", "10.1/neg", "10.1/pos"]

    positives = saturated_compatibility_papers(layout, expected=True)
    assert [entry.paper.doi for entry in positives] == ["10.1/pos"]

    negatives = saturated_compatibility_papers(layout, expected=False)
    assert sorted(entry.paper.doi for entry in negatives) == ["10.1/drift", "10.1/neg"]


def test_saturated_figures_label_every_figure_and_skip_census_drift(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))

    assert {(figure.doi, figure.figure_number) for figure in figures} == {
        ("10.1/neg", 1),
        ("10.1/neg", 2),
        ("10.1/pos", 1),
    }
    assert all(not figure.expected_compatible for figure in figures if figure.doi == "10.1/neg")
    assert all(figure.expected_compatible for figure in figures if figure.doi == "10.1/pos")


def test_saturated_figures_reuse_the_local_pool_cache(tmp_path: Path, monkeypatch) -> None:
    layout = _prepare_layout(tmp_path)
    papers = saturated_compatibility_papers(layout)
    expected = saturated_figures(layout, papers)

    monkeypatch.setattr(
        "sbol_visual_eval.evaluation.compatibility.census_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("census should be cached")),
    )

    assert saturated_figures(layout, papers) == expected
    assert len(list((layout.cache / "evaluation" / "compatibility").glob("*.json.gz"))) == 1


def test_era_stratified_partitions_keep_whole_papers_together() -> None:
    figures = [
        SaturatedFigure(
            doi=f"10.1/{era}-{label}-{paper}",
            year=year,
            pdf_path="paper.pdf",
            figure_number=figure_number,
            caption_text="Figure.",
            page_number=1,
            expected_compatible=label,
        )
        for era, year in (("early", 2012), ("middle", 2015), ("recent", 2020))
        for label in (False, True)
        for paper in range(4)
        for figure_number in (1, 2)
    ]

    partitioned = assign_era_stratified_partitions(figures, holdout_fraction=0.25, seed=42)

    partitions_by_doi: dict[str, set[str]] = {}
    for figure in partitioned:
        partitions_by_doi.setdefault(figure.doi, set()).add(figure.benchmark_partition)
    assert all(len(partitions) == 1 for partitions in partitions_by_doi.values())
    for era in ("2012-2013", "2014-2016", "2017-2023"):
        for label in (False, True):
            assert {
                figure.benchmark_partition
                for figure in partitioned
                if compatibility_era(figure.year) == era and figure.expected_compatible is label
            } == {"calibration", "holdout"}


def test_era_stratified_partitions_validate_fraction() -> None:
    import pytest

    with pytest.raises(ValueError, match="holdout_fraction"):
        assign_era_stratified_partitions([], holdout_fraction=1.0)


def test_sampling_keeps_partitioned_papers_intact() -> None:
    figures = [
        SaturatedFigure(
            doi=f"10.1/{paper}",
            year=2015,
            pdf_path="paper.pdf",
            figure_number=figure_number,
            caption_text="Figure.",
            page_number=1,
            expected_compatible=False,
            benchmark_partition="holdout",
        )
        for paper in range(5)
        for figure_number in (1, 2)
    ]

    sampled = sample_saturated_figures(figures, 2, seed=17)

    assert len({figure.doi for figure in sampled}) == 2
    assert len(sampled) == 4
    assert {figure.benchmark_partition for figure in sampled} == {"holdout"}


def test_sampling_balances_era_and_label_strata() -> None:
    figures = [
        SaturatedFigure(
            doi=f"10.1/{year}-{int(label)}-{paper}",
            year=year,
            pdf_path="paper.pdf",
            figure_number=1,
            caption_text="Figure.",
            page_number=1,
            expected_compatible=label,
            benchmark_partition="calibration",
        )
        for year in (2012, 2015, 2020)
        for label in (False, True)
        for paper in range(3)
    ]

    sampled = sample_saturated_figures(figures, 6, seed=23)

    assert {(compatibility_era(figure.year), figure.expected_compatible) for figure in sampled} == {
        ("2012-2013", False),
        ("2012-2013", True),
        ("2014-2016", False),
        ("2014-2016", True),
        ("2017-2023", False),
        ("2017-2023", True),
    }


def test_compatibility_benchmark_reports_boundary_errors(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    # Judge marks everything compatible: perfect recall, worst false-positive rate.
    judge = ScriptedJudge({1: verdict(1, compatible=True), 2: verdict(2, compatible=True)})

    summary = run_compatibility_benchmark(layout, judge, figures, workers=1)

    assert summary["figures"] == 3
    assert summary["judged"] == 3
    assert summary["certain_positive_figures"] == 1
    assert summary["certain_negative_figures"] == 2
    assert summary["recall"] == 1.0
    assert summary["false_positive_rate"] == 1.0
    assert summary["accuracy"] == round(1 / 3, 4)
    assert set(summary["by_era"]) == {"2014-2016"}
    assert set(summary["by_year"]) == {"2015", "2016"}

    with (layout.reports / "compatibility_benchmark.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert {row["correct"] for row in rows} == {"True", "False"}

    payload = json.loads((layout.reports / "compatibility_benchmark.json").read_text())
    assert payload["judge_errors"] == 0
    assert payload["paper_count_agreement"] == {
        "papers": 2,
        "fully_judged_papers": 2,
        "exact": 1,
        "exact_rate": 0.5,
        "within_one": 1,
        "within_one_rate": 0.5,
        "mae": 1.0,
        "expected_total": 1,
        "predicted_total": 3,
        "net_count_bias": 2,
    }
    assert set(payload["paper_count_agreement_by_era"]) == {"2014-2016"}
    assert set(payload["paper_count_agreement_by_partition"]) == {"unassigned"}


def test_net_count_bias_projects_onto_corpus_mix(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    # Everything called compatible: both negatives are false positives.
    judge = ScriptedJudge({1: verdict(1, compatible=True), 2: verdict(2, compatible=True)})

    summary = run_compatibility_benchmark(layout, judge, figures, workers=1)

    assert summary["expected_positive_figures"] == 1
    assert summary["predicted_positive_figures"] == 3
    assert summary["net_count_bias"] == 2
    # A pure over-caller biases counts upward on the corpus mix.
    assert summary["net_count_bias_per_100_corpus_figures"] > 0


def test_net_count_bias_is_negative_when_over_rejecting(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    # Everything called incompatible: the certain positive is a false negative.
    judge = ScriptedJudge({1: verdict(1, compatible=False), 2: verdict(2, compatible=False)})

    summary = run_compatibility_benchmark(layout, judge, figures, workers=1)

    assert summary["net_count_bias"] == -1
    assert summary["net_count_bias_per_100_corpus_figures"] < 0


def test_compatibility_benchmark_resumes_successful_checkpoint_rows(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    checkpoint_path = layout.reports / "resumed.checkpoint.jsonl"
    checkpoint_path.write_text(
        json.dumps(
            {
                "doi": "10.1/neg",
                "year": 2015,
                "era": "2014-2016",
                "benchmark_partition": "unassigned",
                "figure_number": 2,
                "expected_compatible": False,
                "predicted_compatible": False,
                "correct": True,
                "rationale": "checkpointed",
                "judge_error": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    judge = ScriptedJudge({1: verdict(1, compatible=False)})

    summary = run_compatibility_benchmark(
        layout,
        judge,
        figures,
        workers=1,
        report_stem="resumed",
        resume=True,
    )

    assert summary["resumed_figures"] == 1
    assert len(judge.contexts) == 2
    assert not checkpoint_path.exists()


def test_compatibility_benchmark_preserves_errors_and_resumes_from_report(
    tmp_path: Path,
) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))

    class OneFailureJudge:
        def judge(self, context):
            if context.figure_number == 2:
                raise RuntimeError("temporary failure")
            return verdict(context.figure_number, compatible=False)

    first = run_compatibility_benchmark(
        layout,
        OneFailureJudge(),
        figures,
        workers=1,
        report_stem="report_resume",
    )

    checkpoint_path = layout.reports / "report_resume.checkpoint.jsonl"
    assert first["judge_errors"] == 1
    assert checkpoint_path.exists()

    # A completed CSV remains a recovery source when its checkpoint is unavailable.
    checkpoint_path.unlink()
    judge = ScriptedJudge({2: verdict(2, compatible=False)})
    second = run_compatibility_benchmark(
        layout,
        judge,
        figures,
        workers=1,
        report_stem="report_resume",
        resume=True,
    )

    assert second["resumed_figures"] == 2
    assert second["judge_errors"] == 0
    assert len(judge.contexts) == 1
    assert not checkpoint_path.exists()


def test_compatibility_benchmark_does_not_resume_a_different_prompt_profile(
    tmp_path: Path,
) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))
    checkpoint_path = layout.reports / "profile_changed.checkpoint.jsonl"
    checkpoint_path.write_text(
        json.dumps(
            {
                "doi": "10.1/neg",
                "year": 2015,
                "era": "2014-2016",
                "benchmark_partition": "unassigned",
                "prompt_profile": "earlier_profile",
                "figure_number": 2,
                "expected_compatible": False,
                "predicted_compatible": False,
                "correct": True,
                "rationale": "checkpointed",
                "judge_error": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    judge = ScriptedJudge({1: verdict(1, compatible=False), 2: verdict(2, compatible=False)})

    summary = run_compatibility_benchmark(
        layout,
        judge,
        figures,
        workers=1,
        report_stem="profile_changed",
        resume=True,
        prompt_profile="new_profile",
    )

    assert summary["resumed_figures"] == 0
    assert summary["prompt_profile"] == "new_profile"
    assert len(judge.contexts) == 3


def test_compatibility_benchmark_can_schedule_one_call_per_paper(tmp_path: Path) -> None:
    layout = _prepare_layout(tmp_path)
    figures = saturated_figures(layout, saturated_compatibility_papers(layout))

    class RecordingPaperJudge:
        def __init__(self) -> None:
            self.calls = []

        def judge(self, context):
            raise AssertionError("per-figure judging should not be used")

        def judge_paper(self, contexts):
            self.calls.append(contexts)
            return tuple(verdict(context.figure_number, compatible=False) for context in contexts)

    judge = RecordingPaperJudge()

    summary = run_compatibility_benchmark(
        layout,
        judge,
        figures,
        workers=2,
        report_stem="whole_paper",
        whole_paper=True,
    )

    assert summary["judging_mode"] == "whole_paper"
    assert len(judge.calls) == 2
    assert sorted(len(call) for call in judge.calls) == [1, 2]
