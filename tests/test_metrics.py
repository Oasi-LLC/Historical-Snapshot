from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from historical_snapshot.core.bands import parse_bands
from historical_snapshot.core.metrics import (
    compute_snapshot_metrics,
    nights_in_range,
    stay_overlaps_range,
)
from historical_snapshot.models import BookingRecord
from historical_snapshot.io.reader import read_bookings_csv


FIXTURES = Path(__file__).parent / "fixtures"


def test_compute_snapshot_core_metrics():
    records, issues = read_bookings_csv(FIXTURES / "bookings_sample.csv")
    assert not issues

    snapshot = compute_snapshot_metrics(
        records=records,
        property_id="P001",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        bands=parse_bands("0-7,8-15,16-30,31+"),
    )

    assert snapshot.bookings_count == 3
    assert snapshot.room_nights_sold == 7
    assert snapshot.room_revenue == Decimal("1250.00")
    assert snapshot.adr == Decimal("178.57")
    assert snapshot.occupancy_pct == Decimal("23.33")
    assert snapshot.revpar == Decimal("41.67")
    assert snapshot.average_los == Decimal("2.33")
    assert snapshot.pickup_room_nights_by_band == {"0-7": 2, "8-15": 2, "31+": 3}


def test_invalid_rows_are_reported_but_valid_rows_loaded():
    records, issues = read_bookings_csv(FIXTURES / "bookings_invalid_rows.csv")
    assert len(records) == 1
    assert len(issues) == 2


def test_stay_overlap_prorates_partial_booking():
    record = BookingRecord(
        property_id="P001",
        property_name="Hotel One",
        listing_name="Unit A",
        channel="Direct",
        grouping="Suite",
        reservation_date=date(2025, 1, 1),
        booking_date=date(2025, 1, 1),
        check_in_date=date(2025, 1, 1),
        check_out_date=date(2025, 1, 11),
        room_revenue=Decimal("1000.00"),
        room_nights=10,
        status="confirmed",
    )
    assert stay_overlaps_range(record, date(2025, 1, 5), date(2025, 1, 7))
    assert nights_in_range(record, date(2025, 1, 5), date(2025, 1, 7)) == 3

    snapshot = compute_snapshot_metrics(
        records=[record],
        property_id="P001",
        start_date=date(2025, 1, 5),
        end_date=date(2025, 1, 7),
        bands=parse_bands("0-7"),
        date_basis="stay",
        inventory_units=1,
    )
    assert snapshot.bookings_count == 1
    assert snapshot.room_nights_sold == 3
    assert snapshot.room_revenue == Decimal("300.00")
    assert snapshot.adr == Decimal("100.00")


def test_arrival_basis_uses_check_in_only():
    records, _ = read_bookings_csv(FIXTURES / "bookings_sample.csv")
    stay = compute_snapshot_metrics(
        records=records,
        property_id="P001",
        start_date=date(2025, 1, 20),
        end_date=date(2025, 1, 22),
        bands=parse_bands(None),
        date_basis="stay",
    )
    arrival = compute_snapshot_metrics(
        records=records,
        property_id="P001",
        start_date=date(2025, 1, 20),
        end_date=date(2025, 1, 22),
        bands=parse_bands(None),
        date_basis="arrival",
    )
    assert stay.bookings_count >= arrival.bookings_count


def test_zero_denominators_return_none_metrics():
    records, _ = read_bookings_csv(FIXTURES / "bookings_sample.csv")
    snapshot = compute_snapshot_metrics(
        records=records,
        property_id="unknown",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        bands=parse_bands(None),
    )
    assert snapshot.adr is None
    assert snapshot.occupancy_pct is None
    assert snapshot.revpar is None
    assert snapshot.average_los is None
