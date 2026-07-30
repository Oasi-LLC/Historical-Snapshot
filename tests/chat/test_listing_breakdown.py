from __future__ import annotations

from historical_snapshot.chat.formatter import append_listing_breakdown_tables
from historical_snapshot.chat.tools import _compact_listing_rows, _listing_label, _select_fields


def test_listing_label_uses_property_id_not_property_name():
    row = {
        "property_id": "203 LaFave South: Emerald Pools",
        "property_name": "LaFave",
        "room_revenue": 2437.5,
    }
    assert _listing_label(row) == "203 LaFave South: Emerald Pools"


def test_compact_listing_rows_keeps_unit_names():
    snapshot = {
        "breakdown_snapshots": [
            {
                "property_id": "203 LaFave South: Emerald Pools",
                "property_name": "LaFave",
                "room_revenue": 2437.5,
                "bookings_count": 1,
                "room_nights_sold": 4,
                "adr": 609.38,
                "occupancy_pct": 80.0,
                "revpar": 487.5,
                "average_los": 4.0,
            },
            {
                "property_id": "Gallery House",
                "property_name": "LaFave",
                "room_revenue": 6103.01,
                "bookings_count": 2,
                "room_nights_sold": 4,
                "adr": 1525.75,
                "occupancy_pct": 80.0,
                "revpar": 1220.6,
                "average_los": 2.0,
            },
        ]
    }
    rows = _compact_listing_rows(snapshot, limit=5)
    assert rows[0]["listing"] == "Gallery House"
    assert rows[1]["listing"] == "203 LaFave South: Emerald Pools"
    assert all(row["listing"] != "LaFave" for row in rows)


def test_select_fields_keeps_defaults_when_optional_requested():
    selected = _select_fields(["listing_breakdown"])
    assert "room_revenue" in selected
    assert "listing_breakdown" in selected


def test_select_fields_omits_listing_breakdown_by_default():
    selected = _select_fields(None)
    assert "listing_breakdown" not in selected
    assert "room_revenue" in selected


def test_append_listing_breakdown_tables_includes_names():
    report = "Portfolio report here"
    current = [
        {
            "listing": "Gallery House",
            "room_revenue": 6103,
            "bookings_count": 2,
            "room_nights_sold": 4,
            "adr": 1526,
            "occupancy_pct": 80,
        }
    ]
    ly = [
        {
            "listing": "Angels Landing",
            "room_revenue": 8223,
            "bookings_count": 1,
            "room_nights_sold": 5,
            "adr": 1645,
            "occupancy_pct": 100,
        }
    ]
    out = append_listing_breakdown_tables(
        report,
        current_rows=current,
        ly_final_rows=ly,
    )
    assert "Top listings (current pace)" in out
    assert "Gallery House" in out
    assert "Top listings (LY final)" in out
    assert "Angels Landing" in out
