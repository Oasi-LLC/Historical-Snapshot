from __future__ import annotations

from pathlib import Path

import pytest

from historical_snapshot.chat.interaction_log import (
    InteractionEvent,
    RouteInfo,
    ActionInfo,
    OutcomeInfo,
    ReportSummary,
    append_interaction_event,
    build_interaction_event,
    interaction_logging_enabled,
    iter_interaction_events,
    load_channel_profile,
    tokenize_for_retrieval,
)
from historical_snapshot.chat.interaction_retrieval import (
    channel_context_summary,
    find_similar_interactions,
)


@pytest.fixture
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("INTERACTION_LOGGING", "true")
    return tmp_path


def _sample_event(
    *,
    message: str = "top listings for thanksgiving",
    channel_id: str = "C123",
    route_kind: str = "follow_up",
) -> InteractionEvent:
    return build_interaction_event(
        message=message,
        response_parser="thread",
        route_kind=route_kind,
        route_source="thread_classifier",
        ok=True,
        needs_clarification=False,
        confidence=0.9,
        query={
            "property": "lafave",
            "property_folder": "lafave",
            "start_date": "2026-11-25",
            "end_date": "2026-11-29",
        },
        channel_id=channel_id,
        thread_ts="111.222",
        user_id="UABC",
        conversation=[{"role": "user", "content": "prior"}],
        thread_state_status="confirmed",
        fields_used=["listing_breakdown"],
        tool="resolve_and_run_snapshot",
    )


def test_interaction_logging_enabled_by_default():
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("INTERACTION_LOGGING", raising=False)
        assert interaction_logging_enabled() is True


def test_tokenize_for_retrieval():
    tokens = tokenize_for_retrieval("How is LaFave pacing for Thanksgiving 2026?")
    assert "lafave" in tokens
    assert "thanksgiving" in tokens
    assert "how" not in tokens


def test_append_and_load_interaction_event(tmp_data: Path):
    event = _sample_event()
    append_interaction_event(event, data_root=tmp_data)
    loaded = iter_interaction_events(tmp_data, limit=10)
    assert len(loaded) == 1
    assert loaded[0].message == event.message
    assert loaded[0].route.kind == "follow_up"


def test_channel_profile_aggregates(tmp_data: Path):
    append_interaction_event(_sample_event(), data_root=tmp_data)
    append_interaction_event(
        _sample_event(message="yes", route_kind="confirm"),
        data_root=tmp_data,
    )
    profile = load_channel_profile(tmp_data, "C123")
    assert profile is not None
    assert profile["interaction_count"] == 2
    assert profile["properties"]["lafave"] == 2
    assert profile["route_kinds"]["follow_up"] == 1


def test_find_similar_interactions(tmp_data: Path):
    append_interaction_event(_sample_event(), data_root=tmp_data)
    append_interaction_event(
        _sample_event(
            message="which units are booking best for thanksgiving",
            route_kind="follow_up",
        ),
        data_root=tmp_data,
    )
    similar = find_similar_interactions(
        "top listings thanksgiving",
        data_root=str(tmp_data),
        channel_id="C123",
        limit=3,
    )
    assert similar
    assert similar[0].score > 0


def test_channel_context_summary(tmp_data: Path):
    append_interaction_event(_sample_event(), data_root=tmp_data)
    summary = channel_context_summary(str(tmp_data), "C123")
    assert summary["interaction_count"] == 1
    assert summary["top_properties"]


def test_event_round_trip_json():
    event = InteractionEvent(
        event_id="abc",
        ts="2026-07-29T12:00:00+00:00",
        channel_id="C1",
        thread_ts="1.2",
        user_id_hash="deadbeef",
        message="hello",
        message_tokens=["hello"],
        thread_turn_index=1,
        conversation_turns=0,
        thread_state_status="confirmed",
        route=RouteInfo(kind="snapshot", source="claude"),
        action=ActionInfo(tool="resolve_and_run_snapshot", fields=["listing_breakdown"]),
        outcome=OutcomeInfo(ok=True, parser="claude"),
        report_summary=ReportSummary(property_folder="wmb"),
        similar_events=[{"score": 0.5, "message": "prior"}],
    )
    restored = InteractionEvent.from_dict(event.to_dict())
    assert restored.event_id == "abc"
    assert restored.similar_events[0]["score"] == 0.5
