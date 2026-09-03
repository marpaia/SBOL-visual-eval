"""License allowlisting for bridge-acquired manuscripts."""

from __future__ import annotations

from sbol_visual_eval.corpus.acquisition.pmc_web import (
    eligibility as pmc_web_eligibility,
)


def test_license_allowlist_rejects_lookalike_codes() -> None:
    assert pmc_web_eligibility._explicit_license({"license_code": "CC BY-NC-ND"}) == "CC BY-NC-ND"
    assert pmc_web_eligibility._explicit_license({"license_code": "CC BYGARBAGE"}) is None
    assert pmc_web_eligibility._explicit_license({"license_code": "TDM"}) is None
