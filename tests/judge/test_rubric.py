from __future__ import annotations

from pathlib import Path

from sbol_visual_eval.judge.rubric import (
    best_practice_rules,
    compliance_rules,
    load_rubric,
    render_rubric,
)

from .helpers import RULES

RUBRIC_CSV = """\
rule_key,rubric_id,category,section,historical_rule_id,canonical_specification_section,normative_keyword,statement,notes_json,source_url
compliance:5.2.1,historical_2025_rubric_v1,compliance,5.2.1,5.2.1,5.2.1,MUST,The bounding box of a feature glyph MUST be in contact with the backbone,[],https://example.test/rubric
best_practice:5.2.6,historical_2025_rubric_v1,best_practice,5.2.6,5.2.6,5.2.6,SHOULD,A nucleic acid SHOULD use the RECOMMENDED version of the most specific applicable glyph.,"[""Special case: arrow version of CDS is accepted""]",https://example.test/rubric
"""


def test_load_rubric_parses_rules_and_notes(tmp_path: Path) -> None:
    path = tmp_path / "rubric.csv"
    path.write_text(RUBRIC_CSV, encoding="utf-8")
    rules = load_rubric(path)
    assert len(rules) == 2
    assert rules[0].rule_key == "compliance:5.2.1"
    assert rules[0].notes == ()
    assert rules[1].category == "best_practice"
    assert rules[1].notes == ("Special case: arrow version of CDS is accepted",)


def test_category_selectors() -> None:
    assert [rule.rule_key for rule in compliance_rules(RULES)] == [
        "compliance:5.2.1",
        "compliance:5.2.6",
    ]
    assert [rule.rule_key for rule in best_practice_rules(RULES)] == ["best_practice:5.1.2"]


def test_render_rubric_includes_rules_and_exceptions() -> None:
    rendered = render_rubric(RULES)
    assert "COMPLIANCE RULES" in rendered
    assert "BEST-PRACTICE RULES" in rendered
    assert "[compliance:5.2.1] (MUST)" in rendered
    assert "Reviewer exception: Unless showing secondary structure." in rendered


def test_render_rubric_includes_historical_interpretations() -> None:
    rendered = render_rubric(
        RULES, interpretations={"compliance:5.2.1": "Anchored layouts were accepted."}
    )
    assert "Historical interpretation: Anchored layouts were accepted." in rendered


def test_judge_prompt_embeds_calibrated_interpretations() -> None:
    from sbol_visual_eval.judge.prompt import build_user_prompt

    prompt = build_user_prompt(1, "Figure 1. Construct.", RULES)
    assert "Historical interpretation:" in prompt
    assert "incidental construct sketches" in prompt
