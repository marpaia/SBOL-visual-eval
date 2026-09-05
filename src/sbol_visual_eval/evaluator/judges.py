"""Judge backend selection for the evaluator CLI and harness."""

from __future__ import annotations

from ..judge import AnthropicAPIJudge, ClaudeCLIJudge
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from ..judge.schema import CompatibilityExemplar

JUDGE_BACKENDS = ("anthropic", "claude-cli")


def build_judge(
    backend: str,
    rules: list[RubricRule],
    model: str | None = None,
    *,
    compatibility_only: bool = False,
    exemplars: tuple[CompatibilityExemplar, ...] = (),
) -> FigureJudge:
    if backend == "anthropic":
        keywords = {"model": model} if model else {}
        return AnthropicAPIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            **keywords,
        )
    if backend == "claude-cli":
        keywords = {"model": model} if model else {}
        return ClaudeCLIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            **keywords,
        )
    raise ValueError(f"unknown judge backend {backend!r}; expected one of {JUDGE_BACKENDS}")
