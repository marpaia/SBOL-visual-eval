"""The judge interface every backend implements."""

from __future__ import annotations

from typing import Protocol

from .schema import FigureContext, FigureVerdict


class FigureJudge(Protocol):
    """Judges one figure at a time against the historical rubric."""

    def judge(self, context: FigureContext) -> FigureVerdict: ...


class PaperJudge(FigureJudge, Protocol):
    """Judges every figure in a paper under one shared review context."""

    def judge_paper(self, contexts: tuple[FigureContext, ...]) -> tuple[FigureVerdict, ...]: ...
