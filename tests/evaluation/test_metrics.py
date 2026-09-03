from __future__ import annotations

from sbol_visual_eval.evaluation.groundtruth import GroundTruthPaper
from sbol_visual_eval.evaluation.metrics import score_agreement
from sbol_visual_eval.evaluation.schema import PaperScore


def _paper(
    doi: str,
    year: int,
    score: PaperScore,
    *,
    invariant_valid: bool = True,
    requires_adjudication: bool = False,
) -> GroundTruthPaper:
    return GroundTruthPaper(
        record_id=f"doi:{doi}",
        doi=doi,
        year=year,
        title=doi,
        score=score,
        historical_count_invariant_valid=invariant_valid,
        requires_adjudication=requires_adjudication,
    )


def test_score_agreement_counts_and_flags() -> None:
    pairs = [
        (_paper("10.1/a", 2015, PaperScore(4, 2, 2, 1)), PaperScore(4, 2, 2, 1)),
        (_paper("10.1/b", 2015, PaperScore(3, 1, 0, 0)), PaperScore(3, 2, 1, 0)),
        (_paper("10.1/c", 2016, PaperScore(2, 0, 0, 0)), PaperScore(5, 0, 0, 0)),
    ]
    report = score_agreement(pairs)

    assert report.papers == 3
    assert report.all_counts_exact == 1

    total = report.counts["figures_total"]
    assert total.exact == 2
    assert total.within_one == 2
    assert total.absolute_error_sum == 3

    compatible = report.counts["figures_sbol_visual_compatible"]
    assert compatible.exact == 2
    assert compatible.within_one == 3
    assert compatible.historical_total == 3
    assert compatible.predicted_total == 4

    has_compliant = report.flags["has_compliant_figures"]
    assert has_compliant.true_positives == 1
    assert has_compliant.false_positives == 1
    assert has_compliant.true_negatives == 1
    assert has_compliant.precision == 0.5
    assert has_compliant.recall == 1.0


def test_score_agreement_skips_unscoreable_papers() -> None:
    pairs = [
        (_paper("10.1/a", 2020, PaperScore(4, 2, 2, 2)), PaperScore(4, 2, 2, 2)),
        (
            _paper("10.1/b", 2020, PaperScore(2, 3, 3, 3), invariant_valid=False),
            PaperScore(2, 2, 2, 2),
        ),
        (
            _paper("10.1/c", 2020, PaperScore(1, 1, 1, 1), requires_adjudication=True),
            PaperScore(1, 1, 1, 1),
        ),
    ]
    report = score_agreement(pairs)
    assert report.papers == 1
    assert report.all_counts_exact == 1


def test_score_agreement_yearly_totals() -> None:
    pairs = [
        (_paper("10.1/a", 2015, PaperScore(4, 2, 2, 1)), PaperScore(3, 2, 1, 1)),
        (_paper("10.1/b", 2015, PaperScore(2, 1, 1, 0)), PaperScore(2, 1, 1, 0)),
        (_paper("10.1/c", 2016, PaperScore(1, 0, 0, 0)), PaperScore(1, 1, 0, 0)),
    ]
    report = score_agreement(pairs)
    totals_2015 = report.yearly_totals[2015]
    assert totals_2015["figures_total"] == {"historical": 6, "predicted": 5}
    assert totals_2015["figures_sbol_visual_compliant"] == {"historical": 3, "predicted": 2}
    assert report.yearly_totals[2016]["figures_sbol_visual_compatible"] == {
        "historical": 0,
        "predicted": 1,
    }

    payload = report.to_dict()
    assert payload["yearly_totals"]["2015"]["figures_total"]["historical"] == 6
    assert payload["counts"]["figures_total"]["exact_rate"] == round(2 / 3, 4)
