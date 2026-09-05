"""Judge backend selection for the evaluator CLI and harness."""

from __future__ import annotations

from ..judge import AnthropicAPIJudge, ClaudeCLIJudge
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from ..judge.schema import CompatibilityExemplar
from ..judge.voting import SelfConsistencyJudge

JUDGE_BACKENDS = ("anthropic", "claude-cli")


def build_judge(
    backend: str,
    rules: list[RubricRule],
    model: str | None = None,
    *,
    compatibility_only: bool = False,
    exemplars: tuple[CompatibilityExemplar, ...] = (),
    self_consistency_samples: int = 1,
    era_conditioned: bool = False,
) -> FigureJudge:
    if backend == "anthropic":
        keywords = {"model": model} if model else {}
        judge: FigureJudge = AnthropicAPIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            **keywords,
        )
    elif backend == "claude-cli":
        keywords = {"model": model} if model else {}
        judge = ClaudeCLIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            **keywords,
        )
    else:
        raise ValueError(f"unknown judge backend {backend!r}; expected one of {JUDGE_BACKENDS}")
    if self_consistency_samples == 1:
        return judge
    return SelfConsistencyJudge(judge, samples=self_consistency_samples)
