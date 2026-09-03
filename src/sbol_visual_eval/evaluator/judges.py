"""Judge backend selection for the evaluator CLI and harness."""

from __future__ import annotations

from ..judge import AnthropicAPIJudge, ClaudeCLIJudge
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule

JUDGE_BACKENDS = ("anthropic", "claude-cli")


def build_judge(backend: str, rules: list[RubricRule], model: str | None = None) -> FigureJudge:
    if backend == "anthropic":
        return AnthropicAPIJudge(rules, model=model) if model else AnthropicAPIJudge(rules)
    if backend == "claude-cli":
        return ClaudeCLIJudge(rules, model=model) if model else ClaudeCLIJudge(rules)
    raise ValueError(f"unknown judge backend {backend!r}; expected one of {JUDGE_BACKENDS}")
