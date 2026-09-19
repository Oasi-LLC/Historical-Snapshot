from __future__ import annotations

from historical_snapshot.chat.answer_plan import plan_from_classification, reply_mode_for_plan
from historical_snapshot.chat.formatter import format_listing_compare_report
from historical_snapshot.chat.models import ChatSnapshotQuery, ChatSnapshotResult
from historical_snapshot.chat.parser_rules import _resolve_all_listings
from historical_snapshot.chat.thread_state import (
    TurnKind,
    classify_turn,
    confirmed_state_from_query,
    resolve_query_for_turn,
)
from historical_snapshot.chat.tools import resolve_and_run_snapshot
from historical_snapshot.config import load_property_config
from datetime import date


def test_resolve_all_listings_finds_both_compare_targets():
    config = load_property_config("onera")
    names = _resolve_all_listings(
        "Which listing had a better performance last year: post oak or great lodge king room?",
        config,
    )
    assert names == ["Post Oak", "Great Lodge: King Room"]


def test_classify_listing_compare_turn():
    state = confirmed_state_from_query(
        {
            "property": "onera",
            "property_folder": "onera",
            "start_date": "2026-12-29",
            "end_date": "2027-01-01",
        },
        channel_id="C1",
        thread_ts="1.2",
    )
    turn = classify_turn(
        "Which listing had a better performance last year: post oak or great lodge king room?",
        state,
    )
    assert turn.kind == TurnKind.LISTING_COMPARE
    assert turn.listing_names == ("Post Oak", "Great Lodge: King Room")
    assert turn.compare_focus == "ly_final"
    assert reply_mode_for_plan(plan_from_classification(turn)) == "listing_compare"

    query = resolve_query_for_turn(turn, state)
    assert query is not None
    assert "listing_name" not in query or query.get("listing_name") in (None, "")
    assert query["compare_listings"] == ["Post Oak", "Great Lodge: King Room"]


def test_listing_compare_report_uses_ly_final_side_by_side():
    query = ChatSnapshotQuery(
        property_folder="onera",
        property_id="ONERA",
        property_name="Onera Fredericksburg",
        start_date=date(2026, 12, 29),
        end_date=date(2027, 1, 1),
    )
    result = ChatSnapshotResult(
        query=query,
        current_total={"portfolio_snapshot": {}},
        pace_current={"portfolio_snapshot": {}},
        pace_as_of_current=date(2026, 7, 29),
    )
    metrics = {
        "listing_compare": {
            "names": ["Post Oak", "Great Lodge: King Room"],
            "focus": "ly_final",
        },
        "listing_breakdown": {
            "ly_final": [
                {
                    "listing": "Great Lodge: King Room",
                    "room_revenue": 28815.25,
                    "bookings_count": 13,
                    "room_nights_sold": 23,
                    "adr": 1252.84,
                    "occupancy_pct": 95.83,
                },
                {
                    "listing": "Post Oak",
                    "room_revenue": 1442.58,
                    "bookings_count": 3,
                    "room_nights_sold": 4,
                    "adr": 360.64,
                    "occupancy_pct": 100.0,
                },
            ],
            "current": [
                {
                    "listing": "Post Oak",
                    "room_revenue": 271.2,
                    "bookings_count": 1,
                    "room_nights_sold": 1,
                    "adr": 271.2,
                    "occupancy_pct": 25.0,
                },
                {
                    "listing": "Great Lodge: King Room",
                    "room_revenue": 0,
                    "bookings_count": 0,
                    "room_nights_sold": 0,
                    "adr": 0,
                    "occupancy_pct": 0,
                },
            ],
        },
    }
    report = format_listing_compare_report(result, metrics)
    assert "Listing comparison" in report
    assert "LY final" in report
    assert "King Room" in report  # "Great Lodge: " prefix is stripped in display
    assert "Post Oak" in report
    assert "$28,815" in report
    assert "$1,443" in report
    assert "Metric" not in report  # not the portfolio performance table
    assert "Current pace:" in report


def test_listing_compare_live_numbers():
    payload = resolve_and_run_snapshot(
        property_token="onera",
        start_date="2026-12-29",
        end_date="2027-01-01",
        compare_listings=["Post Oak", "Great Lodge: King Room"],
        compare_focus="ly_final",
        fields=["listing_breakdown"],
        reply_mode="listing_compare",
        data_root="data",
    )
    assert payload["ok"] is True
    report = payload["formatted_report"]
    assert "LY final" in report
    assert "$28,815" in report
    assert "$1,443" in report
    ly_rows = (payload["metrics"]["listing_breakdown"]["ly_final"])
    by_name = {row["listing"]: row for row in ly_rows}
    assert by_name["Great Lodge: King Room"]["room_revenue"] == 28815.25
    assert by_name["Post Oak"]["room_revenue"] == 1442.58
