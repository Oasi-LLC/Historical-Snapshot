from __future__ import annotations

from historical_snapshot.core.bands import band_for_days, parse_bands
from historical_snapshot.core.metrics import booking_window_days_for_record
from historical_snapshot.models import BookingRecord
from datetime import date
from decimal import Decimal


def test_booking_window_is_arrival_minus_reservation():
    record = BookingRecord(
        property_id="P001",
        property_name="Hotel",
        listing_name="Unit",
        channel="Direct",
        grouping="Suite",
        reservation_date=date(2025, 1, 1),
        booking_date=date(2025, 1, 1),
        check_in_date=date(2025, 2, 13),
        check_out_date=date(2025, 2, 15),
        room_revenue=Decimal("100"),
        room_nights=2,
        status="confirmed",
    )
    assert booking_window_days_for_record(record) == 43


def test_default_bands_are_granular():
    bands = parse_bands(None)
    assert [band.label for band in bands] == ["0", "1-3", "4-7", "8-15", "16-30", "31-60", "61+"]


def test_single_day_band():
    bands = parse_bands("0")
    assert band_for_days(0, bands) == "0"
    assert bands[0].includes(0)
    assert not bands[0].includes(1)


def test_days_in_expected_bands():
    bands = parse_bands("0,1-3,4-7,8-15,16-30,31-60,61+")
    assert band_for_days(0, bands) == "0"
    assert band_for_days(2, bands) == "1-3"
    assert band_for_days(5, bands) == "4-7"
    assert band_for_days(10, bands) == "8-15"
    assert band_for_days(61, bands) == "61+"


def test_days_outside_all_closed_bands_use_last_after_check():
    bands = parse_bands("0,1-3,4-7,8-15,16-30,31-60,61+")
    assert band_for_days(-5, bands) == "61+"
    assert band_for_days(10_000, bands) == "61+"
