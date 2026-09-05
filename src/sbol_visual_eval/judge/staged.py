"""Two-stage judging with a dedicated compatibility decision."""

from __future__ import annotations

from .protocol import FigureJudge
from .schema import FigureContext, FigureVerdict


class StagedCascadeJudge:
    """Run the cheap compatibility prompt before the downstream rubric prompt."""

    def __init__(self, compatibility_judge: FigureJudge, downstream_judge: FigureJudge) -> None:
        self._compatibility_judge = compatibility_judge
        self._downstream_judge = downstream_judge

    def judge(self, context: FigureContext) -> FigureVerdict:
        compatibility = self._compatibility_judge.judge(context)
        if not compatibility.compatible:
            return compatibility
        downstream = self._downstream_judge.judge(context)
        return _merge_verdicts(compatibility, downstream)

    def judge_paper(self, contexts: tuple[FigureContext, ...]) -> tuple[FigureVerdict, ...]:
        compatibility_method = getattr(self._compatibility_judge, "judge_paper", None)
        downstream_method = getattr(self._downstream_judge, "judge_paper", None)
        if not callable(compatibility_method) or not callable(downstream_method):
            raise TypeError("both staged judges must support whole-paper evaluation")

        compatibility_verdicts = tuple(compatibility_method(contexts))
        if [verdict.figure_number for verdict in compatibility_verdicts] != [
            context.figure_number for context in contexts
        ]:
            raise ValueError("compatibility stage returned figures outside the paper census")

        positive_contexts = tuple(
            context
            for context, verdict in zip(contexts, compatibility_verdicts, strict=True)
            if verdict.compatible
        )
        downstream_by_number = {}
        if positive_contexts:
            downstream_verdicts = tuple(downstream_method(positive_contexts))
            if [verdict.figure_number for verdict in downstream_verdicts] != [
                context.figure_number for context in positive_contexts
            ]:
                raise ValueError("downstream stage returned figures outside its compatible subset")
            downstream_by_number = {
                verdict.figure_number: verdict for verdict in downstream_verdicts
            }

        return tuple(
            _merge_verdicts(verdict, downstream_by_number[verdict.figure_number])
            if verdict.compatible
            else verdict
            for verdict in compatibility_verdicts
        )

    def metadata(self) -> dict[str, str]:
        """Preserve provider identity and identify the staged pipeline."""
        metadata = getattr(self._downstream_judge, "metadata", None)
        if not callable(metadata):
            return {"backend": type(self._downstream_judge).__name__, "pipeline": "staged"}
        return {**metadata(), "pipeline": "staged"}


def _merge_verdicts(compatibility: FigureVerdict, downstream: FigureVerdict) -> FigureVerdict:
    if compatibility.figure_number != downstream.figure_number:
        raise ValueError("staged verdicts refer to different figures")
    return FigureVerdict(
        figure_number=compatibility.figure_number,
        compatible=True,
        rationale=(
            f"Compatibility stage: {compatibility.rationale} "
            f"Downstream stage: {downstream.rationale}"
        ),
        findings=downstream.findings,
        borderline=compatibility.borderline or downstream.borderline,
    )
