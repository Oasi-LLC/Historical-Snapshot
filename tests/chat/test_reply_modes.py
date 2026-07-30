from __future__ import annotations

from datetime import date

from historical_snapshot.chat.formatter import (
    append_channel_breakdown_tables,
    build_formatted_report,
)
from historical_snapshot.chat.models import ChatSnapshotQuery, ChatSnapshotResult
from historical_snapshot.chat.reply_modes import compose_thread_reply


def _minimal_result(*, listing_name: str | None = None) -> ChatSnapshotResult:
    query = ChatSnapshotQuery(
        property_folder="lafave",
        property_id="lafave",
        property_name="LaFave",
        start_date=date(2026, 11, 25),
        end_date=date(2026, 11, 29),
        listing_name=listing_name,
    )
    portfolio = {
        "room_revenue": 10000,
        "bookings_count": 8,
        "adr": 500,
        "occupancy_pct": 60,
        "revpar": 300,
        "average_los": 1.0,
    }
    snapshot = {"portfolio_snapshot": portfolio}
    return ChatSnapshotResult(
        query=query,
        current_total=snapshot,
        pace_current=snapshot,
        pace_prior=snapshot,
        prior_final=snapshot,
        pace_as_of_current=date(2026, 7, 29),
    )


def _listing_metrics() -> dict:
    return {
        "listing_breakdown": {
            "current": [
                {
                    "listing": "Gallery House",
                    "room_revenue": 6103,
                    "bookings_count": 2,
                    "room_nights_sold": 4,
                    "adr": 1526,
                    "occupancy_pct": 80,
                }
            ],
            "ly_final": [
                {
                    "listing": "Angels Landing",
                    "room_revenue": 8223,
                    "bookings_count": 1,
                    "room_nights_sold": 5,
                    "adr": 1645,
                    "occupancy_pct": 100,
                }
            ],
        }
    }


def _channel_metrics() -> dict:
    return {
        "channel_breakdown": {
            "current": {
                "Airbnb": {
                    "revenue": 7000,
                    "revenue_share_pct": 70,
                    "bookings_count": 5,
                    "room_nights": 10,
                    "adr": 700,
                },
                "VRBO": {
                    "revenue": 3000,
                    "revenue_share_pct": 30,
                    "bookings_count": 3,
                    "room_nights": 6,
                    "adr": 500,
                },
            },
            "ly_final": {
                "Airbnb": {
                    "revenue": 8000,
                    "revenue_share_pct": 80,
                    "bookings_count": 6,
                    "room_nights": 12,
                    "adr": 667,
                }
            },
        }
    }


def test_compose_thread_reply_interpretation_only():
    out = compose_thread_reply(
        report="Full portfolio table",
        interpretation="Pace is behind on ADR.",
        reply_mode="interpretation_only",
    )
    assert out == "Pace is behind on ADR."
    assert "Full portfolio" not in out


def test_compose_thread_reply_delta_with_interpretation():
    out = compose_thread_reply(
        report="Top listings table",
        interpretation="Gallery House leads revenue.",
        reply_mode="delta_listings",
    )
    assert "Top listings table" in out
    assert "Gallery House leads revenue." in out


def test_build_formatted_report_full_includes_performance_table():
    result = _minimal_result()
    report = build_formatted_report(result, _listing_metrics(), reply_mode="full")
    assert "Revenue" in report
    assert "Top listings (current pace)" in report
    assert "Gallery House" in report


def test_build_formatted_report_full_without_listing_metrics_omits_tables():
    result = _minimal_result()
    report = build_formatted_report(result, {}, reply_mode="full")
    assert "Revenue" in report
    assert "Top listings" not in report


def test_build_formatted_report_delta_listings_omits_performance_table():
    result = _minimal_result()
    report = build_formatted_report(result, _listing_metrics(), reply_mode="delta_listings")
    assert "Listing breakdown for this stay window" in report
    assert "Top listings (current pace)" in report
    assert "Gallery House" in report
    assert "vs Pace" not in report


def test_build_formatted_report_delta_channels():
    result = _minimal_result()
    report = build_formatted_report(result, _channel_metrics(), reply_mode="delta_channels")
    assert "Channel mix for this stay window" in report
    assert "Channel mix (current pace)" in report
    assert "Airbnb" in report
    assert "VRBO" in report
    assert "vs Pace" not in report


def test_build_formatted_report_interpretation_only_empty():
    result = _minimal_result()
    report = build_formatted_report(result, _listing_metrics(), reply_mode="interpretation_only")
    assert report == ""


def test_append_channel_breakdown_tables():
    out = append_channel_breakdown_tables(
        "Header",
        current_mix=_channel_metrics()["channel_breakdown"]["current"],
        ly_final_mix=_channel_metrics()["channel_breakdown"]["ly_final"],
    )
    assert "Channel mix (current pace)" in out
    assert "Channel mix (LY final)" in out
    assert "Airbnb" in out
