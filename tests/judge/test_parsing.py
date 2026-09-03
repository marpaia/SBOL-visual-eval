from __future__ import annotations

import pytest

from sbol_visual_eval.judge.parsing import JudgeParseError, parse_verdict
from sbol_visual_eval.judge.schema import RuleVerdict

CLEAN_REPLY = """\
{"figure_number": 2, "compatible": true, "rationale": "Genetic circuit diagram.",
 "findings": [
   {"rule_key": "compliance:5.2.1", "verdict": "pass", "evidence": "glyphs touch backbone"},
   {"rule_key": "best_practice:5.1.2", "verdict": "fail", "evidence": "vertical backbone"}
 ]}"""


def test_parse_clean_json_reply() -> None:
    verdict = parse_verdict(CLEAN_REPLY, figure_number=2)
    assert verdict.figure_number == 2
    assert verdict.compatible
    assert verdict.rationale == "Genetic circuit diagram."
    assert verdict.findings[0].verdict is RuleVerdict.PASS
    assert verdict.findings[1].verdict is RuleVerdict.FAIL


def test_parse_reply_with_surrounding_prose_and_fences() -> None:
    reply = f"Here is my assessment:\n```json\n{CLEAN_REPLY}\n```\nDone."
    verdict = parse_verdict(reply, figure_number=2)
    assert verdict.compatible
    assert len(verdict.findings) == 2


def test_parse_incompatible_reply_without_findings() -> None:
    reply = '{"figure_number": 1, "compatible": false, "rationale": "Data plot only."}'
    verdict = parse_verdict(reply, figure_number=1)
    assert not verdict.compatible
    assert verdict.findings == ()


def test_parse_rejects_reply_without_json() -> None:
    with pytest.raises(JudgeParseError):
        parse_verdict("I could not evaluate this figure.", figure_number=1)


def test_parse_rejects_missing_compatible_field() -> None:
    with pytest.raises(JudgeParseError):
        parse_verdict('{"figure_number": 1}', figure_number=1)


def test_parse_rejects_unknown_verdict_value() -> None:
    reply = (
        '{"compatible": true, "findings": [{"rule_key": "compliance:5.2.1", "verdict": "maybe"}]}'
    )
    with pytest.raises(JudgeParseError):
        parse_verdict(reply, figure_number=1)


def test_parse_skips_leading_non_verdict_braces() -> None:
    reply = "{not json} " + CLEAN_REPLY
    verdict = parse_verdict(reply, figure_number=2)
    assert verdict.compatible
