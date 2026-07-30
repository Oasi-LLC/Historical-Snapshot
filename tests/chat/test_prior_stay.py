from __future__ import annotations

from datetime import date

from historical_snapshot.chat.executor import _prior_stay_window
from historical_snapshot.chat.formatter import format_reply
from historical_snapshot.chat.models import ChatSnapshotQuery, ChatSnapshotResult


def test_prior_stay_window_defaults_to_calendar_shift():
    query = ChatSnapshotQuery(
        property_folder="wmb",
        property_id="wmb",
        property_name="WMB",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 7),
    )
    start, end = _prior_stay_window(query)
    assert start == date(2025, 9, 4)
    assert end == date(2025, 9, 7)


def test_prior_stay_window_custom_holiday():
    query = ChatSnapshotQuery(
        property_folder="wmb",
        property_id="wmb",
        property_name="WMB",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 7),
        prior_stay_start_date=date(2025, 8, 29),
        prior_stay_end_date=date(2025, 9, 1),
    )
    start, end = _prior_stay_window(query)
    assert start == date(2025, 8, 29)
    assert end == date(2025, 9, 1)


def _minimal_result(query: ChatSnapshotQuery) -> ChatSnapshotResult:
    portfolio = {
        "room_revenue": 1000,
        "bookings_count": 5,
        "adr": 200,
        "occupancy_pct": 50,
        "revpar": 100,
        "average_los": 1.0,
    }
    snapshot = {"portfolio_snapshot": portfolio}
    return ChatSnapshotResult(
        query=query,
        current_total=snapshot,
        pace_current=snapshot,
        pace_prior=snapshot,
        prior_final=snapshot,
        pace_as_of_current=date(2026, 7, 28),
    )


def test_formatter_shows_custom_ly_window():
    query = ChatSnapshotQuery(
        property_folder="wmb",
        property_id="wmb",
        property_name="WMB",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 7),
        prior_stay_start_date=date(2025, 8, 29),
        prior_stay_end_date=date(2025, 9, 1),
    )
    report = format_reply(_minimal_result(query))
    assert "Sep 4–7 2026 vs Aug 29–Sep 1 2025 LY" in report


def test_formatter_shows_tables_when_current_zero_but_ly_has_bookings():
    query = ChatSnapshotQuery(
        property_folder="atx",
        property_id="atx",
        property_name="ATX",
        start_date=date(2026, 12, 24),
        end_date=date(2026, 12, 27),
    )
    zero = {
        "room_revenue": 0,
        "bookings_count": 0,
        "adr": 0,
        "occupancy_pct": 0,
        "revpar": 0,
        "average_los": 1.0,
    }
    ly = {
        "room_revenue": 1825.69,
        "bookings_count": 2,
        "adr": 456,
        "occupancy_pct": 50,
        "revpar": 228,
        "average_los": 2.0,
        "booking_window": {"mean_days": 9.5, "median_days": 2.0},
        "pickup": {
            "revenue_by_band": {"1-3": 1300, "16-30": 525},
            "revenue_share_pct_by_band": {"1-3": 71.8, "16-30": 28.2},
        },
    }
    result = ChatSnapshotResult(
        query=query,
        current_total={"portfolio_snapshot": zero},
        pace_current={"portfolio_snapshot": zero},
        pace_prior={"portfolio_snapshot": zero},
        prior_final={"portfolio_snapshot": ly},
        pace_as_of_current=date(2026, 7, 29),
    )
    report = format_reply(result)
    assert "No bookings on the books yet" in report
    assert "LY Final" in report
    assert "Booking window (last year)" in report
    assert "Revenue by booking window (LY final)" in report
    assert "No bookings found for" not in report


def test_formatter_omits_ly_label_for_calendar_shift():
    query = ChatSnapshotQuery(
        property_folder="wmb",
        property_id="wmb",
        property_name="WMB",
        start_date=date(2026, 9, 4),
        end_date=date(2026, 9, 7),
    )
    report = format_reply(_minimal_result(query))
    assert "vs" not in report.split("\n")[0]
