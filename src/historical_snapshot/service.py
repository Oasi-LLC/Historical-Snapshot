from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from historical_snapshot.config import (
    InventoryMode,
    PropertyConfig,
    list_property_configs,
    load_property_config,
    load_property_config_by_id,
    resolve_property_config,
)
from historical_snapshot.core.bands import DEFAULT_BANDS, Band, parse_bands
from historical_snapshot.core.metrics import (
    DateBasis,
    SnapshotMetrics,
    build_booking_details,
    build_calendar_details,
    build_night_details,
    comparable_live_listings,
    compute_portfolio_snapshot_metrics,
    compute_snapshot_metrics,
    count_active_listings,
    count_live_listings_in_scope,
    listing_available_nights_in_window,
    total_available_room_nights,
)
from historical_snapshot.core.aggregations import aggregate_blended, aggregate_by_period
from historical_snapshot.core.snapshot import _decimal_to_float, snapshot_to_dict
from historical_snapshot.io.google_sheets import (
    read_sync_metadata,
    sheets_cache_path,
    sheets_meta_path,
    sync_all_properties,
)
from historical_snapshot.io.sync_policy import ensure_daily_sync as _ensure_daily_sync
from historical_snapshot.io.property_reader import read_property_bookings, resolve_property_data_path
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
    listing_inventory: dict[str, int] | None = None,
    comparable_listings: frozenset[str] | None = None,
) -> int:
    if inventory_mode == "live_listings" and listing_live_dates:
        return count_live_listings_in_scope(
            listing_live_dates,
            start_date,
            end_date,
            listings=comparable_listings,
            listing_inventory=listing_inventory,
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
    listing_inventory: dict[str, int] | None = None,
    listing_name: str | None = None,
    comparable_listings: frozenset[str] | None = None,
    force_include_listings: frozenset[str] | None = None,
) -> int | None:
    if inventory_mode == "live_listings" and listing_live_dates:
        if listing_name is not None:
            forced = force_include_listings is not None and listing_name in force_include_listings
            if comparable_listings is not None and listing_name not in comparable_listings and not forced:
                return 0
            live_date = listing_live_dates.get(listing_name)
            if live_date is None:
                return 0
            units = (listing_inventory or {}).get(listing_name, 1)
            return (
                listing_available_nights_in_window(live_date, start_date, end_date) * units
            )
        listings = comparable_listings if comparable_listings is not None else None
        return total_available_room_nights(
            listing_live_dates,
            start_date,
            end_date,
            listings=listings,
            listing_inventory=listing_inventory,
        )
    return None


def _property_entry(
    property_config: PropertyConfig,
    *,
    data_root: Path,
    csv_path: Path | None = None,
) -> dict:
    data_source_type = "csv"
    last_synced_at = None
    if property_config.data_source and property_config.data_source.is_google_sheets:
        data_source_type = "google_sheets"
        meta = read_sync_metadata(
            sheets_meta_path(property_config, data_root=data_root),
        )
        if meta:
            last_synced_at = meta.get("synced_at")
        cache_path = sheets_cache_path(property_config, data_root=data_root)
        if cache_path.is_file():
            csv_path = cache_path
    elif csv_path is None:
        data_dir = data_root / property_config.folder
        if data_dir.is_dir():
            csv_files = sorted(data_dir.glob("*.csv"))
            if csv_files:
                csv_path = csv_files[0]

    entry: dict = {
        "id": property_config.property_id,
        "name": property_config.property_name,
        "folder": property_config.folder,
        "data_dir": str(data_root / property_config.folder),
        "inventory_mode": property_config.inventory_mode,
        "default_inventory_listings": property_config.default_inventory_listings,
        "pms": property_config.pms,
        "data_source": data_source_type,
        "config": property_config.to_dict(),
    }
    if csv_path is not None:
        entry["csv_path"] = str(csv_path)
        data_dir = data_root / property_config.folder
        if data_dir.is_dir():
            entry["csv_files"] = [str(path) for path in sorted(data_dir.glob("*.csv"))]
        else:
            entry["csv_files"] = [str(csv_path)]
    if last_synced_at:
        entry["last_synced_at"] = last_synced_at
    return entry


def discover_properties(data_root: Path | str = DEFAULT_DATA_ROOT) -> list[dict]:
    root = Path(data_root)
    properties: list[dict] = []
    seen_folders: set[str] = set()

    for property_config in list_property_configs():
        seen_folders.add(property_config.folder.lower())
        csv_path: Path | None = None
        if property_config.data_source and property_config.data_source.is_google_sheets:
            cache_path = sheets_cache_path(property_config, data_root=root)
            if cache_path.is_file():
                csv_path = cache_path
        else:
            data_dir = root / property_config.folder
            if data_dir.is_dir():
                csv_files = sorted(data_dir.glob("*.csv"))
                if csv_files:
                    csv_path = csv_files[0]

        if csv_path is not None or (
            property_config.data_source and property_config.data_source.is_google_sheets
        ):
            properties.append(
                _property_entry(property_config, data_root=root, csv_path=csv_path)
            )

    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            if folder.name.lower() in seen_folders:
                continue
            csv_files = sorted(folder.glob("*.csv"))
            if not csv_files:
                continue

            property_config = load_property_config(folder.name)
            if property_config:
                properties.append(
                    _property_entry(property_config, data_root=root, csv_path=csv_files[0])
                )
            else:
                properties.append(
                    {
                        "id": folder.name.upper(),
                        "name": folder.name.replace("_", " ").title(),
                        "folder": folder.name,
                        "data_dir": str(folder),
                        "csv_path": str(csv_files[0]),
                        "csv_files": [str(path) for path in csv_files],
                        "inventory_mode": "manual",
                        "default_inventory_listings": None,
                        "data_source": "csv",
                    }
                )

    return sorted(properties, key=lambda item: item["name"].lower())


def _sync_result_to_dict(result) -> dict:
    return {
        "properties": [
            {
                "folder": item.folder,
                "property_id": item.property_id,
                "tab": item.tab,
                "cache_path": str(item.cache_path),
                "row_count": item.row_count,
                "synced_at": item.synced_at,
            }
            for item in result.properties
        ],
        "errors": [{"folder": folder, "error": error} for folder, error in result.errors],
    }


def sync_properties(
    *,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    property_folder: str | None = None,
) -> dict:
    result = sync_all_properties(data_root=data_root, property_folder=property_folder)
    return _sync_result_to_dict(result)


def ensure_daily_sync(
    *,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    timezone: str | None = None,
    force: bool = False,
) -> dict | None:
    from historical_snapshot.io.sync_policy import sync_timezone

    tz = sync_timezone(timezone)
    result = _ensure_daily_sync(data_root=data_root, tz=tz, force=force)
    if result is None:
        return None
    return _sync_result_to_dict(result)


def resolve_snapshot_csv_path(
    *,
    csv_path: str | Path | None = None,
    property_folder: str | None = None,
    property_id: str | None = None,
    data_root: Path | str = DEFAULT_DATA_ROOT,
) -> tuple[Path, PropertyConfig | None]:
    property_config: PropertyConfig | None = None
    if property_folder:
        property_config = load_property_config(property_folder)
    elif property_id:
        property_config = load_property_config_by_id(property_id)
    elif csv_path is not None:
        property_config, _ = resolve_property_config(csv_path)

    if property_config is None:
        if csv_path is None:
            raise ValueError("csv_path or property_folder/property_id is required")
        path = Path(csv_path)
        if not path.is_file():
            raise ValueError(f"CSV not found: {csv_path}")
        return path, None

    resolved = resolve_property_data_path(
        property_config,
        csv_path,
        data_root=data_root,
    )
    return resolved, property_config


def list_listings(
    csv_path: str | Path | None = None,
    *,
    property_folder: str | None = None,
    property_id: str | None = None,
    data_root: Path | str = DEFAULT_DATA_ROOT,
) -> list[str]:
    resolved_path, property_config = resolve_snapshot_csv_path(
        csv_path=csv_path,
        property_folder=property_folder,
        property_id=property_id,
        data_root=data_root,
    )
    records, _ = read_property_bookings(
        resolved_path,
        property_config=property_config,
        data_root=data_root,
    )
    return sorted({r.listing_name or r.property_id for r in records if r.listing_name or r.property_id})


def run_snapshot(
    start_date: str | date,
    end_date: str | date,
    *,
    csv_path: str | Path | None = None,
    property_id: str = "LAFAVE",
    property_name: str = "LaFave",
    date_basis: DateBasis = "stay",
    breakdown_by: str | None = None,
    bands: str | list[Band] = DEFAULT_BANDS,
    inventory_listings: int = 30,
    inventory_mode: InventoryMode | None = None,
    property_folder: str | None = None,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    as_of_date: str | date | None = None,
    yoy_compare_start_date: str | date | None = None,
    yoy_compare_end_date: str | date | None = None,
    include_listings_in_breakdown: list[str] | tuple[str, ...] | frozenset[str] | None = None,
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

    # Listings named here are always retained in breakdown_snapshots (and get a
    # real available-room-nights figure) even if the YoY comparable_listings
    # filter would otherwise exclude them - e.g. a cross-listing compare
    # involving a brand-new unit with no prior-year data. Portfolio-level
    # inventory/occupancy math (which uses comparable_listings directly) is
    # untouched; this only widens which listings survive into the per-listing
    # breakdown.
    forced_listings: frozenset[str] | None = (
        frozenset(str(name) for name in include_listings_in_breakdown)
        if include_listings_in_breakdown
        else None
    )

    if property_folder:
        property_config = load_property_config(property_folder)
    elif csv_path is not None:
        property_config, _pms_profile = resolve_property_config(
            csv_path,
            property_id=property_id,
            property_name=property_name,
        )
    else:
        property_config = load_property_config_by_id(property_id)

    resolved_csv_path, resolved_config = resolve_snapshot_csv_path(
        csv_path=csv_path,
        property_folder=property_folder or (property_config.folder if property_config else None),
        property_id=property_id if property_config is None else None,
        data_root=data_root,
    )
    if property_config is None:
        property_config = resolved_config
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
        resolved_csv_path,
        property_config=property_config,
        data_root=data_root,
    )

    live_dates = property_config.listing_live_dates if property_config else {}
    listing_inventory = property_config.listing_inventory if property_config else {}
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
        listing_inventory=listing_inventory or None,
        comparable_listings=comparable_listings,
    )
    portfolio_available_nights = resolve_available_room_nights(
        start,
        end,
        inventory_mode=inventory_mode,
        inventory_units=portfolio_inventory,
        listing_live_dates=live_dates or None,
        listing_inventory=listing_inventory or None,
        comparable_listings=comparable_listings,
    )

    portfolio_nd = build_night_details(portfolio_records, start, end, date_basis=date_basis, as_of_date=as_of)
    portfolio_bd = build_booking_details(portfolio_records, start, end, date_basis=date_basis, as_of_date=as_of)
    portfolio_cd = build_calendar_details(portfolio_nd, start, end, listing_live_dates=live_dates or None)

    portfolio = aggregate_blended(
        booking_details=portfolio_bd,
        night_details=portfolio_nd,
        calendar_details=portfolio_cd,
        property_id="__portfolio__",
        property_name=property_name,
        start_date=start,
        end_date=end,
        bands=parsed_bands,
        available_room_nights=portfolio_available_nights,
        inventory_units=portfolio_inventory,
        as_of_date=as_of,
    )

    breakdown_source = portfolio_records if comparable_listings is not None else records
    if forced_listings and comparable_listings is not None:
        # portfolio_records already dropped these listings' raw rows entirely -
        # pull them back in from the unfiltered records so their breakdown
        # snapshot has real bookings to aggregate, not zero.
        already_ids = {(r.listing_name or r.property_id) for r in breakdown_source}
        missing_ids = forced_listings - already_ids
        if missing_ids:
            breakdown_source = list(breakdown_source) + [
                r for r in records if (r.listing_name or r.property_id) in missing_ids
            ]
    breakdown_records = records_for_breakdown(breakdown_source, breakdown_by)
    bucket_ids = sorted({r.property_id for r in breakdown_records})
    if comparable_listings is not None and breakdown_by == "listing":
        bucket_ids = sorted(
            name
            for name in bucket_ids
            if name in comparable_listings or (forced_listings and name in forced_listings)
        )
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
            listing_inventory=listing_inventory or None,
            listing_name=bucket_id if breakdown_by == "listing" else None,
            comparable_listings=comparable_listings,
            force_include_listings=forced_listings,
        )

    breakdown_snapshots: list[SnapshotMetrics] = []
    for bucket_id in bucket_ids:
        bucket_records = [r for r in breakdown_records if r.property_id == bucket_id]
        b_nd = build_night_details(bucket_records, start, end, date_basis=date_basis, as_of_date=as_of)
        b_bd = build_booking_details(bucket_records, start, end, date_basis=date_basis, as_of_date=as_of)
        b_cd = build_calendar_details(
            b_nd, start, end,
            listing_live_dates=live_dates or None,
            listings=[bucket_id] if breakdown_by == "listing" else None,
        )
        snap = aggregate_blended(
            booking_details=b_bd,
            night_details=b_nd,
            calendar_details=b_cd,
            property_id=bucket_id,
            property_name=property_name,
            start_date=start,
            end_date=end,
            bands=parsed_bands,
            available_room_nights=breakdown_available_nights(bucket_id),
            inventory_units=breakdown_inventory_units(bucket_id),
            as_of_date=as_of,
        )
        if snap.bookings_count > 0:
            breakdown_snapshots.append(snap)

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


def run_listing_ramp_series(
    listing_name: str,
    *,
    property_folder: str | None = None,
    property_id: str | None = None,
    csv_path: str | Path | None = None,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    date_basis: DateBasis = "stay",
    period: str = "monthly",
    horizon_months: int = 18,
) -> list[dict] | None:
    """Monthly rollup for one listing from its go-live date through a capped horizon.

    Built via a single aggregate_by_period() pass over that listing's own detail
    tables (not N per-month snapshot calls) - one read + one build_night_details/
    build_booking_details/build_calendar_details pass over the whole ramp window,
    then one aggregation call.

    Capped to a fixed since-go-live window (default 18 months), not a rolling
    most-recent window and not unbounded through today - the point of a ramp
    view is launch trajectory, not ongoing performance, so an old listing
    doesn't produce years of rows.

    Returns None if the listing has no known go-live date (nothing to build a
    ramp from) rather than raising, so a caller comparing several listings can
    skip the ones without one.
    """
    resolved_csv_path, property_config = resolve_snapshot_csv_path(
        csv_path=csv_path,
        property_folder=property_folder,
        property_id=property_id,
        data_root=data_root,
    )
    if property_config is None:
        return None

    live_dates = property_config.listing_live_dates or {}
    go_live = live_dates.get(listing_name)
    if go_live is None:
        return None

    today = date.today()
    horizon_end = go_live + timedelta(days=30 * horizon_months)
    end = min(today, horizon_end)
    if end < go_live:
        return None

    records, _issues = read_property_bookings(
        resolved_csv_path,
        property_config=property_config,
        data_root=data_root,
    )
    listing_records = [r for r in records if (r.listing_name or r.property_id) == listing_name]

    night_details = build_night_details(listing_records, go_live, end, date_basis=date_basis)
    booking_details = build_booking_details(listing_records, go_live, end, date_basis=date_basis)
    calendar_details = build_calendar_details(
        night_details,
        go_live,
        end,
        listing_live_dates=live_dates or None,
        listings=[listing_name],
    )

    periods = aggregate_by_period(
        night_details,
        booking_details,
        calendar_details,
        period=period,
        start_date=go_live,
        end_date=end,
    )
    return [
        {
            "period": p.period_label,
            "start_date": p.start_date.isoformat(),
            "end_date": p.end_date.isoformat(),
            "bookings_count": p.bookings_count,
            "room_nights_sold": p.room_nights_sold,
            "room_revenue": _decimal_to_float(p.room_revenue),
            "adr": _decimal_to_float(p.adr_weighted),
            "occupancy_pct": _decimal_to_float(p.occupancy_pct),
            "revpar": _decimal_to_float(p.revpar),
        }
        for p in periods
    ]
