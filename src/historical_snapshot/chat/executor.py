from __future__ import annotations

from datetime import date
from typing import Any

from historical_snapshot.chat.format_utils import shift_one_year
from historical_snapshot.chat.models import ChatSnapshotQuery, ChatSnapshotResult
from historical_snapshot.chat.parser_rules import _normalize_listing_key
from historical_snapshot.core.bands import DEFAULT_BANDS
from historical_snapshot.service import run_snapshot


def _portfolio_rev(snapshot: dict | None) -> float | None:
    if snapshot is None:
        return None
    ps = snapshot.get("portfolio_snapshot") or snapshot
    v = ps.get("room_revenue")
    return float(v) if v is not None else None


def _portfolio_bk(snapshot: dict | None) -> int | None:
    if snapshot is None:
        return None
    ps = snapshot.get("portfolio_snapshot") or snapshot
    v = ps.get("bookings_count")
    return int(v) if v is not None else None


def _portfolio_nights(snapshot: dict | None) -> int | None:
    if snapshot is None:
        return None
    ps = snapshot.get("portfolio_snapshot") or snapshot
    v = ps.get("room_nights_sold")
    return int(v) if v is not None else None


def _build_gap_summary(
    pace_prior: dict | None,
    prior_final: dict | None,
) -> dict[str, Any] | None:
    """Pre-compute LY pace-to-final deltas so the LLM can cite them directly.

    Using pre-computed deltas prevents the model from performing arithmetic
    on tool figures (forbidden by the RM doctrine tripwires).
    """
    if pace_prior is None or prior_final is None:
        return None

    pace_rev = _portfolio_rev(pace_prior)
    final_rev = _portfolio_rev(prior_final)
    if pace_rev is None or final_rev is None:
        return None

    pace_bk = _portfolio_bk(pace_prior)
    final_bk = _portfolio_bk(prior_final)
    pace_nights = _portfolio_nights(pace_prior)
    final_nights = _portfolio_nights(prior_final)

    return {
        "ly_pace_rev": pace_rev,
        "ly_final_rev": final_rev,
        "ly_delta_rev": round(final_rev - pace_rev, 2),
        "ly_pace_bk": pace_bk,
        "ly_final_bk": final_bk,
        "ly_delta_bk": (
            (final_bk - pace_bk)
            if (pace_bk is not None and final_bk is not None)
            else None
        ),
        "ly_pace_nights": pace_nights,
        "ly_final_nights": final_nights,
        "ly_delta_nights": (
            (final_nights - pace_nights)
            if (pace_nights is not None and final_nights is not None)
            else None
        ),
    }


def _find_listing_row(breakdown: list[dict], listing_name: str) -> dict | None:
    target = _normalize_listing_key(listing_name)
    if not target:
        return None
    for row in breakdown:
        bucket = _normalize_listing_key(str(row.get("property_id") or row.get("property_name") or ""))
        if bucket == target:
            return row
    return None


def _snapshot_params(
    query: ChatSnapshotQuery,
    *,
    start: date,
    end: date,
    as_of_date: date | None = None,
    yoy_compare_start: date | None = None,
    yoy_compare_end: date | None = None,
) -> dict:
    params = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "property_folder": query.property_folder,
        "property_id": query.property_id,
        "property_name": query.property_name,
        "date_basis": query.date_basis,
        "breakdown_by": query.breakdown_by,
        "bands": DEFAULT_BANDS,
        "inventory_listings": query.inventory_listings,
    }
    if query.compare_listings:
        # A cross-listing compare must retain both named listings in the
        # breakdown even if one (or both) fails the YoY comparable_listings
        # filter - otherwise a brand-new unit silently drops out and the
        # compare falls back to the "no units live in both windows" message
        # instead of a real side-by-side.
        params["include_listings_in_breakdown"] = query.compare_listings
    if as_of_date is not None:
        params["as_of_date"] = as_of_date.isoformat()
    if yoy_compare_start is not None and yoy_compare_end is not None:
        params["yoy_compare_start_date"] = yoy_compare_start.isoformat()
        params["yoy_compare_end_date"] = yoy_compare_end.isoformat()
    return params


def _run(query: ChatSnapshotQuery, *, data_root: str, **kwargs) -> dict:
    return run_snapshot(**_snapshot_params(query, **kwargs), data_root=data_root).to_dict()


def _prior_stay_window(query: ChatSnapshotQuery) -> tuple[date, date]:
    if query.prior_stay_start_date and query.prior_stay_end_date:
        start = query.prior_stay_start_date
        end = query.prior_stay_end_date
        if end < start:
            start, end = end, start
        return start, end
    return shift_one_year(query.start_date), shift_one_year(query.end_date)


def _listing_rows(
    snapshots: dict[str, dict | None],
    listing_name: str,
) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for key, snapshot in snapshots.items():
        if snapshot is None:
            out[key] = None
        else:
            out[key] = _find_listing_row(
                snapshot.get("breakdown_snapshots") or [],
                listing_name,
            )
    return out


def execute_query(query: ChatSnapshotQuery, *, data_root: str = "data") -> ChatSnapshotResult:
    compare_start, compare_end = _prior_stay_window(query)
    pace_as_of_current = date.today()
    pace_as_of_prior = shift_one_year(date.today())

    yoy_start = compare_start if query.inventory_mode == "live_listings" else None
    yoy_end = compare_end if query.inventory_mode == "live_listings" else None

    current_total = _run(
        query,
        data_root=data_root,
        start=query.start_date,
        end=query.end_date,
        yoy_compare_start=yoy_start,
        yoy_compare_end=yoy_end,
    )

    pace_current = _run(
        query,
        data_root=data_root,
        start=query.start_date,
        end=query.end_date,
        as_of_date=pace_as_of_current,
        yoy_compare_start=yoy_start,
        yoy_compare_end=yoy_end,
    )

    pace_prior = None
    prior_final = None
    if query.compare_prior_year:
        pace_prior = _run(
            query,
            data_root=data_root,
            start=compare_start,
            end=compare_end,
            as_of_date=pace_as_of_prior,
            yoy_compare_start=query.start_date if query.inventory_mode == "live_listings" else None,
            yoy_compare_end=query.end_date if query.inventory_mode == "live_listings" else None,
        )
        prior_final = _run(
            query,
            data_root=data_root,
            start=compare_start,
            end=compare_end,
            yoy_compare_start=query.start_date if query.inventory_mode == "live_listings" else None,
            yoy_compare_end=query.end_date if query.inventory_mode == "live_listings" else None,
        )

    comparable = current_total.get("comparable_listings_used") or []

    listing_snapshots: dict[str, dict | None] = {}
    if query.listing_name:
        listing_snapshots = _listing_rows(
            {
                "current_total": current_total,
                "pace_current": pace_current,
                "pace_prior": pace_prior,
                "prior_final": prior_final,
            },
            query.listing_name,
        )

    gap_summary = _build_gap_summary(pace_prior, prior_final)

    return ChatSnapshotResult(
        query=query,
        current_total=current_total,
        pace_current=pace_current,
        pace_prior=pace_prior,
        prior_final=prior_final,
        pace_as_of_current=pace_as_of_current,
        pace_as_of_prior=pace_as_of_prior,
        listing_current_total=listing_snapshots.get("current_total"),
        listing_pace_current=listing_snapshots.get("pace_current"),
        listing_pace_prior=listing_snapshots.get("pace_prior"),
        listing_prior_final=listing_snapshots.get("prior_final"),
        comparable_listings=list(comparable),
        gap_summary=gap_summary,
    )
