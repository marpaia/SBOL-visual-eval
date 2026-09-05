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
from ..evaluation.adjudication import build_compatibility_adjudication
from ..evaluation.cascade_bench import cascade_figures, cascade_papers, run_cascade_benchmark
from ..evaluation.compatibility import (
    assign_era_stratified_partitions,
    run_compatibility_benchmark,
    saturated_compatibility_papers,
    saturated_figures,
)
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
    score.add_argument("--self-consistency", type=int, choices=(1, 3), default=1)
    score.add_argument(
        "--whole-paper",
        action="store_true",
        help="judge every figure with one paper-level consistency call",
    )
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
    evaluate.add_argument("--self-consistency", type=int, choices=(1, 3), default=1)
    evaluate.add_argument("--sample", type=int, help="evaluate a seeded random sample")
    evaluate.add_argument("--seed", type=int, default=7)
    evaluate.add_argument("--years", type=int, nargs="*")
    evaluate.add_argument(
        "--include-other-editions",
        action="store_true",
        help="also evaluate papers whose only local PDF is not the Version of Record",
    )
    evaluate.add_argument("--report-stem", default="evaluator_agreement")
    evaluate.add_argument("--workers", type=int, default=1)
    evaluate.add_argument(
        "--whole-paper",
        action="store_true",
        help="judge each paper's figures together",
    )

    compatibility = subparsers.add_parser(
        "compatibility",
        help="benchmark the compatibility stage on figures labeled by saturated counts",
    )
    compatibility.add_argument("--judge", choices=JUDGE_BACKENDS, default="anthropic")
    compatibility.add_argument("--model", help="judge model override")
    compatibility.add_argument("--self-consistency", type=int, choices=(1, 3), default=1)
    compatibility.add_argument("--sample", type=int, help="benchmark a seeded random sample")
    compatibility.add_argument("--seed", type=int, default=7)
    compatibility.add_argument("--workers", type=int, default=4)
    compatibility.add_argument("--report-stem", default="compatibility_benchmark")
    compatibility.add_argument(
        "--holdout-fraction",
        type=float,
        default=0.2,
        help="paper-level holdout fraction within each era and label stratum",
    )
    compatibility.add_argument("--split-seed", type=int, default=20260904)
    compatibility.add_argument(
        "--partition",
        choices=("all", "calibration", "holdout"),
        default="all",
    )
    compatibility.add_argument(
        "--resume",
        action="store_true",
        help="resume successful judgments from the report checkpoint",
    )
    compatibility.add_argument(
        "--whole-paper",
        action="store_true",
        help="judge each saturated paper's figures together",
    )

    cascade = subparsers.add_parser(
        "cascade",
        help="benchmark compliance and best practice on fully entailed cascade figures",
    )
    cascade.add_argument("--judge", choices=JUDGE_BACKENDS, default="anthropic")
    cascade.add_argument("--model", help="judge model override")
    cascade.add_argument("--self-consistency", type=int, choices=(1, 3), default=1)
    cascade.add_argument("--sample", type=int, help="benchmark a seeded random sample")
    cascade.add_argument("--seed", type=int, default=7)
    cascade.add_argument("--workers", type=int, default=4)
    cascade.add_argument("--report-stem", default="cascade_benchmark")

    adjudicate = subparsers.add_parser(
        "adjudicate",
        help="build an expert-review gallery from exact compatibility disagreements",
    )
    adjudicate.add_argument("report", type=Path, help="compatibility benchmark CSV")
    adjudicate.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    layout = Layout(args.root.resolve())

    if args.command == "score":
        rules = load_rubric(layout)
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            self_consistency_samples=args.self_consistency,
        )
        evaluation = evaluate_pdf(args.pdf.resolve(), judge, rules, whole_paper=args.whole_paper)
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
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            self_consistency_samples=args.self_consistency,
        )
        papers = sweep_papers(
            layout,
            version_of_record_only=not args.include_other_editions,
            years=tuple(args.years) if args.years else None,
        )
        papers = sample_papers(papers, args.sample, args.seed)
        if not papers:
            print("No evaluable papers matched the sweep filters", file=sys.stderr)
            raise SystemExit(1)
        summary = run_sweep(
            layout,
            judge,
            rules,
            papers,
            report_stem=args.report_stem,
            workers=args.workers,
            whole_paper=args.whole_paper,
        )
        agreement = summary["agreement"]
        print(
            f"Evaluated {summary['papers_evaluated']:,}/{summary['papers_attempted']:,} papers; "
            f"all-counts exact rate {agreement['all_counts_exact_rate']:.1%}"
        )
    elif args.command == "compatibility":
        rules = load_rubric(layout)
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            compatibility_only=True,
            self_consistency_samples=args.self_consistency,
        )
        papers = saturated_compatibility_papers(layout)
        papers = sample_papers(papers, args.sample, args.seed)
        figures = saturated_figures(layout, papers)
        figures = assign_era_stratified_partitions(
            figures,
            holdout_fraction=args.holdout_fraction,
            seed=args.split_seed,
        )
        if args.partition != "all":
            figures = [figure for figure in figures if figure.benchmark_partition == args.partition]
        if not figures:
            print("No saturated figures matched the benchmark filters", file=sys.stderr)
            raise SystemExit(1)
        summary = run_compatibility_benchmark(
            layout,
            judge,
            figures,
            workers=args.workers,
            report_stem=args.report_stem,
            resume=args.resume,
            whole_paper=args.whole_paper,
        )
        print(
            f"Compatibility benchmark: {summary['judged']:,} figures, "
            f"accuracy {summary['accuracy']:.1%}, recall {summary['recall']:.1%}, "
            f"false-positive rate {summary['false_positive_rate']:.1%}"
        )
    elif args.command == "cascade":
        rules = load_rubric(layout)
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            self_consistency_samples=args.self_consistency,
        )
        papers = sample_papers(cascade_papers(layout), args.sample, args.seed)
        figures = cascade_figures(layout, papers)
        if not figures:
            print("No cascade figures matched the benchmark filters", file=sys.stderr)
            raise SystemExit(1)
        summary = run_cascade_benchmark(
            layout,
            judge,
            rules,
            figures,
            workers=args.workers,
            report_stem=args.report_stem,
        )
        print(
            f"Cascade benchmark: {summary['judged']:,} figures; accuracy "
            f"compatible {summary['compatible_accuracy']:.1%}, "
            f"compliant {summary['compliant_accuracy']:.1%}, "
            f"best practice {summary['best_practice_accuracy']:.1%}"
        )
    elif args.command == "adjudicate":
        report_path = args.report if args.report.is_absolute() else layout.root / args.report
        output_dir = args.output_dir
        if output_dir is not None and not output_dir.is_absolute():
            output_dir = layout.root / output_dir
        summary = build_compatibility_adjudication(
            layout,
            report_path,
            output_dir=output_dir,
        )
        print(
            f"Adjudication artifact: {summary['disagreements']:,} disagreements at "
            f"{summary['review_path']}"
        )
