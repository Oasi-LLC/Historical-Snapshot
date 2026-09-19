from __future__ import annotations

from historical_snapshot.chat.tools import (
    ALL_FIELDS,
    OPTIONAL_FIELDS,
    _adr_distribution,
    _resolve_optional_field,
    _select_fields,
)


def _portfolio(**overrides):
    base = {
        "room_revenue": 12450.0,
        "adr": 692.0,
        "adr_median": 650.0,
        "adr_max": 900.0,
        "adr_p25": 500.0,
        "adr_p75": 780.0,
        "day_type_metrics": {
            "sun_thu": {"occupancy_pct": 60.0, "adr_weighted": 610.0, "revpar": 366.0},
            "fri_sat": {"occupancy_pct": 85.0, "adr_weighted": 780.0, "revpar": 663.0},
        },
        "dow_metrics": {
            "Mon": {"occupancy_pct": 55.0, "adr_weighted": 600.0, "revpar": 330.0},
            "Fri": {"occupancy_pct": 90.0, "adr_weighted": 800.0, "revpar": 720.0},
        },
        "channel_deep_metrics": {
            "Airbnb": {"revenue_share_pct": 62.0, "revpar_of_total_available": 410.0},
            "Direct": {"revenue_share_pct": 38.0, "revpar_of_total_available": 250.0},
        },
    }
    base.update(overrides)
    return base


def test_new_optional_fields_are_registered():
    for field in (
        "day_type_breakdown",
        "dow_breakdown",
        "channel_deep_metrics",
        "adr_distribution",
    ):
        assert field in OPTIONAL_FIELDS
        assert field in ALL_FIELDS


def test_select_fields_accepts_new_optional_fields():
    selected = _select_fields(["dow_breakdown", "adr_distribution"])
    assert "dow_breakdown" in selected
    assert "adr_distribution" in selected
    # defaults are still present alongside the requested optional fields
    assert "room_revenue" in selected


def test_day_type_breakdown_reads_engine_precomputed_metrics():
    pace = _portfolio()
    out = _resolve_optional_field(
        "day_type_breakdown", result=None, pace=pace, prior_pace=None, prior_final=None
    )
    assert out["current"]["fri_sat"]["occupancy_pct"] == 85.0
    assert out["ly_pace"] == {}
    assert out["ly_final"] == {}


def test_dow_breakdown_reads_engine_precomputed_metrics():
    pace = _portfolio()
    out = _resolve_optional_field(
        "dow_breakdown", result=None, pace=pace, prior_pace=None, prior_final=None
    )
    assert out["current"]["Fri"]["revpar"] == 720.0


def test_channel_deep_metrics_reads_engine_precomputed_metrics():
    pace = _portfolio()
    out = _resolve_optional_field(
        "channel_deep_metrics", result=None, pace=pace, prior_pace=None, prior_final=None
    )
    assert out["current"]["Airbnb"]["revpar_of_total_available"] == 410.0


def test_adr_distribution_pulls_mean_median_max_percentiles():
    pace = _portfolio()
    dist = _adr_distribution(pace)
    assert dist == {
        "mean": 692.0,
        "median": 650.0,
        "max": 900.0,
        "p25": 500.0,
        "p75": 780.0,
    }


def test_adr_distribution_none_when_portfolio_missing():
    assert _adr_distribution(None) is None


def test_resolve_optional_field_adr_distribution_across_bases():
    out = _resolve_optional_field(
        "adr_distribution",
        result=None,
        pace=_portfolio(),
        prior_pace=_portfolio(adr=610.0, adr_median=590.0),
        prior_final=None,
    )
    assert out["current"]["mean"] == 692.0
    assert out["ly_pace"]["mean"] == 610.0
    assert out["ly_final"] is None


def test_missing_metrics_degrade_to_empty_dict_not_crash():
    # A snapshot that hasn't computed these yet (or an older cached payload)
    # should not blow up field resolution - the engine always populates these
    # today, but the resolver should stay defensive regardless.
    out = _resolve_optional_field(
        "dow_breakdown", result=None, pace={}, prior_pace=None, prior_final=None
    )
    assert out["current"] == {}
