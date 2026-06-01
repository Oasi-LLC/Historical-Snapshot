from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from historical_snapshot.service import discover_properties, run_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
DATA_ROOT = Path(__file__).parent.parent / "data"


def test_run_snapshot_stay_overlap_proration():
    result = run_snapshot(
        csv_path=FIXTURES / "bookings_sample.csv",
        start_date=date(2025, 1, 5),
        end_date=date(2025, 1, 7),
        property_id="P001",
        property_name="Hotel One",
        date_basis="stay",
        inventory_listings=1,
    )
    assert result.portfolio.bookings_count == 1
    assert result.portfolio.room_nights_sold == 2
    assert result.portfolio.room_revenue == Decimal("500.00")


def test_discover_properties_finds_lafave():
    properties = discover_properties(DATA_ROOT)
    ids = [p["id"] for p in properties]
    assert "LAFAVE" in ids


def test_discover_properties_includes_flohom_config():
    properties = discover_properties(DATA_ROOT)
    flohom = next((p for p in properties if p["id"] == "FLOHOM"), None)
    if flohom is None:
        pytest.skip("local flohom data folder not present")
    assert flohom["name"] == "Flohom"
    assert flohom["inventory_mode"] == "active_listings"
    assert flohom["pms"] == "hostaway"
