"""Command-line interface for the SBOL Visual evaluator.

``score`` is the evaluator's public shape — one manuscript PDF in, one
historical-format validation score out. ``census`` and ``evaluate``
measure the evaluator against the historical corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ..corpus.layout import Layout
from ..evaluation.harness import run_sweep, sample_papers, sweep_papers
from ..evaluation.reconciliation import build_figure_census
from ..judge.rubric import load_rubric
from .judges import JUDGE_BACKENDS, build_judge
from .pipeline import evaluate_pdf


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current working directory)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    score = subparsers.add_parser("score", help="score one manuscript PDF in the historical format")
    score.add_argument("pdf", type=Path)
    score.add_argument("--judge", choices=JUDGE_BACKENDS, default="anthropic")
    score.add_argument("--model", help="judge model override")
    score.add_argument("--output", type=Path, help="write the score JSON here instead of stdout")

    census = subparsers.add_parser(
        "census", help="reconcile the figure census against historical figure totals"
    )
    census.add_argument("--workers", type=int, default=8)
    census.add_argument("--limit", type=int)

    evaluate = subparsers.add_parser(
        "evaluate", help="run the evaluator over local papers and score agreement"
    )
    evaluate.add_argument("--judge", choices=JUDGE_BACKENDS, default="anthropic")
    evaluate.add_argument("--model", help="judge model override")
    evaluate.add_argument("--sample", type=int, help="evaluate a seeded random sample")
    evaluate.add_argument("--seed", type=int, default=7)
    evaluate.add_argument("--years", type=int, nargs="*")
    evaluate.add_argument(
        "--include-other-editions",
        action="store_true",
        help="also evaluate papers whose only local PDF is not the Version of Record",
    )
    evaluate.add_argument("--report-stem", default="evaluator_agreement")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    layout = Layout(args.root.resolve())

    if args.command == "score":
        rules = load_rubric(layout)
        judge = build_judge(args.judge, rules, args.model)
        evaluation = evaluate_pdf(args.pdf.resolve(), judge, rules)
        payload = json.dumps(evaluation.to_dict(), indent=2, sort_keys=True)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
    elif args.command == "census":
        if args.workers < 1:
            raise SystemExit("--workers must be at least 1")
        summary = build_figure_census(layout, workers=args.workers, limit=args.limit)
        totals = summary["all_pdfs"]
        print(
            f"Figure census: {totals['exact']:,}/{totals['censused']:,} papers exact "
            f"({totals['exact_rate']:.1%}); "
            f"VOR exact rate {summary['version_of_record_pdfs']['exact_rate']:.1%}"
        )
    elif args.command == "evaluate":
        rules = load_rubric(layout)
        judge = build_judge(args.judge, rules, args.model)
        papers = sweep_papers(
            layout,
            version_of_record_only=not args.include_other_editions,
            years=tuple(args.years) if args.years else None,
        )
        papers = sample_papers(papers, args.sample, args.seed)
        if not papers:
            print("No evaluable papers matched the sweep filters", file=sys.stderr)
            raise SystemExit(1)
        summary = run_sweep(layout, judge, rules, papers, report_stem=args.report_stem)
        agreement = summary["agreement"]
        print(
            f"Evaluated {summary['papers_evaluated']:,}/{summary['papers_attempted']:,} papers; "
            f"all-counts exact rate {agreement['all_counts_exact_rate']:.1%}"
        )
