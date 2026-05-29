from __future__ import annotations

import argparse
import json

from historical_snapshot.core.snapshot import multi_snapshot_to_text
from historical_snapshot.service import run_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snapshot",
        description="Get property-level historical performance snapshots from CSV data.",
    )
    parser.add_argument("--csv", required=True, help="Path to booking CSV export")
    parser.add_argument("--property-id", default="LAFAVE", help="Property ID label for output")
    parser.add_argument("--property-name", default="LaFave", help="Property name label for output")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
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
        default="0-7,8-15,16-30,31-60,61+",
        help="Booking window bands, e.g. '0-7,8-15,16-30,31-60,61+'",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_snapshot(
        csv_path=args.csv,
        start_date=args.start_date,
        end_date=args.end_date,
        property_id=args.property_id,
        property_name=args.property_name,
        date_basis=args.date_basis,
        breakdown_by=args.breakdown_by,
        bands=args.bands,
        inventory_listings=args.inventory_listings,
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


if __name__ == "__main__":
    raise SystemExit(main())
