from __future__ import annotations

from historical_snapshot.chat.thread_state import (
    TurnKind,
    attach_thread_footer,
    classify_turn,
    confirmed_state_from_query,
    extract_thread_footer,
    pending_state_from_proposed,
    query_dict_from_proposed,
    resolve_query_for_turn,
)


def _confirmed_state(**query):
    base = {
        "property": "lafave",
        "property_folder": "lafave",
        "start_date": "2026-11-25",
        "end_date": "2026-11-29",
        "prior_start_date": "2025-11-26",
        "prior_end_date": "2025-11-30",
    }
    base.update(query)
    return confirmed_state_from_query(
        base,
        channel_id="C123",
        thread_ts="111.222",
    )


def test_footer_round_trip():
    state = _confirmed_state()
    text = attach_thread_footer("Report body", state.to_footer())
    assert text == "Report body"
    assert "<!-- snapshot:" not in text

    legacy = 'Report body\n<!-- snapshot:{"status":"confirmed","last_query":{"start_date":"2026-11-25"}} -->'
    footer = extract_thread_footer(legacy)
    assert footer is not None
    assert footer["status"] == "confirmed"


def test_query_dict_from_proposed():
    proposed = {
        "property": "lafave",
        "start_date": "2026-11-25",
        "end_date": "2026-11-29",
        "prior_start_date": "2025-11-26",
        "prior_end_date": "2025-11-30",
    }
    query = query_dict_from_proposed(proposed)
    assert query is not None
    assert query["property"] == "lafave"
    assert query["prior_start_date"] == "2025-11-26"


def test_pending_state_from_proposed():
    state = pending_state_from_proposed(
        {
            "property": "lafave",
            "start_date": "2026-11-25",
            "end_date": "2026-11-29",
        },
        channel_id="C123",
        thread_ts="111.222",
    )
    assert state is not None
    assert state.status == "pending"
    assert state.pending_query["start_date"] == "2026-11-25"


def test_classify_confirm():
    state = pending_state_from_proposed(
        {
            "property": "lafave",
            "start_date": "2026-11-25",
            "end_date": "2026-11-29",
        },
        channel_id="C123",
        thread_ts="111.222",
    )
    turn = classify_turn("yes", state)
    assert turn.kind == TurnKind.CONFIRM
    query = resolve_query_for_turn(turn, state)
    assert query["start_date"] == "2026-11-25"


def test_classify_listing_follow_up():
    state = _confirmed_state()
    turn = classify_turn("top listings for that window", state)
    assert turn.kind == TurnKind.FOLLOW_UP
    assert "listing_breakdown" in turn.extra_fields


def test_classify_date_override():
    state = _confirmed_state()
    turn = classify_turn("For 2026, use Nov 24-27 as the window", state)
    assert turn.kind == TurnKind.DATE_OVERRIDE
    assert turn.query_override["start_date"] == "2026-11-24"
    assert turn.query_override["end_date"] == "2026-11-27"


def test_classify_new_property_passes_through():
    state = _confirmed_state()
    turn = classify_turn("How is WMB pacing for Thanksgiving 2026?", state)
    assert turn.kind == TurnKind.PASS


def test_classify_interpret_only():
    state = _confirmed_state()
    turn = classify_turn("why are we behind on revenue?", state)
    assert turn.kind == TurnKind.INTERPRET_ONLY


def test_two_listings_without_compare_language_does_not_force_compare():
    """Regression: mentioning two known listings isn't itself a request to compare them.

    LISTING_COMPARE_PATTERN exists specifically to gate this, but was previously defined
    and never referenced - classify_turn matched on listing count alone, so any follow-up
    naming two units (for any reason) was forced into a side-by-side compare table/reply
    mode instead of being left for the LLM router to interpret.
    """
    state = _confirmed_state(property="onera", property_folder="onera")
    turn = classify_turn(
        "Post Oak and Great Lodge King Room both had guests mention slow wifi",
        state,
    )
    assert turn.kind != TurnKind.LISTING_COMPARE
