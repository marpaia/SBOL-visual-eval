"""Judge backend selection for the evaluator CLI and harness."""

from __future__ import annotations

from ..judge import AnthropicAPIJudge, ClaudeCLIJudge, CodexCLIJudge, StagedCascadeJudge
from ..judge.protocol import FigureJudge
from ..judge.rubric import RubricRule
from ..judge.schema import CompatibilityExemplar
from ..judge.voting import SelfConsistencyJudge

JUDGE_BACKENDS = ("anthropic", "claude-cli", "codex-cli")
DEFAULT_JUDGE_BACKEND = "claude-cli"


def _build_backend(
    backend: str,
    rules: list[RubricRule],
    model: str | None,
    *,
    compatibility_only: bool,
    exemplars: tuple[CompatibilityExemplar, ...],
    era_conditioned: bool,
    request_borderline: bool,
    assume_compatible: bool = False,
) -> FigureJudge:
    if backend == "anthropic":
        keywords = {"model": model} if model else {}
        judge: FigureJudge = AnthropicAPIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=request_borderline,
            assume_compatible=assume_compatible,
            **keywords,
        )
    elif backend == "claude-cli":
        keywords = {"model": model} if model else {}
        judge = ClaudeCLIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=request_borderline,
            assume_compatible=assume_compatible,
            **keywords,
        )
    elif backend == "codex-cli":
        keywords = {"model": model} if model else {}
        judge = CodexCLIJudge(
            rules,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=request_borderline,
            assume_compatible=assume_compatible,
            **keywords,
        )
    else:
        raise ValueError(f"unknown judge backend {backend!r}; expected one of {JUDGE_BACKENDS}")
    return judge


def build_judge(
    backend: str,
    rules: list[RubricRule],
    model: str | None = None,
    *,
    compatibility_only: bool = False,
    exemplars: tuple[CompatibilityExemplar, ...] = (),
    self_consistency_samples: int = 1,
    era_conditioned: bool = False,
    staged: bool = False,
) -> FigureJudge:
    request_borderline = self_consistency_samples > 1
    if staged and compatibility_only:
        raise ValueError("staged judging already owns the compatibility stage")
    if staged:
        compatibility_exemplars = tuple(
            exemplar for exemplar in exemplars if exemplar.expected_compliant is None
        )
        compatibility_judge = _build_backend(
            backend,
            rules,
            model,
            compatibility_only=True,
            exemplars=compatibility_exemplars,
            era_conditioned=era_conditioned,
            request_borderline=request_borderline,
        )
        downstream_judge = _build_backend(
            backend,
            rules,
            model,
            compatibility_only=False,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=request_borderline,
            assume_compatible=True,
        )
        judge: FigureJudge = StagedCascadeJudge(compatibility_judge, downstream_judge)
    else:
        judge = _build_backend(
            backend,
            rules,
            model,
            compatibility_only=compatibility_only,
            exemplars=exemplars,
            era_conditioned=era_conditioned,
            request_borderline=request_borderline,
        )
    if self_consistency_samples == 1:
        return judge
    return SelfConsistencyJudge(judge, samples=self_consistency_samples)
