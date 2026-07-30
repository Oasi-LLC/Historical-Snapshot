from __future__ import annotations

from datetime import date

from historical_snapshot.chat.answer_plan import (
    extract_top_n,
    parse_answer_plan,
    plan_from_classification,
    plan_from_tool_args,
    reply_mode_for_plan,
)
from historical_snapshot.chat.models import ChatSnapshotQuery, ChatSnapshotResult
from historical_snapshot.chat.plan_renderer import render_answer_plan
from historical_snapshot.chat.thread_state import (
    TurnClassification,
    TurnKind,
    confirmed_state_from_query,
)


def test_plan_from_listing_compare_classification():
    turn = TurnClassification(
        kind=TurnKind.LISTING_COMPARE,
        listing_names=("Post Oak", "Great Lodge: King Room"),
        compare_focus="ly_final",
        extra_fields=("listing_breakdown",),
    )
    plan = plan_from_classification(turn)
    assert plan.intent == "listing_compare"
    assert plan.blocks == ("header", "listing_compare")
    assert plan.listings == ("Post Oak", "Great Lodge: King Room")
    assert plan.basis == ("ly_final",)
    assert reply_mode_for_plan(plan) == "listing_compare"


def test_plan_from_follow_up_listings():
    turn = TurnClassification(
        kind=TurnKind.FOLLOW_UP,
        extra_fields=("listing_breakdown",),
    )
    plan = plan_from_classification(turn)
    assert plan.intent == "listing_rank"
    assert "top_listings" in plan.blocks
    assert "performance" not in plan.blocks


def test_extract_top_n_from_message():
    assert extract_top_n("Give me top 3 performing listings") == 3
    assert extract_top_n("top 15 listings") == 15
    assert extract_top_n("top listings please") is None
    assert extract_top_n("top 999 listings") is None  # absurd/unmatched, not clamped-then-huge
    assert extract_top_n("top 25 listings") == 20  # clamped to MAX_TOP_N


def test_plan_top_n_overrides_default_render_limit():
    """Regression: 'top 3' was previously ignored and always rendered 5 rows."""
    turn = TurnClassification(
        kind=TurnKind.FOLLOW_UP,
        extra_fields=("listing_breakdown",),
    )
    plan = plan_from_classification(turn)
    assert plan.top_n is None

    plan_with_count = plan.with_top_n_from_message("Give me top 3 performing listings")
    assert plan_with_count.top_n == 3

    # No explicit count in the message -> unchanged.
    plan_unchanged = plan.with_top_n_from_message("Give me the top performing listings")
    assert plan_unchanged.top_n is None


def test_parse_freeform_llm_plan():
    plan = parse_answer_plan(
        {
            "intent": "listing_compare",
            "basis": ["ly_final"],
            "blocks": ["header", "listing_compare"],
            "listings": ["Post Oak", "Great Lodge: King Room"],
            "confidence": 0.9,
        },
        source="llm",
    )
    assert plan is not None
    assert plan.source == "llm"
    assert plan.snapshot_fields() == ["listing_breakdown"]


def test_render_plan_listing_compare_not_performance_table():
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
    plan = parse_answer_plan(
        {
            "intent": "listing_compare",
            "basis": ["ly_final"],
            "blocks": ["header", "listing_compare"],
            "listings": ["Post Oak", "Great Lodge: King Room"],
        }
    )
    assert plan is not None
    metrics = {
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
            "current": [],
        }
    }
    report = render_answer_plan(plan, result, metrics)
    assert "LY final" in report
    assert "$28,815" in report
    assert "Metric" not in report


def test_render_plan_explain_is_empty_report():
    query = ChatSnapshotQuery(
        property_folder="onera",
        property_id="ONERA",
        property_name="Onera Fredericksburg",
        start_date=date(2026, 12, 29),
        end_date=date(2027, 1, 1),
    )
    result = ChatSnapshotResult(
        query=query,
        current_total={"portfolio_snapshot": {"room_revenue": 1}},
        pace_current={"portfolio_snapshot": {"room_revenue": 1}},
        pace_as_of_current=date(2026, 7, 29),
    )
    plan = plan_from_classification(TurnClassification(kind=TurnKind.INTERPRET_ONLY))
    assert render_answer_plan(plan, result, {}) == ""


def test_plan_from_tool_args_explicit_claude_plan():
    plan = plan_from_tool_args(
        message="top listings for this window",
        fields=["listing_breakdown"],
        answer_plan_raw={
            "intent": "listing_rank",
            "blocks": ["header", "top_listings"],
            "basis": ["current", "ly_final"],
            "confidence": 0.95,
        },
    )
    assert plan.source == "claude_tool"
    assert plan.intent == "listing_rank"
    assert "top_listings" in plan.blocks
    assert "performance" not in plan.blocks


def test_plan_from_tool_args_infers_compare_from_message():
    plan = plan_from_tool_args(
        message="which did better last year: post oak or great lodge king room?",
        property_token="onera",
        fields=None,
    )
    assert plan.intent == "listing_compare"
    assert plan.source == "tool_inferred"
    assert "Post Oak" in plan.listings
    assert "Great Lodge: King Room" in plan.listings
    assert plan.basis == ("ly_final",)


def test_plan_from_tool_args_defaults_to_portfolio():
    plan = plan_from_tool_args(
        message="Onera Dec 29 2026 to Jan 1 2027",
        property_token="onera",
        fields=None,
    )
    assert plan.intent == "portfolio_snapshot"
    assert "performance" in plan.blocks
