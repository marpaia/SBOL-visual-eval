"""Shared rubric fixtures for judge tests."""

from __future__ import annotations

from sbol_visual_eval.judge.rubric import RubricRule

RULES = [
    RubricRule(
        rule_key="compliance:5.2.1",
        category="compliance",
        section="5.2.1",
        normative_keyword="MUST",
        statement="The bounding box of a feature glyph MUST be in contact with the backbone",
        notes=(),
    ),
    RubricRule(
        rule_key="compliance:5.2.6",
        category="compliance",
        section="5.2.6",
        normative_keyword="MUST",
        statement="The glyph used for a feature MUST contain the role of the feature.",
        notes=(),
    ),
    RubricRule(
        rule_key="best_practice:5.1.2",
        category="best_practice",
        section="5.1.2",
        normative_keyword="SHOULD",
        statement="Nucleic acid backbones SHOULD be horizontal.",
        notes=("Unless showing secondary structure.",),
    ),
]
