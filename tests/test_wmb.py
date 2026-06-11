from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from historical_snapshot.config import load_property_config
from historical_snapshot.core.bands import parse_bands
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.service import discover_properties, run_snapshot

FIXTURES = Path(__file__).parent / "fixtures"
DATA_ROOT = Path(__file__).parent.parent / "data"
WMB_CSV = DATA_ROOT / "WMB" / "WMB_main_data.csv"


def test_wmb_postprocess_filters_and_splits():
    property_config = load_property_config("WMB")
    records, issues = read_bookings_csv(
        FIXTURES / "wmb_sample.csv",
        property_config=property_config,
    )
    assert not issues
    assert len(records) == 4

    listing_names = {record.listing_name for record in records}
    assert listing_names == {
        "Greenhouse",
        "Greenhouse (Pet Friendly)",
        "Spyglass",
    }

    split_rows = [record for record in records if record.listing_name == "Spyglass"]
    greenhouse_pf_rows = [
        record for record in records if record.listing_name == "Greenhouse (Pet Friendly)"
    ]
    assert len(split_rows) == 1
    assert split_rows[0].room_revenue == Decimal("450")
    assert split_rows[0].room_nights == 2
    assert {record.room_revenue for record in greenhouse_pf_rows} == {
        Decimal("450"),
        Decimal("400"),
    }
    influencer_row = next(record for record in greenhouse_pf_rows if record.room_revenue == Decimal("400"))
    assert influencer_row.channel == "Other"


def test_wmb_listing_groups_collapse_to_greenhouse_and_spyglass():
    property_config = load_property_config("WMB")
    records, _ = read_bookings_csv(
        FIXTURES / "wmb_sample.csv",
        property_config=property_config,
    )
    groupings = {record.listing_name: record.grouping for record in records}
    assert groupings == {
        "Greenhouse": "Greenhouse",
        "Greenhouse (Pet Friendly)": "Greenhouse",
        "Spyglass": "Spyglass",
    }


def test_wmb_grouping_breakdown_uses_building_inventory():
    if not WMB_CSV.is_file():
        return

    result = run_snapshot(
        csv_path=WMB_CSV,
        start_date=date(2026, 7, 3),
        end_date=date(2026, 7, 5),
        property_folder="WMB",
        breakdown_by="grouping",
        bands=parse_bands("0-7,8-14,15-30,31-60,61+"),
    )
    groups = {item.property_id: item for item in result.breakdown}
    assert set(groups) == {"Greenhouse", "Spyglass"}
    assert result.portfolio.room_nights_sold == sum(item.room_nights_sold for item in result.breakdown)


def test_wmb_listing_inventory_config():
    property_config = load_property_config("WMB")
    assert property_config is not None
    assert property_config.property_name == "Onera Wimberley"
    assert property_config.default_inventory_listings == 28
    assert property_config.listing_inventory["Greenhouse"] == 9
    assert property_config.listing_inventory["Spyglass (Pet Friendly)"] == 6


def test_run_snapshot_wmb_fixture_breakdown_uses_listing_inventory():
    result = run_snapshot(
        csv_path=FIXTURES / "wmb_sample.csv",
        start_date=date(2024, 8, 1),
        end_date=date(2024, 12, 31),
        property_folder="WMB",
        date_basis="stay",
    )
    assert result.property_name == "Onera Wimberley"
    assert result.portfolio.bookings_count == 4
    assert result.portfolio.room_revenue == Decimal("1760.00")
    assert result.inventory_units_used == 28

    greenhouse = next(
        item for item in result.breakdown if item.property_id == "Greenhouse"
    )
    assert greenhouse.bookings_count == 1
    assert greenhouse.room_revenue == Decimal("460.00")


def test_wmb_july_fourth_weekend_metrics_are_physical():
    """Regression: US m/d dates must not inflate occupancy above 100% for a 2-day window."""
    if not WMB_CSV.is_file():
        return

    result = run_snapshot(
        csv_path=WMB_CSV,
        start_date=date(2025, 7, 4),
        end_date=date(2025, 7, 5),
        property_folder="WMB",
        bands=parse_bands("0,1-3,4-7,8-15,16-30,31-60,61+"),
    )
    portfolio = result.portfolio
    assert portfolio.bookings_count < 60
    assert portfolio.room_nights_sold <= 56
    assert float(portfolio.occupancy_pct or 0) <= 100
    assert portfolio.room_revenue == Decimal("37268.00")


def test_run_snapshot_wmb_main_data_smoke():
    if not WMB_CSV.is_file():
        return

    result = run_snapshot(
        csv_path=WMB_CSV,
        start_date=date(2024, 8, 1),
        end_date=date(2025, 12, 31),
        property_folder="WMB",
        bands=parse_bands("0-7,8-14,15-30,31-60,61+"),
    )
    assert result.property_name == "Onera Wimberley"
    assert result.portfolio.bookings_count > 0
    assert result.portfolio.room_revenue > Decimal("0")
    assert len(result.breakdown) == 6
