from __future__ import annotations

import argparse
import json
import sys

from historical_snapshot.core.bands import DEFAULT_BANDS
from historical_snapshot.core.snapshot import multi_snapshot_to_text
from historical_snapshot.service import DEFAULT_DATA_ROOT, run_snapshot, sync_properties


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapshot",
        description="Get property-level historical performance snapshots from CSV or Google Sheets data.",
    )
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser(
        "sync",
        help="Pull property tabs from Google Sheets into the local cache",
    )
    sync_parser.add_argument(
        "--property-folder",
        default=None,
        help="Sync one property by config folder name (e.g. onera, lafave)",
    )
    sync_parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Data root directory (default: data)",
    )
    sync_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format",
    )

    run_parser = subparsers.add_parser("run", help="Run a snapshot")
    _add_run_arguments(run_parser)

    parser.add_argument("--csv", help="Path to booking CSV export")
    _add_run_arguments(parser)
    return parser


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--property-id", default="LAFAVE", help="Property ID label for output")
    parser.add_argument("--property-name", default="LaFave", help="Property name label for output")
    parser.add_argument(
        "--property-folder",
        default=None,
        help="Property config folder name (e.g. flohom, WMB); inferred from CSV parent dir if omitted",
    )
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--date-basis",
        choices=("stay", "arrival", "reservation"),
        default="stay",
        help=(
            "Date filter: stay = bookings with occupied nights in range (default); "
            "arrival = check-in in range; reservation = booked in range"
        ),
    )
    parser.add_argument(
        "--inventory-listings",
        type=int,
        default=30,
        help="Total listing count for portfolio occupancy/revpar denominator",
    )
    parser.add_argument(
        "--breakdown-by",
        choices=("listing", "grouping"),
        default="listing",
        help="Second section breakdown grain",
    )
    parser.add_argument(
        "--bands",
        default=DEFAULT_BANDS,
        help=f"Booking window bands, e.g. '{DEFAULT_BANDS}'",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--data-root",
        default=str(DEFAULT_DATA_ROOT),
        help="Data root directory (default: data)",
    )
    parser.add_argument(
        "--yoy-compare-start-date",
        default=None,
        help="YoY comparison period start (YYYY-MM-DD); for live_listings comparable units",
    )
    parser.add_argument(
        "--yoy-compare-end-date",
        default=None,
        help="YoY comparison period end (YYYY-MM-DD)",
    )


def _run_sync(args: argparse.Namespace) -> int:
    result = sync_properties(
        data_root=args.data_root,
        property_folder=args.property_folder,
    )
    if args.format == "json":
        print(json.dumps(result, indent=2))
        return 1 if result["errors"] else 0

    for item in result["properties"]:
        print(
            f"Synced {item['folder']} ({item['tab']}): "
            f"{item['row_count']} rows -> {item['cache_path']}"
        )
    for error in result["errors"]:
        print(f"Error syncing {error['folder']}: {error['error']}", file=sys.stderr)
    return 1 if result["errors"] else 0


def _run_snapshot(args: argparse.Namespace) -> int:
    if not args.start_date or not args.end_date:
        raise SystemExit("--start-date and --end-date are required")
    if not args.csv and not args.property_folder:
        raise SystemExit("Provide --csv or --property-folder")

    result = run_snapshot(
        start_date=args.start_date,
        end_date=args.end_date,
        csv_path=args.csv,
        property_id=args.property_id,
        property_name=args.property_name,
        date_basis=args.date_basis,
        breakdown_by=args.breakdown_by,
        bands=args.bands,
        inventory_listings=args.inventory_listings,
        property_folder=args.property_folder,
        data_root=args.data_root,
        yoy_compare_start_date=args.yoy_compare_start_date,
        yoy_compare_end_date=args.yoy_compare_end_date,
    )

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(
            multi_snapshot_to_text(
                portfolio=result.portfolio,
                listings=result.breakdown,
                invalid_row_count=result.invalid_rows_skipped,
                breakdown_label=result.breakdown_by.capitalize(),
            )
        )
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "sync":
        return _run_sync(args)
    if args.command == "run":
        return _run_snapshot(args)
    if args.start_date and args.end_date and (args.csv or args.property_folder):
        return _run_snapshot(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
