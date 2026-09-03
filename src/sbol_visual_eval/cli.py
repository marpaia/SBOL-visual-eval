"""Command-line interface for building and validating the SBOL Visual corpus."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .corpus.acquisition.downloads import download_papers
from .corpus.build import build_corpus
from .corpus.inventory import build_local_inventory
from .corpus.layout import Layout
from .corpus.sources import acquire_sources
from .corpus.util.storage import write_json
from .corpus.validation import validate_corpus


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current working directory)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire = subparsers.add_parser("acquire", help="download and checksum primary sources")
    acquire.add_argument("--force", action="store_true", help="redownload existing raw files")

    build = subparsers.add_parser("build", help="normalize labels and resolve metadata")
    build.add_argument("--offline", action="store_true", help="require existing metadata caches")
    build.add_argument(
        "--refresh-metadata", action="store_true", help="refresh Crossref and OpenAlex caches"
    )

    prepare = subparsers.add_parser("prepare", help="acquire raw sources and build the corpus")
    prepare.add_argument("--force", action="store_true", help="redownload existing raw files")
    prepare.add_argument(
        "--refresh-metadata", action="store_true", help="refresh Crossref and OpenAlex caches"
    )

    download = subparsers.add_parser(
        "download-papers", help="download source papers under a conservative rights policy"
    )
    download.add_argument("--allow-license-unknown", action="store_true")
    download.add_argument("--include-publisher", action="store_true")
    download.add_argument("--workers", type=int, default=6)
    download.add_argument("--limit", type=int)
    download.add_argument("--force", action="store_true")
    download.add_argument("--report-stem", default="downloads")

    download_pmc = subparsers.add_parser(
        "download-pmc", help="download official PMC article PDFs and media packages"
    )
    download_pmc.add_argument("--workers", type=int, default=6)

    biorxiv_preprints = subparsers.add_parser(
        "acquire-biorxiv-preprints",
        help="acquire the six reviewed bioRxiv predecessor preprints",
    )
    biorxiv_preprints.add_argument(
        "--source-dir",
        type=Path,
        help="import staged <DOI-suffix>.api.json and .pdf files instead of downloading",
    )

    pmc_web_bridge = subparsers.add_parser(
        "serve-pmc-web-bridge",
        help="receive public PMC author-manuscript PDFs from a loopback browser bridge",
    )
    pmc_web_bridge.add_argument("--host", default="127.0.0.1")
    pmc_web_bridge.add_argument("--port", type=int, default=8766)

    publisher_bridge = subparsers.add_parser(
        "serve-publisher-bridge",
        help="receive authenticated ACS PDFs from a loopback browser bridge",
    )
    publisher_bridge.add_argument("--host", default="127.0.0.1")
    publisher_bridge.add_argument("--port", type=int, default=8765)
    publisher_bridge.add_argument(
        "--browser-origin",
        default="https://pubs-acs-org.colorado.idm.oclc.org",
    )

    publisher_download = subparsers.add_parser(
        "download-publisher-authenticated",
        help="download ACS VOR PDFs through an authenticated EZProxy session",
    )
    publisher_download.add_argument("--delay", type=float, default=1.0)
    publisher_download.add_argument("--max-retries", type=int, default=5)
    publisher_download.add_argument("--workers", type=int, default=1)
    publisher_download.add_argument("--limit", type=int)
    publisher_download.add_argument("--ca-bundle", type=Path)
    publisher_download.add_argument(
        "--browser-origin",
        default="https://pubs-acs-org.colorado.idm.oclc.org",
    )
    publisher_download.add_argument("--impersonate", default="chrome136")

    subparsers.add_parser(
        "backfill-generic-manifests",
        help="verify and upgrade repository-paper provenance manifests",
    )
    subparsers.add_parser("inventory", help="summarize locally acquired paper artifacts")
    subparsers.add_parser("validate", help="run corpus integrity checks")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _argument_parser().parse_args(argv)
    layout = Layout(args.root.resolve())
    if args.command == "acquire":
        acquire_sources(layout, force=args.force)
    elif args.command == "build":
        build_corpus(layout, offline=args.offline, refresh_metadata=args.refresh_metadata)
    elif args.command == "prepare":
        acquire_sources(layout, force=args.force)
        build_corpus(layout, refresh_metadata=args.refresh_metadata)
    elif args.command == "download-papers":
        if args.workers < 1:
            raise SystemExit("--workers must be at least 1")
        download_papers(
            layout,
            allow_license_unknown=args.allow_license_unknown,
            include_publisher=args.include_publisher,
            workers=args.workers,
            limit=args.limit,
            force=args.force,
            report_stem=args.report_stem,
        )
    elif args.command == "download-pmc":
        if args.workers < 1:
            raise SystemExit("--workers must be at least 1")
        from .corpus.acquisition.pmc import acquire_pmc

        acquire_pmc(layout, workers=args.workers)
    elif args.command == "acquire-biorxiv-preprints":
        from .corpus.acquisition.biorxiv import acquire_biorxiv_preprints

        summary = acquire_biorxiv_preprints(layout, source_dir=args.source_dir)
        print(
            "bioRxiv predecessor preprints: "
            f"{summary['downloaded']} downloaded, "
            f"{summary['already_present']} already present, "
            f"{summary['failed']} failed"
        )
        if summary["failed"]:
            raise SystemExit(1)
    elif args.command == "serve-pmc-web-bridge":
        if not 1 <= args.port <= 65535:
            raise SystemExit("--port must be between 1 and 65535")
        from .corpus.acquisition.pmc_web import serve_pmc_web_bridge

        serve_pmc_web_bridge(layout, host=args.host, port=args.port)
    elif args.command == "serve-publisher-bridge":
        if not 1 <= args.port <= 65535:
            raise SystemExit("--port must be between 1 and 65535")
        from .corpus.acquisition.publisher import serve_publisher_bridge

        serve_publisher_bridge(
            layout,
            host=args.host,
            port=args.port,
            browser_origin=args.browser_origin,
        )
    elif args.command == "download-publisher-authenticated":
        from .corpus.acquisition.publisher import acquire_publisher_authenticated

        acquire_publisher_authenticated(
            layout,
            delay_seconds=args.delay,
            max_retries=args.max_retries,
            workers=args.workers,
            limit=args.limit,
            ca_bundle=args.ca_bundle,
            browser_origin=args.browser_origin,
            impersonate=args.impersonate,
        )
    elif args.command == "backfill-generic-manifests":
        from .corpus.acquisition.generic_manifest import backfill_generic_manifests

        summary = backfill_generic_manifests(layout)
        write_json(layout.reports / "generic_manifest_backfill.json", summary)
        print(
            "Generic manifest backfill: "
            f"{summary['updated']} updated, {summary['unchanged']} unchanged, "
            f"{summary['failed']} failed"
        )
        if summary["failed"]:
            raise SystemExit(1)
    elif args.command == "inventory":
        build_local_inventory(layout)
    elif args.command == "validate":
        errors, warnings = validate_corpus(layout)
        for warning in warnings:
            print(f"WARNING: {warning}")
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(1)
        print("Corpus validation passed")
