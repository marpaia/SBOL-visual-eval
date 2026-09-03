from __future__ import annotations

from sbol_visual_eval.evaluation.schema import PaperScore


def test_derived_flags_match_corpus_semantics() -> None:
    score = PaperScore(
        figures_total=4,
        figures_sbol_visual_compatible=2,
        figures_sbol_visual_compliant=2,
        figures_best_practices=1,
    )
    assert score.has_compatible_figures
    assert score.has_compliant_figures
    assert score.all_compatible_figures_compliant
    assert score.has_best_practice_figures
    assert not score.all_compatible_figures_best_practice


def test_zero_compatible_never_sets_all_compatible_flags() -> None:
    score = PaperScore(
        figures_total=3,
        figures_sbol_visual_compatible=0,
        figures_sbol_visual_compliant=0,
        figures_best_practices=0,
    )
    assert not score.all_compatible_figures_compliant
    assert not score.all_compatible_figures_best_practice


def test_all_compatible_best_practice_requires_full_cascade() -> None:
    score = PaperScore(
        figures_total=5,
        figures_sbol_visual_compatible=3,
        figures_sbol_visual_compliant=3,
        figures_best_practices=3,
    )
    assert score.all_compatible_figures_best_practice


def test_count_invariant_violations() -> None:
    valid = PaperScore(
        figures_total=4,
        figures_sbol_visual_compatible=3,
        figures_sbol_visual_compliant=2,
        figures_best_practices=1,
    )
    assert valid.count_invariant_violations() == []

    invalid = PaperScore(
        figures_total=2,
        figures_sbol_visual_compatible=3,
        figures_sbol_visual_compliant=1,
        figures_best_practices=2,
    )
    violations = invalid.count_invariant_violations()
    assert "figures_sbol_visual_compatible exceeds figures_total" in violations
    assert "figures_best_practices exceeds figures_sbol_visual_compliant" in violations


def test_to_dict_uses_historical_vocabulary() -> None:
    score = PaperScore(
        figures_total=4,
        figures_sbol_visual_compatible=1,
        figures_sbol_visual_compliant=1,
        figures_best_practices=0,
    )
    row = score.to_dict()
    assert row["figures_total"] == 4
    assert row["figures_sbol_visual_compatible"] == 1
    assert row["has_compatible_figures"] is True
    assert row["all_compatible_figures_compliant"] is True
    assert row["all_compatible_figures_best_practice"] is False
