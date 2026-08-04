"""Command-line interface and registered source-adapter selection."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .http import DEFAULT_USER_AGENT, HttpClient
from .pipeline import CrawlPipeline
from .sources import SOURCE_ADAPTERS
from .storage import DatasetStorage


def positive_int(value: str) -> int:
    """Parse a string as an integer and reject non-positive values for argparse."""
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def nonnegative_float(value: str) -> float:
    """Parse a string as a float and reject negative values for argparse."""
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Register shared crawl options (limits, output path, HTTP settings) on a subparser."""
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=100,
        help="maximum incomplete source objects attempted per run; skips do not count (default: 100)",
    )
    parser.add_argument("--output", type=Path, default=Path("dataset"), help="dataset directory")
    parser.add_argument("--skip-images", action="store_true", help="save metadata without image files")
    parser.add_argument("--force", action="store_true", help="fetch complete records again")
    parser.add_argument("--timeout", type=nonnegative_float, default=30.0, help="request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="temporary failure retry count")
    parser.add_argument(
        "--request-delay",
        type=nonnegative_float,
        default=0.25,
        help="minimum seconds between requests (default: 0.25)",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent string")


def build_parser() -> argparse.ArgumentParser:
    """Build the root parser with one subcommand per registered source adapter."""
    parser = argparse.ArgumentParser(
        description="Build a source-grounded image dataset without AI-generated annotations."
    )
    subparsers = parser.add_subparsers(dest="source", required=True, title="sources")
    for source_key, adapter_class in SOURCE_ADAPTERS.items():
        source_parser = subparsers.add_parser(
            source_key,
            help=f"crawl {adapter_class.source_name}",
        )
        add_common_arguments(source_parser)
        adapter_class.add_cli_arguments(source_parser)
        source_parser.set_defaults(adapter_class=adapter_class)
    return parser


def _with_legacy_default_source(argv: list[str]) -> list[str]:
    """Keep first-version commands working while supporting explicit source names."""
    if argv and argv[0] in {"-h", "--help"}:
        return argv
    if not argv or argv[0].startswith("-"):
        return ["metmuseum", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args, discover source IDs, crawl each object, and write the dataset."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_with_legacy_default_source(raw_argv))
    if args.retries < 0:
        parser.error("--retries must be zero or greater")
    if args.timeout == 0:
        parser.error("--timeout must be greater than zero")

    client = HttpClient(
        timeout=args.timeout,
        retries=args.retries,
        request_delay=args.request_delay,
        user_agent=args.user_agent,
    )
    adapter = args.adapter_class()
    try:
        if getattr(args, "query", None):
            print(f"Searching {adapter.source_name} for {args.query!r} ...")
        elif getattr(args, "all_objects", False):
            print(f"Discovering objects from {adapter.source_name} ...")
        discovery = adapter.discover(args, client)
    except Exception as error:
        print(f"Could not collect source object IDs: {error}", file=sys.stderr)
        return 1

    output_dir = args.output.resolve()
    storage = DatasetStorage(output_dir)
    storage.prepare(adapter.source_key)
    discovery_path = storage.write_discovery(
        adapter.source_key,
        {
            "method": discovery.method,
            "parameters": discovery.parameters,
            "request_url": discovery.request_url,
            "total_reported": discovery.total_reported,
            "discovered_count": len(discovery.source_ids),
            "source_ids": discovery.source_ids,
        },
    )
    print(
        f"Discovered {len(discovery.source_ids)} object ID(s); "
        f"snapshot: {storage.relative_path(discovery_path)}"
    )
    if not discovery.source_ids:
        print(f"No matching {adapter.source_name} object IDs were found.")
        return 0

    pipeline = CrawlPipeline(
        adapter=adapter,
        client=client,
        storage=storage,
        skip_images=args.skip_images,
        force=args.force,
    )
    print(
        f"Processing up to {args.limit} incomplete object(s) "
        f"from {adapter.source_name} into {output_dir}"
    )
    show_individual_skips = len(discovery.source_ids) <= 100

    def report_progress(
        index: int,
        total: int,
        source_id: str,
        status: str,
        error: Exception | None,
    ) -> None:
        reference = adapter.display_reference(source_id)
        if status == "skipped":
            if show_individual_skips:
                print(f"[{index}/{total}] {reference}: already complete")
        elif status == "failed":
            print(f"[{index}/{total}] {reference}: failed: {error}", file=sys.stderr)
        else:
            print(f"[{index}/{total}] {reference}: saved")

    summary = pipeline.crawl_many(
        discovery.source_ids,
        max_new=args.limit,
        on_progress=report_progress,
    )

    metadata_count = storage.rebuild_metadata()
    print(
        f"Done: {summary.completed} completed, {summary.skipped} skipped, "
        f"{summary.failed} failed; "
        f"metadata.jsonl contains {metadata_count} record(s)."
    )
    if summary.limit_reached:
        print("Batch limit reached. Run the same command again to continue with the next incomplete objects.")
    return 1 if summary.failed else 0
