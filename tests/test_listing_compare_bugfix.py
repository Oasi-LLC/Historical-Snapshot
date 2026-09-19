"""Tests for the listing-compare "new unit shows $0" bugfix.

Covers the two service.py additions used to fix it:
- resolve_available_room_nights(force_include_listings=...) no longer zeroes
  out a listing's available room nights just because it fails the
  comparable_live_listings check.
- run_snapshot(include_listings_in_breakdown=...) keeps a non-comparable
  listing's raw records (and therefore its breakdown snapshot) instead of
  silently dropping them via the YoY comparable_listings filter.
- run_listing_ramp_series() builds a monthly since-go-live rollup for one
  listing via a single aggregate_by_period() pass.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import historical_snapshot.service as service
from historical_snapshot.config import PropertyConfig
from historical_snapshot.models import BookingRecord
from historical_snapshot.service import resolve_available_room_nights, run_snapshot


def _booking(
    check_in: str,
    check_out: str,
    revenue: float,
    listing: str,
    reservation_date: str | None = None,
) -> BookingRecord:
    ci = date.fromisoformat(check_in)
    co = date.fromisoformat(check_out)
    rd = date.fromisoformat(reservation_date) if reservation_date else ci
    return BookingRecord(
        property_id=listing,
        property_name="Test Property",
        listing_name=listing,
        channel="Direct",
        grouping="",
        reservation_date=rd,
        booking_date=rd,
        check_in_date=ci,
        check_out_date=co,
        room_revenue=Decimal(str(revenue)),
        room_nights=(co - ci).days,
        status="confirmed",
    )


def _property_config(**overrides) -> PropertyConfig:
    base = dict(
        folder="TestProp",
        pms="test",
        property_id="TESTPROP",
        property_name="Test Property",
        inventory_mode="live_listings",
        listing_live_dates={
            "Old Unit": date(2020, 1, 1),
            "New Unit": date(2026, 6, 1),
        },
    )
    base.update(overrides)
    return PropertyConfig(**base)


# ---------------------------------------------------------------------------
# resolve_available_room_nights
# ---------------------------------------------------------------------------


def test_non_comparable_listing_zeroed_without_force_include():
    nights = resolve_available_room_nights(
        date(2026, 9, 1),
        date(2026, 9, 30),
        inventory_mode="live_listings",
        inventory_units=1,
        listing_live_dates={"New Unit": date(2026, 6, 1)},
        listing_name="New Unit",
        comparable_listings=frozenset(),  # New Unit not in the comparable set
    )
    assert nights == 0


def test_force_include_listings_bypasses_comparable_zeroing():
    nights = resolve_available_room_nights(
        date(2026, 9, 1),
        date(2026, 9, 30),
        inventory_mode="live_listings",
        inventory_units=1,
        listing_live_dates={"New Unit": date(2026, 6, 1)},
        listing_name="New Unit",
        comparable_listings=frozenset(),
        force_include_listings=frozenset({"New Unit"}),
    )
    assert nights == 30  # September fully available since go-live in June


def test_force_include_does_not_affect_untouched_listings():
    # A listing NOT in force_include_listings still gets zeroed as before -
    # this is additive, not a blanket bypass of the comparable filter.
    nights = resolve_available_room_nights(
        date(2026, 9, 1),
        date(2026, 9, 30),
        inventory_mode="live_listings",
        inventory_units=1,
        listing_live_dates={"Other Unit": date(2026, 6, 1)},
        listing_name="Other Unit",
        comparable_listings=frozenset(),
        force_include_listings=frozenset({"New Unit"}),
    )
    assert nights == 0


# ---------------------------------------------------------------------------
# run_snapshot(include_listings_in_breakdown=...)
# ---------------------------------------------------------------------------


def _patch_data_source(monkeypatch, config: PropertyConfig, records: list[BookingRecord]):
    monkeypatch.setattr(
        service,
        "resolve_snapshot_csv_path",
        lambda **kwargs: (Path("dummy.csv"), config),
    )
    monkeypatch.setattr(
        service,
        "read_property_bookings",
        lambda *args, **kwargs: (records, []),
    )


def test_new_listing_dropped_from_breakdown_without_the_fix(monkeypatch):
    config = _property_config()
    records = [
        _booking("2026-09-05", "2026-09-08", 900, "Old Unit"),
        _booking("2026-09-10", "2026-09-12", 400, "New Unit"),
    ]
    _patch_data_source(monkeypatch, config, records)

    result = run_snapshot(
        "2026-09-01",
        "2026-09-30",
        property_folder="TestProp",
        # yoy compare window predates New Unit's go-live -> New Unit is not
        # comparable and (before the fix) silently vanishes from the breakdown
        yoy_compare_start_date="2025-09-01",
        yoy_compare_end_date="2025-09-30",
    )

    names = {snap.property_id for snap in result.breakdown}
    assert "Old Unit" in names
    assert "New Unit" not in names  # reproduces the original bug


def test_include_listings_in_breakdown_keeps_new_listing_with_real_numbers(monkeypatch):
    config = _property_config()
    records = [
        _booking("2026-09-05", "2026-09-08", 900, "Old Unit"),
        _booking("2026-09-10", "2026-09-12", 400, "New Unit"),
    ]
    _patch_data_source(monkeypatch, config, records)

    result = run_snapshot(
        "2026-09-01",
        "2026-09-30",
        property_folder="TestProp",
        yoy_compare_start_date="2025-09-01",
        yoy_compare_end_date="2025-09-30",
        include_listings_in_breakdown=["New Unit"],
    )

    by_name = {snap.property_id: snap for snap in result.breakdown}
    assert "New Unit" in by_name
    new_unit = by_name["New Unit"]
    assert new_unit.room_revenue == Decimal("400.00")
    assert new_unit.bookings_count == 1
    # Occupancy/RevPAR should be real numbers, not None - this is the part
    # that force_include_listings in resolve_available_room_nights fixes;
    # without it the listing would keep its revenue but occupancy/revpar
    # would silently be None (available_room_nights forced to 0).
    assert new_unit.occupancy_pct is not None
    assert new_unit.revpar is not None

    # The other (comparable) listing and the portfolio total are untouched.
    assert by_name["Old Unit"].room_revenue == Decimal("900.00")


def test_include_listings_in_breakdown_is_a_pure_addition(monkeypatch):
    # Listings not named in include_listings_in_breakdown behave exactly as
    # before - this only widens the set, it doesn't change filtering for
    # anyone who doesn't ask for it.
    config = _property_config()
    records = [
        _booking("2026-09-05", "2026-09-08", 900, "Old Unit"),
        _booking("2026-09-10", "2026-09-12", 400, "New Unit"),
    ]
    _patch_data_source(monkeypatch, config, records)

    with_fix = run_snapshot(
        "2026-09-01",
        "2026-09-30",
        property_folder="TestProp",
        yoy_compare_start_date="2025-09-01",
        yoy_compare_end_date="2025-09-30",
        include_listings_in_breakdown=None,
    )
    without_fix_names = {snap.property_id for snap in with_fix.breakdown}
    assert without_fix_names == {"Old Unit"}


# ---------------------------------------------------------------------------
# run_listing_ramp_series
# ---------------------------------------------------------------------------


def test_ramp_series_returns_none_without_go_live_date(monkeypatch):
    config = _property_config(listing_live_dates={"Old Unit": date(2020, 1, 1)})
    _patch_data_source(monkeypatch, config, [])
    result = service.run_listing_ramp_series("Unknown Unit", property_folder="TestProp")
    assert result is None


def test_ramp_series_builds_monthly_rollup_since_go_live(monkeypatch):
    config = _property_config(listing_live_dates={"New Unit": date(2026, 6, 1)})
    records = [
        _booking("2026-06-05", "2026-06-08", 600, "New Unit"),
        _booking("2026-07-10", "2026-07-13", 750, "New Unit"),
    ]
    _patch_data_source(monkeypatch, config, records)

    # horizon_months=2 caps the window at go_live + ~60 days (~2026-07-31),
    # which is in the past relative to this suite's run date - keeps the
    # test deterministic without needing to mock date.today().
    series = service.run_listing_ramp_series(
        "New Unit",
        property_folder="TestProp",
        horizon_months=2,
    )
    assert series is not None
    by_period = {row["period"]: row for row in series}
    assert "2026-06" in by_period
    assert by_period["2026-06"]["room_revenue"] == 600.0
    assert by_period["2026-06"]["bookings_count"] == 1
    assert "2026-07" in by_period
    assert by_period["2026-07"]["room_revenue"] == 750.0
