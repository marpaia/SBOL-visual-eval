"""Judge backend selection for the evaluator CLI and harness."""

from __future__ import annotations

from ..judge import AnthropicAPIJudge, ClaudeCLIJudge, CodexCLIJudge
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from ..judge.schema import CompatibilityExemplar
from ..judge.voting import SelfConsistencyJudge

JUDGE_BACKENDS = ("anthropic", "claude-cli", "codex-cli")


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
            request_borderline=self_consistency_samples > 1,
            **keywords,
        )
    elif backend == "claude-cli":
        keywords = {"model": model} if model else {}
        judge = ClaudeCLIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=self_consistency_samples > 1,
            **keywords,
        )
    elif backend == "codex-cli":
        keywords = {"model": model} if model else {}
        judge = CodexCLIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=self_consistency_samples > 1,
            **keywords,
        )
    else:
        raise ValueError(f"unknown judge backend {backend!r}; expected one of {JUDGE_BACKENDS}")
    if self_consistency_samples == 1:
        return judge
    return SelfConsistencyJudge(judge, samples=self_consistency_samples)
