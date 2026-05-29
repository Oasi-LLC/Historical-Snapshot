from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from historical_snapshot.core.bands import Band, parse_bands
from historical_snapshot.core.metrics import (
    DateBasis,
    SnapshotMetrics,
    compute_portfolio_snapshot_metrics,
    compute_snapshot_metrics,
)
from historical_snapshot.core.snapshot import snapshot_to_dict
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.models import BookingRecord, ValidationIssue

DEFAULT_BANDS = "0-7,8-15,16-30,31-60,61+"
DEFAULT_DATA_ROOT = Path("data")


@dataclass(frozen=True)
class SnapshotResult:
    property_id: str
    property_name: str
    breakdown_by: str
    portfolio: SnapshotMetrics
    breakdown: list[SnapshotMetrics]
    invalid_rows_skipped: int
    validation_issues: list[ValidationIssue]

    @property
    def portfolio_snapshot(self) -> dict:
        return snapshot_to_dict(self.portfolio)

    @property
    def breakdown_snapshots(self) -> list[dict]:
        return [snapshot_to_dict(item) for item in self.breakdown]

    def to_dict(self) -> dict:
        return {
            "property": {"id": self.property_id, "name": self.property_name},
            "breakdown_by": self.breakdown_by,
            "portfolio_snapshot": self.portfolio_snapshot,
            "breakdown_snapshots": self.breakdown_snapshots,
            "invalid_rows_skipped": self.invalid_rows_skipped,
        }


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def records_for_breakdown(records: list[BookingRecord], breakdown_by: str) -> list[BookingRecord]:
    remapped: list[BookingRecord] = []
    for record in records:
        if breakdown_by == "grouping":
            bucket_id = record.grouping or "Ungrouped"
        else:
            bucket_id = record.listing_name or record.property_id
        remapped.append(
            BookingRecord(
                property_id=bucket_id,
                property_name=record.property_name,
                listing_name=record.listing_name,
                channel=record.channel,
                grouping=record.grouping,
                reservation_date=record.reservation_date,
                booking_date=record.booking_date,
                check_in_date=record.check_in_date,
                check_out_date=record.check_out_date,
                room_revenue=record.room_revenue,
                room_nights=record.room_nights,
                status=record.status,
                available_room_nights=record.available_room_nights,
            )
        )
    return remapped


def grouping_inventory_counts(records: list[BookingRecord]) -> dict[str, int]:
    listings_by_group: dict[str, set[str]] = {}
    for record in records:
        group = record.grouping or "Ungrouped"
        listing = record.listing_name or record.property_id
        listings_by_group.setdefault(group, set()).add(listing)
    return {group: max(len(listings), 1) for group, listings in listings_by_group.items()}


def discover_properties(data_root: Path | str = DEFAULT_DATA_ROOT) -> list[dict]:
    root = Path(data_root)
    if not root.is_dir():
        return []
    properties: list[dict] = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        csv_files = sorted(folder.glob("*.csv"))
        if not csv_files:
            continue
        properties.append(
            {
                "id": folder.name.upper(),
                "name": folder.name.replace("_", " ").title(),
                "data_dir": str(folder),
                "csv_path": str(csv_files[0]),
                "csv_files": [str(p) for p in csv_files],
            }
        )
    return properties


def list_listings(csv_path: str | Path) -> list[str]:
    records, _ = read_bookings_csv(csv_path)
    return sorted({r.listing_name or r.property_id for r in records if r.listing_name or r.property_id})


def run_snapshot(
    csv_path: str | Path,
    start_date: str | date,
    end_date: str | date,
    *,
    property_id: str = "LAFAVE",
    property_name: str = "LaFave",
    date_basis: DateBasis = "stay",
    breakdown_by: str = "listing",
    bands: str | list[Band] = DEFAULT_BANDS,
    inventory_listings: int = 30,
) -> SnapshotResult:
    start = parse_date(start_date)
    end = parse_date(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")

    parsed_bands = bands if isinstance(bands, list) else parse_bands(bands)
    records, issues = read_bookings_csv(csv_path)

    portfolio = compute_portfolio_snapshot_metrics(
        records=records,
        property_name=property_name,
        start_date=start,
        end_date=end,
        bands=parsed_bands,
        date_basis=date_basis,
        inventory_units=inventory_listings,
    )
    breakdown_records = records_for_breakdown(records, breakdown_by)
    bucket_ids = sorted({r.property_id for r in breakdown_records})
    inventory_by_group = grouping_inventory_counts(records) if breakdown_by == "grouping" else {}

    def breakdown_inventory_units(bucket_id: str) -> int:
        if breakdown_by == "listing":
            return 1
        return inventory_by_group.get(bucket_id, 1)

    breakdown_snapshots = [
        compute_snapshot_metrics(
            records=breakdown_records,
            property_id=bucket_id,
            start_date=start,
            end_date=end,
            bands=parsed_bands,
            date_basis=date_basis,
            inventory_units=breakdown_inventory_units(bucket_id),
        )
        for bucket_id in bucket_ids
    ]
    breakdown_snapshots = [item for item in breakdown_snapshots if item.bookings_count > 0]

    return SnapshotResult(
        property_id=property_id,
        property_name=property_name,
        breakdown_by=breakdown_by,
        portfolio=portfolio,
        breakdown=breakdown_snapshots,
        invalid_rows_skipped=len(issues),
        validation_issues=issues,
    )
