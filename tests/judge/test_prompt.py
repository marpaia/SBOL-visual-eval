from __future__ import annotations

from sbol_visual_eval.judge.prompt import (
    build_compatibility_paper_prompt,
    build_compatibility_prompt,
    build_user_prompt,
)
from sbol_visual_eval.judge.schema import FigureContext

from .helpers import RULES


def test_default_prompt_does_not_add_publication_era_guidance() -> None:
    prompt = build_compatibility_prompt(1, "Figure 1. Construct.", publication_year=2013)

    assert "PUBLICATION ERA" not in prompt
    assert '"borderline":' not in prompt


def test_early_era_prompt_uses_measured_conventional_boundary() -> None:
    prompt = build_compatibility_prompt(
        1,
        "Figure 1. Construct.",
        publication_year=2013,
        era_conditioned=True,
    )

    assert "PUBLICATION ERA: 2013 (2012-2013)" in prompt
    assert "restriction sites, primers, markers" in prompt
    assert "strand/domain interaction" in prompt


def test_middle_era_guidance_is_available_to_full_cascade_prompt() -> None:
    prompt = build_user_prompt(
        1,
        "Figure 1. Construct.",
        RULES,
        publication_year=2015,
        era_conditioned=True,
    )

    assert "PUBLICATION ERA: 2015 (2014-2016)" in prompt
    assert "genome-editing" in prompt
    assert "compliance:5.2.1" in prompt


def test_recent_whole_paper_prompt_rejects_early_cartography_exception() -> None:
    contexts = (
        FigureContext(
            1,
            "Figure 1. Construct.",
            b"png",
            publication_year=2022,
            page_number=2,
        ),
    )

    prompt = build_compatibility_paper_prompt(contexts, era_conditioned=True)

    assert "PUBLICATION ERA: 2022 (2017-2023)" in prompt
    assert "Do not import the earlier panel's exception" in prompt


def test_voting_prompt_requests_an_explicit_borderline_flag() -> None:
    prompt = build_compatibility_prompt(
        1,
        "Figure 1. Construct.",
        request_borderline=True,
    )

    assert 'Set "borderline" to true only' in prompt
    assert '"borderline": true or false,' in prompt
