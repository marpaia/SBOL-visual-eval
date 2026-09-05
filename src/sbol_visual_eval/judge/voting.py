"""Selective self-consistency voting for borderline figure verdicts."""

from __future__ import annotations

from collections import Counter
from threading import Lock

from .protocol import FigureJudge
from .schema import FigureContext, FigureVerdict, RuleFinding


class SelfConsistencyJudge:
    """Resamples only borderline first verdicts and returns a majority decision."""

    def __init__(self, judge: FigureJudge, *, samples: int = 3) -> None:
        if samples < 3 or samples % 2 == 0:
            raise ValueError("self-consistency samples must be an odd integer of at least 3")
        self._judge = judge
        self._samples = samples
        self._lock = Lock()
        self._initial_figure_verdicts = 0
        self._extra_figure_verdicts = 0
        self._initial_paper_calls = 0
        self._extra_paper_calls = 0

    def judge(self, context: FigureContext) -> FigureVerdict:
        first = self._judge.judge(context)
        with self._lock:
            self._initial_figure_verdicts += 1
        if not first.borderline:
            return first
        verdicts = [first]
        verdicts.extend(self._judge.judge(context) for _ in range(self._samples - 1))
        with self._lock:
            self._extra_figure_verdicts += self._samples - 1
        return _majority_verdict(verdicts)

    def judge_paper(self, contexts: tuple[FigureContext, ...]) -> tuple[FigureVerdict, ...]:
        judge_paper = getattr(self._judge, "judge_paper", None)
        if not callable(judge_paper):
            raise TypeError("wrapped judge does not support whole-paper evaluation")
        first = tuple(judge_paper(contexts))
        with self._lock:
            self._initial_paper_calls += 1
            self._initial_figure_verdicts += len(first)
        borderline_numbers = {verdict.figure_number for verdict in first if verdict.borderline}
        if not borderline_numbers:
            return first

        samples = [first]
        samples.extend(tuple(judge_paper(contexts)) for _ in range(self._samples - 1))
        with self._lock:
            self._extra_paper_calls += self._samples - 1
            self._extra_figure_verdicts += (self._samples - 1) * len(first)

        by_sample = [{verdict.figure_number: verdict for verdict in sample} for sample in samples]
        return tuple(
            _majority_verdict([sample[verdict.figure_number] for sample in by_sample])
            if verdict.figure_number in borderline_numbers
            else verdict
            for verdict in first
        )

    def sampling_statistics(self) -> dict[str, int]:
        with self._lock:
            return {
                "samples": self._samples,
                "initial_figure_verdicts": self._initial_figure_verdicts,
                "extra_figure_verdicts": self._extra_figure_verdicts,
                "initial_paper_calls": self._initial_paper_calls,
                "extra_paper_calls": self._extra_paper_calls,
            }


def _majority_verdict(verdicts: list[FigureVerdict]) -> FigureVerdict:
    figure_numbers = {verdict.figure_number for verdict in verdicts}
    if len(figure_numbers) != 1:
        raise ValueError("self-consistency samples refer to different figures")

    compatible = Counter(verdict.compatible for verdict in verdicts).most_common(1)[0][0]
    matching = [verdict for verdict in verdicts if verdict.compatible is compatible]
    representative = next((verdict for verdict in matching if not verdict.borderline), matching[0])

    finding_maps = [
        {finding.rule_key: finding for finding in verdict.findings} for verdict in verdicts
    ]
    rule_keys = list(
        dict.fromkeys(finding.rule_key for verdict in verdicts for finding in verdict.findings)
    )
    findings = []
    if compatible:
        for rule_key in rule_keys:
            candidates = [mapping[rule_key] for mapping in finding_maps if rule_key in mapping]
            if not candidates:
                continue
            majority = Counter(finding.verdict for finding in candidates).most_common(1)[0][0]
            evidence = next(
                finding.evidence for finding in candidates if finding.verdict is majority
            )
            findings.append(RuleFinding(rule_key, majority, evidence))

    return FigureVerdict(
        figure_number=representative.figure_number,
        compatible=compatible,
        rationale=representative.rationale,
        findings=tuple(findings),
        borderline=Counter(verdict.borderline for verdict in verdicts).most_common(1)[0][0],
    )
