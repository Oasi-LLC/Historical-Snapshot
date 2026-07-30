from __future__ import annotations

import pytest

from historical_snapshot.chat.intent_router import (
    parse_intent_router_response,
    _extract_json_object,
)
from historical_snapshot.chat.thread_state import TurnKind, TurnClassification


@pytest.fixture
def min_confidence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTENT_ROUTER_MIN_CONFIDENCE", "0.6")


def test_extract_json_object_from_fenced_text():
    payload = _extract_json_object('Here is the result:\n{"kind":"follow_up","confidence":0.9}')
    assert payload is not None
    assert payload["kind"] == "follow_up"


def test_parse_follow_up(min_confidence):
    result = parse_intent_router_response(
        {
            "kind": "follow_up",
            "confidence": 0.88,
            "extra_fields": ["listing_breakdown"],
            "reasoning": "User wants unit rankings for same window",
        }
    )
    assert result is not None
    assert result.kind == TurnKind.FOLLOW_UP
    assert "listing_breakdown" in result.extra_fields
    assert result.confidence == 0.88


def test_parse_below_confidence_returns_none(min_confidence):
    result = parse_intent_router_response(
        {"kind": "follow_up", "confidence": 0.4, "extra_fields": []}
    )
    assert result is None


def test_parse_date_override_merges_fields(min_confidence):
    result = parse_intent_router_response(
        {
            "kind": "date_override",
            "confidence": 0.91,
            "start_date": "2026-12-24",
            "end_date": "2026-12-27",
        }
    )
    assert result is not None
    assert result.kind == TurnKind.DATE_OVERRIDE
    assert result.query_override == {
        "start_date": "2026-12-24",
        "end_date": "2026-12-27",
    }


def test_parse_new_topic_returns_none(min_confidence):
    assert parse_intent_router_response({"kind": "new_topic", "confidence": 0.99}) is None


def test_parse_listing_drilldown(min_confidence):
    result = parse_intent_router_response(
        {
            "kind": "listing_drilldown",
            "confidence": 0.85,
            "listing_name": "Gallery House",
        }
    )
    assert result is not None
    assert result.listing_name == "Gallery House"
    assert "listing_breakdown" in result.extra_fields


def test_classify_turn_hybrid_uses_rules_without_llm(monkeypatch, min_confidence):
    monkeypatch.setenv("INTENT_ROUTER_ENABLED", "false")
    from historical_snapshot.chat.intent_router import classify_turn_hybrid
    from historical_snapshot.chat.thread_state import confirmed_state_from_query

    state = confirmed_state_from_query(
        {
            "property": "lafave",
            "start_date": "2026-11-25",
            "end_date": "2026-11-29",
        },
        channel_id="C1",
        thread_ts="1.0",
    )
    classification, source = classify_turn_hybrid(
        "top listings",
        state,
        data_root="data",
        channel_id="C1",
    )
    assert classification.kind == TurnKind.FOLLOW_UP
    assert source == "thread_classifier"
