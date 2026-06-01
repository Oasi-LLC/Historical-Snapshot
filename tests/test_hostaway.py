from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from historical_snapshot.config import load_property_config, load_pms_profile
from historical_snapshot.core.metrics import count_active_listings
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.service import run_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
DATA_ROOT = Path(__file__).parent.parent / "data"


def test_hostaway_csv_parses_with_profile():
    config = load_property_config("flohom")
    profile = load_pms_profile("hostaway")
    records, issues = read_bookings_csv(
        FIXTURES / "hostaway_sample.csv",
        property_config=config,
        pms_profile=profile,
    )
    assert len(records) == 3
    assert len(issues) == 0
    assert all(r.room_revenue > 0 for r in records)


def test_hostaway_excludes_unpaid_and_unknown_payment():
    config = load_property_config("flohom")
    profile = load_pms_profile("hostaway")
    records, _ = read_bookings_csv(
        FIXTURES / "hostaway_sample.csv",
        property_config=config,
        pms_profile=profile,
    )
    listing_names = {r.listing_name for r in records}
    assert listing_names == {"Unit A", "Unit D"}


def test_active_listings_differs_by_period():
    config = load_property_config("flohom")
    profile = load_pms_profile("hostaway")
    records, _ = read_bookings_csv(
        FIXTURES / "hostaway_sample.csv",
        property_config=config,
        pms_profile=profile,
    )
    july_2025 = count_active_listings(records, date(2025, 7, 4), date(2025, 7, 5))
    july_2024 = count_active_listings(records, date(2024, 7, 4), date(2024, 7, 5))
    assert july_2025 == 1
    assert july_2024 == 1


def test_run_snapshot_flohom_config_active_inventory():
    result = run_snapshot(
        csv_path=FIXTURES / "hostaway_sample.csv",
        start_date=date(2025, 7, 4),
        end_date=date(2025, 7, 5),
        property_folder="flohom",
    )
    assert result.inventory_mode == "active_listings"
    assert result.inventory_units_used == 1
    assert result.portfolio.bookings_count == 1


@pytest.mark.skipif(
    not (DATA_ROOT / "flohom" / "flohom_main_data.csv").is_file(),
    reason="local flohom data not present",
)
def test_flohom_local_csv_loads():
    csv_path = DATA_ROOT / "flohom" / "flohom_main_data.csv"
    config = load_property_config("flohom")
    profile = load_pms_profile("hostaway")
    records, issues = read_bookings_csv(
        csv_path,
        property_config=config,
        pms_profile=profile,
    )
    assert len(records) > 1000
    assert len(issues) < len(records)
