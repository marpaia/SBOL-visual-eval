"""The judge interface every backend implements."""

from __future__ import annotations

from typing import Protocol

from .schema import FigureContext, FigureVerdict


class FigureJudge(Protocol):
    """Judges one figure at a time against the historical rubric."""

    def judge(self, context: FigureContext) -> FigureVerdict: ...
