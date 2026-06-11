from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from historical_snapshot.config import (
    InventoryMode,
    PropertyConfig,
    load_pms_profile,
    load_property_config,
    resolve_property_config,
)
from historical_snapshot.core.bands import DEFAULT_BANDS, Band, parse_bands
from historical_snapshot.core.metrics import (
    DateBasis,
    SnapshotMetrics,
    comparable_live_listings,
    compute_portfolio_snapshot_metrics,
    compute_snapshot_metrics,
    count_active_listings,
    count_live_listings_in_scope,
    listing_available_nights_in_window,
    total_available_room_nights,
)
from historical_snapshot.core.snapshot import snapshot_to_dict
from historical_snapshot.io.property_reader import read_property_bookings
from historical_snapshot.models import BookingRecord, ValidationIssue

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
    inventory_mode: InventoryMode = "manual"
    inventory_units_used: int | None = None
    comparable_listings_used: tuple[str, ...] | None = None

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
            "inventory_mode": self.inventory_mode,
            "inventory_units_used": self.inventory_units_used,
            "comparable_listings_used": list(self.comparable_listings_used or ()),
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


def grouping_inventory_counts(
    records: list[BookingRecord],
    start_date: date,
    end_date: date,
    *,
    inventory_mode: InventoryMode,
) -> dict[str, int]:
    listings_by_group: dict[str, set[str]] = {}
    for record in records:
        group = record.grouping or "Ungrouped"
        listing = record.listing_name or record.property_id
        listings_by_group.setdefault(group, set()).add(listing)

    if inventory_mode == "active_listings":
        counts: dict[str, int] = {}
        for group in listings_by_group:
            group_records = [r for r in records if (r.grouping or "Ungrouped") == group]
            counts[group] = count_active_listings(group_records, start_date, end_date)
        return counts

    return {group: max(len(listings), 1) for group, listings in listings_by_group.items()}


def resolve_inventory_units(
    records: list[BookingRecord],
    start_date: date,
    end_date: date,
    *,
    inventory_mode: InventoryMode,
    manual_units: int | None,
    listing_live_dates: dict[str, date] | None = None,
    comparable_listings: frozenset[str] | None = None,
) -> int:
    if inventory_mode == "live_listings" and listing_live_dates:
        return count_live_listings_in_scope(
            listing_live_dates,
            start_date,
            end_date,
            listings=comparable_listings,
        )
    if inventory_mode == "active_listings":
        return count_active_listings(records, start_date, end_date)
    if manual_units is not None:
        return manual_units
    return 1


def resolve_available_room_nights(
    start_date: date,
    end_date: date,
    *,
    inventory_mode: InventoryMode,
    inventory_units: int | None,
    listing_live_dates: dict[str, date] | None = None,
    listing_name: str | None = None,
    comparable_listings: frozenset[str] | None = None,
) -> int | None:
    if inventory_mode == "live_listings" and listing_live_dates:
        if listing_name is not None:
            if comparable_listings is not None and listing_name not in comparable_listings:
                return 0
            live_date = listing_live_dates.get(listing_name)
            if live_date is None:
                return 0
            return listing_available_nights_in_window(live_date, start_date, end_date)
        listings = comparable_listings if comparable_listings is not None else None
        return total_available_room_nights(
            listing_live_dates, start_date, end_date, listings=listings
        )
    return None


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

        property_config = load_property_config(folder.name)
        if property_config:
            prop_id = property_config.property_id
            prop_name = property_config.property_name
            inventory_mode = property_config.inventory_mode
            default_inventory = property_config.default_inventory_listings
            pms = property_config.pms
        else:
            prop_id = folder.name.upper()
            prop_name = folder.name.replace("_", " ").title()
            inventory_mode = "manual"
            default_inventory = None
            pms = None

        entry: dict = {
            "id": prop_id,
            "name": prop_name,
            "data_dir": str(folder),
            "csv_path": str(csv_files[0]),
            "csv_files": [str(p) for p in csv_files],
            "inventory_mode": inventory_mode,
            "default_inventory_listings": default_inventory,
        }
        if pms:
            entry["pms"] = pms
        if property_config:
            entry["config"] = property_config.to_dict()
        properties.append(entry)
    return properties


def list_listings(csv_path: str | Path) -> list[str]:
    property_config, pms_profile = resolve_property_config(csv_path)
    records, _ = read_property_bookings(
        csv_path,
        property_config=property_config,
    )
    return sorted({r.listing_name or r.property_id for r in records if r.listing_name or r.property_id})


def run_snapshot(
    csv_path: str | Path,
    start_date: str | date,
    end_date: str | date,
    *,
    property_id: str = "LAFAVE",
    property_name: str = "LaFave",
    date_basis: DateBasis = "stay",
    breakdown_by: str | None = None,
    bands: str | list[Band] = DEFAULT_BANDS,
    inventory_listings: int = 30,
    inventory_mode: InventoryMode | None = None,
    property_folder: str | None = None,
    as_of_date: str | date | None = None,
    yoy_compare_start_date: str | date | None = None,
    yoy_compare_end_date: str | date | None = None,
) -> SnapshotResult:
    start = parse_date(start_date)
    end = parse_date(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    as_of = parse_date(as_of_date) if as_of_date is not None else None
    yoy_compare_start = (
        parse_date(yoy_compare_start_date) if yoy_compare_start_date is not None else None
    )
    yoy_compare_end = (
        parse_date(yoy_compare_end_date) if yoy_compare_end_date is not None else None
    )
    if (yoy_compare_start is None) ^ (yoy_compare_end is None):
        raise ValueError("yoy_compare_start_date and yoy_compare_end_date must both be set")

    if property_folder:
        property_config = load_property_config(property_folder)
    else:
        property_config, _pms_profile = resolve_property_config(
            csv_path,
            property_id=property_id,
            property_name=property_name,
        )
    if property_config:
        property_id = property_config.property_id
        property_name = property_config.property_name
        if breakdown_by is None:
            breakdown_by = property_config.defaults.get("breakdown_by", "listing")
        if inventory_mode is None:
            inventory_mode = property_config.inventory_mode
        if inventory_mode == "manual" and property_config.default_inventory_listings is not None:
            inventory_listings = property_config.default_inventory_listings
    if inventory_mode is None:
        inventory_mode = "manual"
    if breakdown_by is None:
        breakdown_by = "listing"

    parsed_bands = bands if isinstance(bands, list) else parse_bands(bands)
    records, issues = read_property_bookings(
        csv_path,
        property_config=property_config,
    )

    live_dates = property_config.listing_live_dates if property_config else {}
    comparable_listings: frozenset[str] | None = None
    if (
        inventory_mode == "live_listings"
        and live_dates
        and yoy_compare_start is not None
        and yoy_compare_end is not None
    ):
        comparable_listings = comparable_live_listings(
            live_dates, start, end, yoy_compare_start, yoy_compare_end
        )

    portfolio_records = records
    if comparable_listings is not None:
        portfolio_records = [
            record for record in records if record.listing_name in comparable_listings
        ]

    portfolio_inventory = resolve_inventory_units(
        portfolio_records,
        start,
        end,
        inventory_mode=inventory_mode,
        manual_units=inventory_listings,
        listing_live_dates=live_dates or None,
        comparable_listings=comparable_listings,
    )
    portfolio_available_nights = resolve_available_room_nights(
        start,
        end,
        inventory_mode=inventory_mode,
        inventory_units=portfolio_inventory,
        listing_live_dates=live_dates or None,
        comparable_listings=comparable_listings,
    )

    portfolio = compute_portfolio_snapshot_metrics(
        records=portfolio_records,
        property_name=property_name,
        start_date=start,
        end_date=end,
        bands=parsed_bands,
        date_basis=date_basis,
        inventory_units=portfolio_inventory,
        available_room_nights=portfolio_available_nights,
        as_of_date=as_of,
    )
    breakdown_source = portfolio_records if comparable_listings is not None else records
    breakdown_records = records_for_breakdown(breakdown_source, breakdown_by)
    bucket_ids = sorted({r.property_id for r in breakdown_records})
    if comparable_listings is not None and breakdown_by == "listing":
        bucket_ids = sorted(name for name in bucket_ids if name in comparable_listings)
    inventory_by_group = (
        grouping_inventory_counts(
            records, start, end, inventory_mode=inventory_mode
        )
        if breakdown_by == "grouping"
        else {}
    )

    def breakdown_inventory_units(bucket_id: str) -> int:
        if breakdown_by == "listing":
            if property_config and property_config.listing_inventory:
                return property_config.listing_inventory.get(bucket_id, 1)
            return 1
        if property_config and property_config.grouping_inventory:
            return property_config.grouping_inventory.get(bucket_id, 1)
        return inventory_by_group.get(bucket_id, 1)

    def breakdown_available_nights(bucket_id: str) -> int | None:
        return resolve_available_room_nights(
            start,
            end,
            inventory_mode=inventory_mode,
            inventory_units=breakdown_inventory_units(bucket_id),
            listing_live_dates=live_dates or None,
            listing_name=bucket_id if breakdown_by == "listing" else None,
            comparable_listings=comparable_listings,
        )

    breakdown_snapshots = [
        compute_snapshot_metrics(
            records=breakdown_records,
            property_id=bucket_id,
            start_date=start,
            end_date=end,
            bands=parsed_bands,
            date_basis=date_basis,
            inventory_units=breakdown_inventory_units(bucket_id),
            available_room_nights=breakdown_available_nights(bucket_id),
            as_of_date=as_of,
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
        inventory_mode=inventory_mode,
        inventory_units_used=portfolio_inventory,
        comparable_listings_used=(
            tuple(sorted(comparable_listings)) if comparable_listings is not None else None
        ),
    )
