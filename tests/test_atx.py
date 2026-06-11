from __future__ import annotations

from datetime import date
from pathlib import Path

from historical_snapshot.config import load_property_config
from historical_snapshot.core.bands import parse_bands
from historical_snapshot.io.property_reader import read_property_bookings
from historical_snapshot.service import discover_properties, run_snapshot

DATA_ROOT = Path(__file__).parent.parent / "data"
ATX_DIR = DATA_ROOT / "ATX"


def test_atx_merges_track_and_hostaway_without_duplicates():
    if not ATX_DIR.is_dir():
        return

    property_config = load_property_config("ATX")
    csv_path = ATX_DIR / "hostaway_data.csv"
    records, issues = read_property_bookings(csv_path, property_config=property_config)

    listings = {record.listing_name for record in records}
    assert listings <= {"Sunstrip", "Malvern (4BR)", "Malvern (3BR)"}
    assert "Romney" not in listings
    assert "Coventry Ln" not in listings

    stay_keys = {
        (record.listing_name, record.check_in_date, record.check_out_date) for record in records
    }
    assert len(stay_keys) == len(records)

    track_count = sum(
        1 for record in records if record.check_in_date < date(2024, 10, 21)
    )
    hostaway_count = len(records) - track_count
    assert track_count == 64 + 35  # Sunstrip + Malvern from Track
    assert hostaway_count > 0


def test_atx_malvern_maps_to_4br():
    if not ATX_DIR.is_dir():
        return

    property_config = load_property_config("ATX")
    records, _ = read_property_bookings(
        ATX_DIR / "hostaway_data.csv",
        property_config=property_config,
    )
    malvern_4br = [record for record in records if record.listing_name == "Malvern (4BR)"]
    assert len(malvern_4br) == 35 + 19 + 14  # Track Malvern + Hostaway Malvern + Malvern (4BR)


def test_discover_properties_finds_atx():
    properties = discover_properties(DATA_ROOT)
    ids = [item["id"] for item in properties]
    assert "ATX" in ids


def test_run_snapshot_atx_smoke():
    if not ATX_DIR.is_dir():
        return

    result = run_snapshot(
        csv_path=ATX_DIR / "hostaway_data.csv",
        start_date=date(2024, 1, 1),
        end_date=date(2025, 12, 31),
        property_folder="ATX",
        bands=parse_bands("0-7,8-14,15-30,31-60,61+"),
    )
    assert result.property_name == "ATX"
    assert result.inventory_units_used == 2
    assert result.portfolio.bookings_count > 0
    assert len(result.breakdown) == 2  # Sunstrip + Malvern (4BR) in 2024-2025 window
