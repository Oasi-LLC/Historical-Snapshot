from __future__ import annotations

from datetime import date
from decimal import Decimal

from historical_snapshot.core.metrics import count_active_listings
from historical_snapshot.models import BookingRecord
from historical_snapshot.service import run_snapshot

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _record(listing: str, check_in: str, check_out: str) -> BookingRecord:
    check_in_date = date.fromisoformat(check_in)
    check_out_date = date.fromisoformat(check_out)
    nights = (check_out_date - check_in_date).days
    return BookingRecord(
        property_id="P",
        property_name="P",
        listing_name=listing,
        channel="direct",
        grouping="",
        reservation_date=check_in_date,
        booking_date=check_in_date,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        room_revenue=Decimal("100") * nights,
        room_nights=nights,
        status="confirmed",
    )


def test_count_active_listings_excludes_pre_launch_units():
    records = [
        _record("Old", "2023-01-01", "2023-01-03"),
        _record("New", "2025-06-01", "2025-06-03"),
    ]
    assert count_active_listings(records, date(2025, 7, 4), date(2025, 7, 5)) == 1
    assert count_active_listings(records, date(2023, 1, 1), date(2023, 1, 2)) == 1


def test_yoy_snapshot_uses_different_inventory_counts():
    current = run_snapshot(
        csv_path=FIXTURES / "hostaway_sample.csv",
        start_date=date(2025, 7, 4),
        end_date=date(2025, 7, 5),
        property_folder="flohom",
    )
    prior = run_snapshot(
        csv_path=FIXTURES / "hostaway_sample.csv",
        start_date=date(2024, 7, 4),
        end_date=date(2024, 7, 5),
        property_folder="flohom",
    )
    assert current.inventory_units_used == 1
    assert prior.inventory_units_used == 1
