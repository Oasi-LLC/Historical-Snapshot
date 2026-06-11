from __future__ import annotations

from datetime import date
from pathlib import Path

from historical_snapshot.service import run_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def test_explicit_breakdown_by_overrides_property_config_default():
    result = run_snapshot(
        csv_path=FIXTURES / "bookings_sample.csv",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 31),
        property_id="P001",
        property_name="Hotel One",
        breakdown_by="grouping",
        inventory_listings=1,
    )
    assert result.breakdown_by == "grouping"
    assert {item.property_id for item in result.breakdown} == {"Ungrouped"}
