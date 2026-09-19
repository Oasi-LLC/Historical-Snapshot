"""Golden-master parity test: old compute_snapshot_metrics vs new aggregate_blended.

Runs both code paths over real Flohom data and asserts field-by-field equality.
This gates the refactor — if these fail, the new path diverges from production.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from historical_snapshot.config import load_property_config
from historical_snapshot.core.bands import DEFAULT_BANDS, parse_bands
from historical_snapshot.core.metrics import (
    compute_portfolio_snapshot_metrics,
    compute_snapshot_metrics,
    build_night_details,
    build_booking_details,
    build_calendar_details,
    SnapshotMetrics,
)
from historical_snapshot.core.aggregations import aggregate_blended
from historical_snapshot.io.property_reader import read_property_bookings, resolve_property_data_path
from historical_snapshot.service import (
    records_for_breakdown,
    resolve_available_room_nights,
    resolve_inventory_units,
)

DATA_ROOT = Path("data")
BANDS = parse_bands(DEFAULT_BANDS)


def _load_flohom():
    config = load_property_config("flohom")
    assert config is not None
    path = resolve_property_data_path(config, data_root="data")
    records, _ = read_property_bookings(path, property_config=config, data_root="data")
    return config, records


def _assert_snapshot_equal(old: SnapshotMetrics, new: SnapshotMetrics):
    """Assert all fields match between old and new SnapshotMetrics."""
    assert old.property_id == new.property_id
    assert old.property_name == new.property_name
    assert old.start_date == new.start_date
    assert old.end_date == new.end_date
    assert old.bookings_count == new.bookings_count, f"bookings: {old.bookings_count} vs {new.bookings_count}"
    assert old.room_nights_sold == new.room_nights_sold, f"nights: {old.room_nights_sold} vs {new.room_nights_sold}"
    assert old.room_revenue == new.room_revenue, f"rev: {old.room_revenue} vs {new.room_revenue}"
    assert old.adr == new.adr, f"adr: {old.adr} vs {new.adr}"
    assert old.occupancy_pct == new.occupancy_pct, f"occ: {old.occupancy_pct} vs {new.occupancy_pct}"
    assert old.revpar == new.revpar, f"revpar: {old.revpar} vs {new.revpar}"
    assert old.average_los == new.average_los, f"los: {old.average_los} vs {new.average_los}"

    assert old.booking_window_mean_days == new.booking_window_mean_days
    assert old.booking_window_median_days == new.booking_window_median_days

    assert old.pickup_bookings_by_band == new.pickup_bookings_by_band
    assert old.pickup_room_nights_by_band == new.pickup_room_nights_by_band
    for band in old.pickup_revenue_by_band:
        assert old.pickup_revenue_by_band[band] == new.pickup_revenue_by_band.get(band), f"pickup rev {band}"

    assert old.los_distribution == new.los_distribution
    assert old.arrival_day_of_week_mix == new.arrival_day_of_week_mix

    for ch in old.channel_mix:
        assert ch in new.channel_mix, f"channel {ch} missing"
        for k in ("bookings_count", "room_nights", "revenue", "adr", "revenue_share_pct", "nights_share_pct"):
            assert old.channel_mix[ch].get(k) == new.channel_mix[ch].get(k), f"channel {ch}/{k}: {old.channel_mix[ch].get(k)} vs {new.channel_mix[ch].get(k)}"


@pytest.fixture(scope="module")
def flohom_data():
    return _load_flohom()


SCENARIOS = [
    ("oct_2025", date(2025, 10, 1), date(2025, 10, 31), None),
    ("jul_2026", date(2026, 7, 1), date(2026, 7, 31), None),
    ("oct_2026_pace", date(2026, 10, 1), date(2026, 10, 31), date(2026, 8, 18)),
]


@pytest.mark.parametrize("name,start,end,as_of", SCENARIOS)
def test_portfolio_parity(flohom_data, name, start, end, as_of):
    config, records = flohom_data
    live_dates = config.listing_live_dates
    listing_inventory = config.listing_inventory

    available_rn = resolve_available_room_nights(
        start, end,
        inventory_mode=config.inventory_mode,
        inventory_units=None,
        listing_live_dates=live_dates or None,
        listing_inventory=listing_inventory or None,
    )
    inv_units = resolve_inventory_units(
        records, start, end,
        inventory_mode=config.inventory_mode,
        manual_units=config.default_inventory_listings,
        listing_live_dates=live_dates or None,
        listing_inventory=listing_inventory or None,
    )

    old = compute_portfolio_snapshot_metrics(
        records=records,
        property_name="Flohom",
        start_date=start,
        end_date=end,
        bands=BANDS,
        date_basis="stay",
        inventory_units=inv_units,
        available_room_nights=available_rn,
        as_of_date=as_of,
    )

    booking_details = build_booking_details(records, start, end, date_basis="stay", as_of_date=as_of)
    night_details = build_night_details(records, start, end, date_basis="stay", as_of_date=as_of)
    calendar_details = build_calendar_details(night_details, start, end, listing_live_dates=live_dates)

    new = aggregate_blended(
        booking_details=booking_details,
        night_details=night_details,
        calendar_details=calendar_details,
        property_id="__portfolio__",
        property_name="Flohom",
        start_date=start,
        end_date=end,
        bands=BANDS,
        available_room_nights=available_rn,
        inventory_units=inv_units,
        as_of_date=as_of,
    )

    _assert_snapshot_equal(old, new)


@pytest.mark.parametrize("name,start,end,as_of", SCENARIOS)
def test_listing_breakdown_parity(flohom_data, name, start, end, as_of):
    config, records = flohom_data
    live_dates = config.listing_live_dates
    listing_inventory = config.listing_inventory

    breakdown_records = records_for_breakdown(records, "listing")
    bucket_ids = sorted({r.property_id for r in breakdown_records})

    for bucket_id in bucket_ids[:3]:
        inv = (listing_inventory or {}).get(bucket_id, 1)
        arn = resolve_available_room_nights(
            start, end,
            inventory_mode=config.inventory_mode,
            inventory_units=inv,
            listing_live_dates=live_dates or None,
            listing_inventory=listing_inventory or None,
            listing_name=bucket_id,
        )

        old = compute_snapshot_metrics(
            records=breakdown_records,
            property_id=bucket_id,
            start_date=start,
            end_date=end,
            bands=BANDS,
            date_basis="stay",
            inventory_units=inv,
            available_room_nights=arn,
            as_of_date=as_of,
        )

        if old.bookings_count == 0:
            continue

        bucket_records = [r for r in breakdown_records if r.property_id == bucket_id]
        bd = build_booking_details(bucket_records, start, end, date_basis="stay", as_of_date=as_of)
        nd = build_night_details(bucket_records, start, end, date_basis="stay", as_of_date=as_of)
        cd = build_calendar_details(nd, start, end, listing_live_dates=live_dates)

        new = aggregate_blended(
            booking_details=bd,
            night_details=nd,
            calendar_details=cd,
            property_id=bucket_id,
            property_name=old.property_name,
            start_date=start,
            end_date=end,
            bands=BANDS,
            available_room_nights=arn,
            inventory_units=inv,
            as_of_date=as_of,
        )

        _assert_snapshot_equal(old, new)
