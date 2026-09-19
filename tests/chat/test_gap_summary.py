"""Tests for gap_summary pre-computation and the new interpretation renderer."""
from __future__ import annotations

import pytest

from historical_snapshot.chat.executor import _build_gap_summary
from historical_snapshot.chat.slack_format import render_interpretation_struct


# ---------------------------------------------------------------------------
# _build_gap_summary
# ---------------------------------------------------------------------------

def _make_snapshot(rev, bk, nights):
    return {
        "portfolio_snapshot": {
            "room_revenue": rev,
            "bookings_count": bk,
            "room_nights_sold": nights,
        }
    }


def test_gap_summary_normal():
    pace = _make_snapshot(55387.2, 39, 93)
    final = _make_snapshot(58569.2, 42, 98)
    gs = _build_gap_summary(pace, final)
    assert gs is not None
    assert gs["ly_pace_rev"] == pytest.approx(55387.2)
    assert gs["ly_final_rev"] == pytest.approx(58569.2)
    assert gs["ly_delta_rev"] == pytest.approx(3182.0, abs=0.1)
    assert gs["ly_pace_bk"] == 39
    assert gs["ly_final_bk"] == 42
    assert gs["ly_delta_bk"] == 3
    assert gs["ly_delta_nights"] == 5


def test_gap_summary_zero_pace_bookings():
    """pace_bk=0 must NOT drop the delta (truthy check bug)."""
    pace = _make_snapshot(0.0, 0, 0)
    final = _make_snapshot(1500.0, 3, 4)
    gs = _build_gap_summary(pace, final)
    assert gs is not None
    assert gs["ly_delta_bk"] == 3     # 3 - 0, not None
    assert gs["ly_delta_nights"] == 4
    assert gs["ly_delta_rev"] == pytest.approx(1500.0)


def test_gap_summary_zero_final_bookings():
    """final_bk=0 must NOT drop the delta."""
    pace = _make_snapshot(500.0, 2, 3)
    final = _make_snapshot(500.0, 0, 0)
    gs = _build_gap_summary(pace, final)
    assert gs is not None
    assert gs["ly_delta_bk"] == -2    # 0 - 2
    assert gs["ly_delta_nights"] == -3


def test_gap_summary_none_inputs():
    assert _build_gap_summary(None, None) is None
    assert _build_gap_summary(_make_snapshot(100, 1, 1), None) is None
    assert _build_gap_summary(None, _make_snapshot(100, 1, 1)) is None


def test_gap_summary_missing_revenue_fields():
    pace = {"portfolio_snapshot": {"bookings_count": 5}}
    final = {"portfolio_snapshot": {"bookings_count": 6}}
    assert _build_gap_summary(pace, final) is None


# ---------------------------------------------------------------------------
# render_interpretation_struct — new 3-field schema
# ---------------------------------------------------------------------------

def test_render_new_schema_full():
    data = {
        "stay_context": "16 days out — open",
        "analysis": "Revenue is up 6% driven by ADR.",
        "gap_and_action": "Last year added $3,182 from this point.",
    }
    result = render_interpretation_struct(data, reply_mode="full")
    assert result is not None
    assert result.startswith("Interpretation — 16 days out — open")
    assert "1. Revenue is up 6%" in result
    assert "2. Last year added $3,182" in result
    # stay_context must NOT appear as a numbered bullet
    assert "1. 16 days" not in result


def test_render_new_schema_null_gap():
    data = {
        "stay_context": "closed",
        "analysis": "Revenue finished up 10%.",
        "gap_and_action": None,
    }
    result = render_interpretation_struct(data)
    assert result is not None
    assert "Interpretation — closed" in result
    assert "1. Revenue finished up 10%." in result
    # Only one numbered bullet when gap_and_action is null
    assert "2." not in result


def test_render_new_schema_gap_string_null():
    """'null' as a string should also be skipped."""
    data = {
        "stay_context": "closed",
        "analysis": "ADR held flat.",
        "gap_and_action": "null",
    }
    result = render_interpretation_struct(data)
    assert result is not None
    assert "2." not in result


def test_render_new_schema_no_stay_context():
    data = {"analysis": "Pace is ahead.", "gap_and_action": "LY added $500."}
    result = render_interpretation_struct(data)
    assert result is not None
    assert result.startswith("Interpretation\n")


# ---------------------------------------------------------------------------
# render_interpretation_struct — legacy 5-field schema (backward compat)
# ---------------------------------------------------------------------------

def test_render_legacy_schema():
    data = {
        "stay_state": "Open, 10 days out.",
        "primary_comparison": "Revenue up 5%.",
        "driver_decomposition": "ADR-driven.",
        "gap_to_final": "LY added $1k.",
        "action": "Monitor.",
    }
    result = render_interpretation_struct(data)
    assert result is not None
    assert result.startswith("Interpretation\n")
    assert "1. Open, 10 days out." in result
    assert "5. Monitor." in result


# ---------------------------------------------------------------------------
# render_interpretation_struct — flat bullets (reduced modes)
# ---------------------------------------------------------------------------

def test_render_bullets_mode():
    data = {"bullets": ["Listing A is up.", "Listing B is down."]}
    result = render_interpretation_struct(data, reply_mode="delta_listings")
    assert result is not None
    assert "1. Listing A is up." in result
    assert "2. Listing B is down." in result


def test_render_empty_returns_none():
    assert render_interpretation_struct({}) is None
    assert render_interpretation_struct({"bullets": []}) is None
    assert render_interpretation_struct({"stay_context": "open"}) is None  # no bullets
