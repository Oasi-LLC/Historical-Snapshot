from __future__ import annotations

from types import SimpleNamespace

import historical_snapshot.service as service
from historical_snapshot.chat.formatter import _format_month_label, _ramp_series_table
from historical_snapshot.chat.tools import _comparison_summary, _ramp_series_for_compare


# ---------------------------------------------------------------------------
# _comparison_summary
# ---------------------------------------------------------------------------


def _row(listing, revenue, adr, occ, revpar):
    return {
        "listing": listing,
        "room_revenue": revenue,
        "adr": adr,
        "occupancy_pct": occ,
        "revpar": revpar,
    }


def test_comparison_summary_identifies_leader_and_delta():
    rows = [
        _row("Flohom 15", 12450, 692, 71, 491),
        _row("Flohom 17", 9800, 700, 64, 448),
    ]
    summary = _comparison_summary(rows)
    assert summary["listing_a"] == "Flohom 15"
    assert summary["listing_b"] == "Flohom 17"
    assert summary["revenue"] == {"leader": "Flohom 15", "delta": 2650.0}
    assert summary["adr"] == {"leader": "Flohom 17", "delta": 8.0}
    assert summary["occupancy_pct"]["leader"] == "Flohom 15"


def test_comparison_summary_none_when_not_exactly_two_rows():
    assert _comparison_summary([_row("Solo Unit", 100, 100, 50, 50)]) is None
    assert _comparison_summary([]) is None


def test_comparison_summary_handles_missing_fields_without_crashing():
    rows = [
        {"listing": "A", "room_revenue": None, "adr": 100, "occupancy_pct": 50, "revpar": 50},
        {"listing": "B", "room_revenue": 200, "adr": 90, "occupancy_pct": 60, "revpar": 55},
    ]
    summary = _comparison_summary(rows)
    assert summary["revenue"] is None  # can't compare when one side is missing
    assert summary["adr"]["leader"] == "A"


def test_comparison_summary_tie_has_no_leader():
    rows = [
        _row("A", 1000, 500, 70, 350),
        _row("B", 1000, 500, 70, 350),
    ]
    summary = _comparison_summary(rows)
    assert summary["revenue"] == {"leader": None, "delta": 0.0}


# ---------------------------------------------------------------------------
# _ramp_series_for_compare
# ---------------------------------------------------------------------------


def test_ramp_series_skips_listings_with_ly_data(monkeypatch):
    calls = []

    def fake_ramp(name, **kwargs):
        calls.append(name)
        return [{"period": "2026-01", "room_revenue": 100.0}]

    monkeypatch.setattr(service, "run_listing_ramp_series", fake_ramp)

    config = SimpleNamespace(folder="TestProp")
    ly_final_rows = [
        {"listing": "Established Unit", "bookings_count": 3, "room_revenue": 900},
        {"listing": "New Unit", "bookings_count": 0, "room_revenue": 0},
    ]
    series = _ramp_series_for_compare(
        config=config,
        compare_names=["Established Unit", "New Unit"],
        ly_final_rows=ly_final_rows,
        data_root="data",
    )
    assert calls == ["New Unit"]  # established listing skipped, has real LY data
    assert "Established Unit" not in series
    assert series["New Unit"][0]["room_revenue"] == 100.0


def test_ramp_series_omits_listing_with_no_ramp_available(monkeypatch):
    monkeypatch.setattr(service, "run_listing_ramp_series", lambda name, **kwargs: None)
    config = SimpleNamespace(folder="TestProp")
    series = _ramp_series_for_compare(
        config=config,
        compare_names=["New Unit"],
        ly_final_rows=[{"listing": "New Unit", "bookings_count": 0, "room_revenue": 0}],
        data_root="data",
    )
    assert series == {}


# ---------------------------------------------------------------------------
# formatter rendering
# ---------------------------------------------------------------------------


def test_format_month_label():
    assert _format_month_label("2026-01") == "Jan 2026"
    assert _format_month_label("not-a-period") == "not-a-period"


def test_ramp_series_table_aligns_by_month_with_gaps():
    ramp_series = {
        "Flohom 15": [
            {"period": "2026-06", "room_revenue": 8200, "occupancy_pct": 68},
            {"period": "2026-07", "room_revenue": 10400, "occupancy_pct": 74},
        ],
        "Flohom 17": [
            {"period": "2026-07", "room_revenue": 8900, "occupancy_pct": 63},
        ],
    }
    lines = _ramp_series_table(ramp_series, ["Flohom 15", "Flohom 17"])
    text = "\n".join(lines)
    assert "Jun 2026" in text
    assert "Jul 2026" in text
    assert "$8,200" in text
    # Flohom 17 has no June row - should render as a placeholder, not $0
    june_line = next(line for line in lines if line.startswith("Jun 2026"))
    assert "—" in june_line


def test_ramp_series_table_empty_when_no_data():
    assert _ramp_series_table({}, ["A", "B"]) == []
    assert _ramp_series_table({"A": []}, ["A"]) == []
