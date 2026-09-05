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
    exclude_reported_papers,
    run_compatibility_benchmark,
    sample_saturated_figures,
    saturated_compatibility_papers,
    saturated_figures,
)
from ..evaluation.compatibility_compare import compare_compatibility_reports
from ..evaluation.harness import (
    exclude_reported_sweep_papers,
    run_sweep,
    sample_papers,
    sweep_papers,
)
from ..evaluation.reconciliation import build_figure_census
from ..judge.exemplars import load_compatibility_exemplars
from ..judge.prompt import (
    BORDERLINE_PROMPT_PROFILE,
    CODEX_THRESHOLD_PROMPT_PROFILE,
    COMPATIBILITY_EXEMPLAR_PROFILE,
    COMPATIBILITY_PROMPT_PROFILE,
    ERA_COMPATIBILITY_PROMPT_PROFILE,
)
from ..judge.rubric import load_rubric
from .judges import JUDGE_BACKENDS, build_judge
from .pipeline import evaluate_pdf


def _prompt_profile(args: argparse.Namespace, *, compatibility_only: bool = False) -> str:
    profile = (
        ERA_COMPATIBILITY_PROMPT_PROFILE if args.era_conditioned else COMPATIBILITY_PROMPT_PROFILE
    )
    if args.few_shot:
        profile += f"+{COMPATIBILITY_EXEMPLAR_PROFILE}"
    if args.self_consistency > 1:
        profile += f"+{BORDERLINE_PROMPT_PROFILE}"
    if args.judge == "codex-cli" and not compatibility_only:
        profile += f"+{CODEX_THRESHOLD_PROMPT_PROFILE}"
    return profile


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
    score.add_argument("--few-shot", action="store_true", help="use calibrated image references")
    score.add_argument(
        "--era-conditioned", action="store_true", help="apply publication-era policy"
    )
    score.add_argument("--publication-year", type=int, help="paper year for era conditioning")
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
    evaluate.add_argument("--few-shot", action="store_true", help="use calibrated image references")
    evaluate.add_argument(
        "--era-conditioned", action="store_true", help="apply publication-era policy"
    )
    evaluate.add_argument("--sample", type=int, help="evaluate a seeded random sample")
    evaluate.add_argument("--seed", type=int, default=7)
    evaluate.add_argument("--years", type=int, nargs="*")
    evaluate.add_argument(
        "--exclude-report",
        action="append",
        type=Path,
        default=[],
        help="exclude every paper present in this benchmark CSV; repeatable",
    )
    evaluate.add_argument(
        "--include-other-editions",
        action="store_true",
        help="also evaluate papers whose only local PDF is not the Version of Record",
    )
    evaluate.add_argument("--report-stem", default="evaluator_agreement")
    evaluate.add_argument("--workers", type=int, default=1)
    evaluate.add_argument("--resume", action="store_true", help="resume successful papers")
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
    compatibility.add_argument(
        "--few-shot", action="store_true", help="use calibrated image references"
    )
    compatibility.add_argument(
        "--era-conditioned", action="store_true", help="apply publication-era policy"
    )
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
        "--exclude-report",
        action="append",
        type=Path,
        default=[],
        help="exclude every paper present in this compatibility CSV; repeatable",
    )
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
    cascade.add_argument("--few-shot", action="store_true", help="use calibrated image references")
    cascade.add_argument(
        "--era-conditioned", action="store_true", help="apply publication-era policy"
    )
    cascade.add_argument("--sample", type=int, help="benchmark a seeded random sample")
    cascade.add_argument("--seed", type=int, default=7)
    cascade.add_argument("--workers", type=int, default=4)
    cascade.add_argument("--report-stem", default="cascade_benchmark")
    cascade.add_argument("--resume", action="store_true", help="resume successful judgments")

    adjudicate = subparsers.add_parser(
        "adjudicate",
        help="build an expert-review gallery from exact compatibility disagreements",
    )
    adjudicate.add_argument("report", type=Path, help="compatibility benchmark CSV")
    adjudicate.add_argument("--output-dir", type=Path)

    compare = subparsers.add_parser(
        "compare-compatibility",
        help="compare paired compatibility benchmark reports",
    )
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    layout = Layout(args.root.resolve())

    if args.command == "score":
        if args.era_conditioned and args.publication_year is None:
            raise SystemExit("--publication-year is required with --era-conditioned")
        rules = load_rubric(layout)
        exemplars = load_compatibility_exemplars(layout) if args.few_shot else ()
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            exemplars=exemplars,
            self_consistency_samples=args.self_consistency,
            era_conditioned=args.era_conditioned,
        )
        evaluation = evaluate_pdf(
            args.pdf.resolve(),
            judge,
            rules,
            publication_year=args.publication_year,
            whole_paper=args.whole_paper,
        )
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
        exemplars = load_compatibility_exemplars(layout) if args.few_shot else ()
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            exemplars=exemplars,
            self_consistency_samples=args.self_consistency,
            era_conditioned=args.era_conditioned,
        )
        papers = sweep_papers(
            layout,
            version_of_record_only=not args.include_other_editions,
            years=tuple(args.years) if args.years else None,
        )
        exclusion_paths = [
            path.resolve() if path.is_absolute() else (layout.root / path).resolve()
            for path in args.exclude_report
        ]
        papers = exclude_reported_sweep_papers(papers, exclusion_paths)
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
            prompt_profile=_prompt_profile(args),
            resume=args.resume,
            benchmark_definition={
                "sample_papers": args.sample,
                "sample_seed": args.seed,
                "years": args.years,
                "version_of_record_only": not args.include_other_editions,
                "excluded_reports": [layout.display_path(path) for path in exclusion_paths],
            },
        )
        agreement = summary["agreement"]
        print(
            f"Evaluated {summary['papers_evaluated']:,}/{summary['papers_attempted']:,} papers; "
            f"all-counts exact rate {agreement['all_counts_exact_rate']:.1%}"
        )
    elif args.command == "compatibility":
        rules = load_rubric(layout)
        exemplars = load_compatibility_exemplars(layout) if args.few_shot else ()
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            compatibility_only=True,
            exemplars=exemplars,
            self_consistency_samples=args.self_consistency,
            era_conditioned=args.era_conditioned,
        )
        papers = saturated_compatibility_papers(layout)
        figures = saturated_figures(layout, papers)
        figures = assign_era_stratified_partitions(
            figures,
            holdout_fraction=args.holdout_fraction,
            seed=args.split_seed,
        )
        if args.partition != "all":
            figures = [figure for figure in figures if figure.benchmark_partition == args.partition]
        exclusion_paths = [
            path.resolve() if path.is_absolute() else (layout.root / path).resolve()
            for path in args.exclude_report
        ]
        figures = exclude_reported_papers(figures, exclusion_paths)
        figures = sample_saturated_figures(figures, args.sample, args.seed)
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
            prompt_profile=_prompt_profile(args, compatibility_only=True),
            benchmark_definition={
                "partition": args.partition,
                "sample_papers": args.sample,
                "sample_seed": args.seed,
                "split_seed": args.split_seed,
                "holdout_fraction": args.holdout_fraction,
                "excluded_reports": [layout.display_path(path) for path in exclusion_paths],
            },
        )
        print(
            f"Compatibility benchmark: {summary['judged']:,} figures, "
            f"accuracy {summary['accuracy']:.1%}, recall {summary['recall']:.1%}, "
            f"false-positive rate {summary['false_positive_rate']:.1%}"
        )
    elif args.command == "cascade":
        rules = load_rubric(layout)
        exemplars = load_compatibility_exemplars(layout) if args.few_shot else ()
        judge = build_judge(
            args.judge,
            rules,
            args.model,
            exemplars=exemplars,
            self_consistency_samples=args.self_consistency,
            era_conditioned=args.era_conditioned,
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
            prompt_profile=_prompt_profile(args),
            resume=args.resume,
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
    elif args.command == "compare-compatibility":
        baseline_path = (
            args.baseline if args.baseline.is_absolute() else layout.root / args.baseline
        )
        candidate_path = (
            args.candidate if args.candidate.is_absolute() else layout.root / args.candidate
        )
        output_path = args.output
        if output_path is not None and not output_path.is_absolute():
            output_path = layout.root / output_path
        summary = compare_compatibility_reports(
            layout,
            baseline_path,
            candidate_path,
            output_path=output_path,
        )
        baseline_exact = summary["baseline"]["paper_count_agreement"]["exact_rate"]
        candidate_exact = summary["candidate"]["paper_count_agreement"]["exact_rate"]
        print(
            f"Compatibility comparison: {summary['figures']:,} figures; saturated-paper "
            f"exact rate {baseline_exact:.1%} -> {candidate_exact:.1%}"
        )
