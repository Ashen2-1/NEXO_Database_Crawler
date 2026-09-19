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


def additional_records_needed(dataset_target: int, qualifying_count: int) -> int:
    """Return how many more qualifying records are needed to reach the final dataset target."""
    return max(dataset_target - qualifying_count, 0)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Register shared crawl options (limits, output path, HTTP settings) on a subparser."""
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=100,
        help="minimum target number of qualifying records in the final export (default: 100)",
    )
    parser.add_argument(
        "--max-examined",
        type=positive_int,
        help="optional safety cap on all candidate objects examined in this run",
    )
    parser.add_argument("--output", type=Path, default=Path("dataset"), help="dataset directory")
    parser.add_argument("--skip-images", action="store_true", help="save metadata without image files")
    parser.add_argument(
        "--require-image",
        action="store_true",
        help="export only records whose image was successfully downloaded",
    )
    parser.add_argument(
        "--public-domain-only",
        action="store_true",
        help="keep only source objects explicitly marked public domain",
    )
    parser.add_argument(
        "--enrich-descriptions",
        action="store_true",
        help="fetch optional source-grounded descriptions from adapter-supported sources",
    )
    parser.add_argument(
        "--require-description",
        action="store_true",
        help="enrich descriptions and export only records with a non-empty sourced description",
    )
    parser.add_argument(
        "--year-from",
        type=int,
        help="keep records whose creation interval overlaps this year or later",
    )
    parser.add_argument(
        "--year-to",
        type=int,
        help="keep records whose creation interval overlaps this year or earlier",
    )
    parser.add_argument(
        "--require-creator",
        action="store_true",
        help="keep only records with at least one non-empty creator name",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="start or resume a refresh job and download images again",
    )
    parser.add_argument("--timeout", type=nonnegative_float, default=30.0, help="request timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="temporary failure retry count")
    parser.add_argument(
        "--request-delay",
        type=nonnegative_float,
        default=1.0,
        help="minimum seconds between requests (default: 1.0)",
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
    if args.require_image and args.skip_images:
        parser.error("--require-image cannot be combined with --skip-images")
    if args.year_from is not None and args.year_to is not None and args.year_from > args.year_to:
        parser.error("--year-from cannot be greater than --year-to")

    enrich_descriptions = args.enrich_descriptions or args.require_description

    client = HttpClient(
        timeout=args.timeout,
        retries=args.retries,
        request_delay=args.request_delay,
        user_agent=args.user_agent,
    )
    adapter = args.adapter_class()
    try:
        if getattr(args, "target", None):
            scope_note = (
                " (person scope: broad)"
                if "person" in args.target and getattr(args, "person_scope", "standard") == "broad"
                else ""
            )
            print(
                f"Discovering {adapter.source_name} targets: "
                f"{', '.join(args.target)}{scope_note} ..."
            )
        elif getattr(args, "query", None):
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
    targets = tuple(getattr(args, "target", ()) or ())
    export_requirements = {
        "require_image": args.require_image,
        "require_description": args.require_description,
        "require_creator": args.require_creator,
        "year_from": args.year_from,
        "year_to": args.year_to,
        "targets": targets,
    }
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
    refresh_path = None
    refresh_state = None
    refresh_mode = bool(getattr(args, "updated_since", None) or args.force)
    if refresh_mode:
        refresh_selector = {
            "method": discovery.method,
            "parameters": discovery.parameters,
            "request_url": discovery.request_url,
            "force": bool(args.force),
            "year_from": args.year_from,
            "year_to": args.year_to,
            "require_creator": args.require_creator,
        }
        refresh_path, refresh_state, resumed = storage.start_or_resume_refresh(
            adapter.source_key,
            refresh_selector,
        )
        action = "Resuming" if resumed else "Starting"
        print(
            f"{action} refresh job {refresh_state['job_id']}; "
            f"state: {storage.relative_path(refresh_path)}"
        )
    if not discovery.source_ids:
        if refresh_path is not None:
            storage.update_refresh_job(
                refresh_path,
                completed=True,
                summary={"discovered": 0, "completed": 0, "failed": 0},
            )
        print(f"No matching {adapter.source_name} object IDs were found.")
        return 0

    pipeline = CrawlPipeline(
        adapter=adapter,
        client=client,
        storage=storage,
        skip_images=args.skip_images,
        force=args.force,
        enrich_descriptions=enrich_descriptions,
        public_domain_only=args.public_domain_only,
        require_image=args.require_image,
        require_description=args.require_description,
        require_creator=args.require_creator,
        year_from=args.year_from,
        year_to=args.year_to,
        targets=targets,
        refresh_after=refresh_state["started_at"] if refresh_state is not None else None,
    )
    existing_qualifying_count = storage.count_matching_records(**export_requirements)
    remaining_target = additional_records_needed(args.limit, existing_qualifying_count)
    safety_cap = f" (examining at most {args.max_examined})" if args.max_examined else ""
    print(
        f"Dataset target: {args.limit} qualifying record(s); "
        f"{existing_qualifying_count} already qualify, "
        f"up to {remaining_target} additional record(s) needed{safety_cap}."
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
        elif status == "filtered":
            if show_individual_skips:
                print(f"[{index}/{total}] {reference}: filtered (target, rights, or strict requirement)")
        elif status == "unchanged":
            print(f"[{index}/{total}] {reference}: checked, unchanged")
        elif status == "updated":
            print(f"[{index}/{total}] {reference}: metadata updated")
        elif status == "created":
            print(f"[{index}/{total}] {reference}: created")
        else:
            print(f"[{index}/{total}] {reference}: saved")

    summary = pipeline.crawl_many(
        discovery.source_ids,
        max_new=remaining_target,
        max_examined=args.max_examined,
        on_progress=report_progress,
    )

    metadata_count = storage.rebuild_metadata(**export_requirements)
    print(
        f"Run results: {summary.completed} successful this run, "
        f"{summary.skipped} already complete, {summary.filtered} filtered, "
        f"{summary.failed} failed."
    )
    print(
        f"Successful work: {summary.created} newly created, "
        f"{summary.completed_from_cache} completed from cached source data, "
        f"{summary.updated} updated, {summary.unchanged} checked unchanged."
    )
    print(
        f"Export results: metadata.jsonl, metadata_full.csv, and metadata_ai.csv "
        f"contain {metadata_count} record(s)."
    )
    print(
        f"Dataset progress: {existing_qualifying_count} qualifying before this run, "
        f"{summary.completed} newly qualified this run, {metadata_count} in the final export."
    )
    if refresh_path is not None:
        refresh_completed = (
            not summary.limit_reached
            and not summary.max_examined_reached
            and not summary.halted
            and summary.failed == 0
        )
        storage.update_refresh_job(
            refresh_path,
            completed=refresh_completed,
            summary={
                "discovered": summary.discovered,
                "examined": summary.examined,
                "attempted": summary.attempted,
                "completed": summary.completed,
                "created": summary.created,
                "updated": summary.updated,
                "unchanged": summary.unchanged,
                "completed_from_cache": summary.completed_from_cache,
                "skipped": summary.skipped,
                "filtered": summary.filtered,
                "failed": summary.failed,
                "limit_reached": summary.limit_reached,
                "max_examined_reached": summary.max_examined_reached,
                "halted": summary.halted,
                "halt_reason": summary.halt_reason,
                "dataset_target": args.limit,
                "qualifying_before_run": existing_qualifying_count,
                "final_export_count": metadata_count,
            },
        )
        print(f"Refresh job status: {'completed' if refresh_completed else 'in progress'}.")
    if metadata_count >= args.limit:
        print(f"Dataset target reached: {metadata_count}/{args.limit}.")
    elif summary.halted:
        print(
            f"Dataset target not reached: {metadata_count}/{args.limit}; "
            "the run stopped after repeated HTTP 403 responses from the source."
        )
        print("Wait before resuming and consider a larger --request-delay (for example, 2.0).")
    elif summary.max_examined_reached:
        print(
            f"Dataset target not reached: {metadata_count}/{args.limit}; "
            f"stopped after examining {summary.examined} candidate(s)."
        )
    else:
        print(
            f"Dataset target not reached: {metadata_count}/{args.limit}; "
            "the discovered candidate list was exhausted."
        )
    return 1 if summary.failed else 0
