from __future__ import annotations

from datetime import date

from historical_snapshot.chat.formatter import format_reply
from historical_snapshot.chat.models import ChatSnapshotQuery, ChatSnapshotResult
from historical_snapshot.chat.slack_format import format_interpretation


def test_revenue_by_window_uses_table():
    query = ChatSnapshotQuery(
        property_folder="wmb",
        property_id="wmb",
        property_name="WMB",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 7),
    )
    portfolio = {
        "room_revenue": 1000,
        "bookings_count": 5,
        "adr": 200,
        "occupancy_pct": 50,
        "revpar": 100,
        "average_los": 1.0,
        "pickup": {
            "revenue_by_band": {"0": 100, "1-3": 200},
            "revenue_share_pct_by_band": {"0": 33.0, "1-3": 67.0},
        },
    }
    snapshot = {"portfolio_snapshot": portfolio}
    result = ChatSnapshotResult(
        query=query,
        current_total=snapshot,
        pace_current=snapshot,
        pace_prior=snapshot,
        prior_final=snapshot,
        pace_as_of_current=date(2026, 7, 29),
    )
    report = format_reply(result)
    assert "Revenue by booking window (LY final)" in report
    assert "Window" in report and "Revenue" in report and "Share" in report
    assert "• 0d:" not in report


def test_interpretation_numbered_without_asterisks():
    raw = (
        "• *Pacing well behind LY* — down 55% vs LY pace.\n"
        "• *Volume concern* — occupancy 7% vs 87% LY final."
    )
    out = format_interpretation(raw)
    assert out is not None
    assert out.startswith("Interpretation\n\n1.")
    assert "*" not in out
    assert "1." in out and "2." in out


def test_interpretation_preserves_long_bullets():
    long_claim = (
        "Booking-curve pattern mismatch: LY final median was 16.0d with 30% of revenue "
        "in the 1–3d band, so Current vs LY Pace is the primary open-stay comparison."
    )
    out = format_interpretation(long_claim)
    assert out is not None
    assert "…" not in out
    assert long_claim in out
