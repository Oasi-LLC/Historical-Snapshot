from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from historical_snapshot.chat.executor import execute_query
from historical_snapshot.chat.formatter import build_formatted_report
from historical_snapshot.chat.models import ChatSnapshotQuery
from historical_snapshot.chat.parser_rules import PROPERTY_ALIASES, _inventory_for
from historical_snapshot.chat.plan_renderer import render_answer_plan
from historical_snapshot.chat.slack_format import format_clarification
from historical_snapshot.config import load_property_config, load_property_config_by_id, list_property_configs

DEFAULT_METRICS: list[str] = [
    "room_revenue",
    "bookings_count",
    "room_nights_sold",
    "adr",
    "occupancy_pct",
    "revpar",
    "average_los",
    "booking_window",
]

OPTIONAL_FIELDS: list[str] = [
    "listing_breakdown",
    "channel_breakdown",
    "los_distribution",
    "cancellations",
    "cancellation_rate",
    "lead_time_distribution",
]

ALL_FIELDS: list[str] = DEFAULT_METRICS + OPTIONAL_FIELDS

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "list_properties",
        "description": "List available properties, ids, folders, inventory modes, and aliases.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "resolve_and_run_snapshot",
        "description": (
            "Run the historical snapshot once property and stay dates are confirmed. "
            "Returns structured metrics plus a ready-to-post Slack formatted_report. "
            "Default LY compare uses calendar shift; pass prior_start_date/prior_end_date "
            "when the user confirmed a custom prior-year holiday window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "property": {
                    "type": "string",
                    "description": "Property folder, property_id, name, or alias.",
                },
                "start_date": {"type": "string", "description": "Stay start YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Stay end YYYY-MM-DD."},
                "prior_start_date": {
                    "type": "string",
                    "description": (
                        "Optional LY stay start when holiday-aligned compare was confirmed "
                        "(omit for default calendar-shift LY)."
                    ),
                },
                "prior_end_date": {
                    "type": "string",
                    "description": "Optional LY stay end for holiday-aligned compare.",
                },
                "listing_name": {"type": "string", "description": "Optional listing/unit filter."},
                "compare_prior_year": {
                    "type": "boolean",
                    "description": "Include YoY pace/final comparisons. Default true.",
                },
                "fields": {
                    "type": "array",
                    "description": (
                        "Metrics to return. Defaults to core portfolio metrics. "
                        "Add listing_breakdown for top units/listings; channel_breakdown "
                        "for channel mix; lead_time_distribution for booking-window bands."
                    ),
                    "items": {"type": "string", "enum": list(ALL_FIELDS)},
                },
                "answer_plan": {
                    "type": "object",
                    "description": (
                        "Optional compositional answer shape. Controls which Slack blocks "
                        "are rendered (not the full portfolio dump by default). "
                        "intent: portfolio_snapshot|listing_rank|channel_mix|listing_detail|"
                        "listing_compare|explain|custom. "
                        "blocks: header, performance, top_listings, listing_compare, "
                        "channel_mix, none. "
                        "basis: current, ly_pace, ly_final. "
                        "listings: exact unit names when comparing or drilling down."
                    ),
                    "properties": {
                        "intent": {"type": "string"},
                        "basis": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "blocks": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "listings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "include_interpretation": {"type": "boolean"},
                        "interpretation_mode": {"type": "string"},
                        "subtitle": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["property", "start_date", "end_date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "clarify",
        "description": (
            "Ask for missing property, stay window, or performance scope. "
            "For holidays, weekends, or ambiguous periods: suggest date ranges in proposed "
            "but do not run the snapshot until the user confirms."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Slack-ready clarification for the user.",
                },
                "questions": {
                    "type": "array",
                    "description": (
                        "Missing items: property, stay_window, compare_window, performance_scope."
                    ),
                    "items": {"type": "string"},
                },
                "proposed": {
                    "type": "object",
                    "description": (
                        "Suggested defaults awaiting confirmation: property, start_date, end_date, "
                        "prior_start_date, prior_end_date (when LY holiday window differs from "
                        "calendar shift), intent."
                    ),
                    "properties": {
                        "property": {"type": "string"},
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                        "prior_start_date": {"type": "string"},
                        "prior_end_date": {"type": "string"},
                        "intent": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    },
]


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _resolve_property_config(token: str):
    raw = (token or "").strip()
    if not raw:
        return None

    lowered = raw.lower()
    folder = PROPERTY_ALIASES.get(lowered)
    if folder:
        return load_property_config(folder)

    by_id = load_property_config_by_id(raw)
    if by_id:
        return by_id

    for config in list_property_configs():
        if config.property_name.lower() == lowered or config.folder.lower() == lowered:
            return config
        if lowered in config.property_name.lower():
            return config
    return None


def list_properties_payload() -> dict[str, Any]:
    items = []
    for config in list_property_configs():
        aliases = sorted(
            alias for alias, folder in PROPERTY_ALIASES.items() if folder == config.folder.lower()
        )
        items.append(
            {
                "folder": config.folder,
                "property_id": config.property_id,
                "property_name": config.property_name,
                "inventory_mode": config.inventory_mode,
                "aliases": aliases,
            }
        )
    return {"properties": items}


def _pct_change_value(current: float | int | None, prior: float | int | None) -> float | None:
    if current is None or prior is None:
        return None
    prior_f = float(prior)
    if prior_f == 0:
        return None
    return ((float(current) - prior_f) / prior_f) * 100


def _pickup_summary(portfolio: dict | None, *, limit: int = 4) -> dict[str, Any] | None:
    if not portfolio:
        return None
    pickup = portfolio.get("pickup") or {}
    revenue_by_band = pickup.get("revenue_by_band") or {}
    bw = portfolio.get("booking_window") or {}
    if not revenue_by_band:
        return {
            "mean_days": bw.get("mean_days"),
            "median_days": bw.get("median_days"),
            "top_bands": [],
        }
    bands = sorted(revenue_by_band.items(), key=lambda item: float(item[1] or 0), reverse=True)
    top_bands = []
    for band, revenue in bands[:limit]:
        top_bands.append(
            {
                "band": band,
                "revenue": revenue,
                "bookings": (pickup.get("bookings_by_band") or {}).get(band),
                "revenue_share_pct": (pickup.get("revenue_share_pct_by_band") or {}).get(band),
            }
        )
    return {
        "mean_days": bw.get("mean_days"),
        "median_days": bw.get("median_days"),
        "top_bands": top_bands,
    }


def _select_fields(fields: list[str] | None) -> list[str]:
    selected: list[str] = list(DEFAULT_METRICS)
    if fields:
        allowed = set(ALL_FIELDS)
        for field in fields:
            name = str(field).strip()
            if name in allowed and name not in selected:
                selected.append(name)
    return selected


def _build_metric(
    field: str,
    pace: dict | None,
    prior_pace: dict | None,
    prior_final: dict | None,
) -> dict[str, Any]:
    if field == "booking_window":
        return {
            "current": _pickup_summary(pace),
            "ly_pace": _pickup_summary(prior_pace),
            "ly_final": _pickup_summary(prior_final),
        }

    cur = pace.get(field) if pace else None
    ly_p = prior_pace.get(field) if prior_pace else None
    ly_f = prior_final.get(field) if prior_final else None
    return {
        "current": cur,
        "ly_pace": ly_p,
        "ly_final": ly_f,
        "vs_pace_pct": _pct_change_value(cur, ly_p),
        "vs_final_pct": _pct_change_value(cur, ly_f),
    }


def _portfolio_from_snapshot(snapshot: dict | None) -> dict | None:
    if not snapshot:
        return None
    return snapshot.get("portfolio_snapshot") or None


def _listing_label(row: dict[str, Any]) -> str:
    # Breakdown rows put the listing/group bucket in property_id; property_name
    # is the parent property (e.g. "LaFave") and must not be used as the label.
    return str(row.get("property_id") or row.get("property_name") or "Unknown")


def _compact_listing_rows(snapshot: dict | None, *, limit: int = 10) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    rows = list(snapshot.get("breakdown_snapshots") or [])
    rows.sort(key=lambda row: float(row.get("room_revenue") or 0), reverse=True)
    compact = []
    for row in rows[:limit]:
        # The engine already computes a booking-window mean/median for every
        # breakdown row (see core/metrics.py + snapshot_to_dict) - surface it here
        # instead of silently dropping it, so ranking questions ("who books
        # furthest out") can be answered without a separate tool round-trip.
        booking_window = row.get("booking_window") or {}
        compact.append(
            {
                "listing": _listing_label(row),
                "room_revenue": row.get("room_revenue"),
                "bookings_count": row.get("bookings_count"),
                "room_nights_sold": row.get("room_nights_sold"),
                "adr": row.get("adr"),
                "occupancy_pct": row.get("occupancy_pct"),
                "revpar": row.get("revpar"),
                "average_los": row.get("average_los"),
                "booking_window_mean_days": booking_window.get("mean_days"),
                "booking_window_median_days": booking_window.get("median_days"),
            }
        )
    return compact


def _listing_rows_for_names(
    snapshot: dict | None,
    listing_names: list[str],
) -> list[dict[str, Any]]:
    """Pull exact named listings from a breakdown (not top-N truncated).

    Named comparisons/detail views are bounded to a handful of units, so unlike
    _compact_listing_rows we can afford to pass through the full set of
    per-listing metrics the engine already computes (booking window, pickup
    bands, LOS distribution, channel mix, arrival-day mix) rather than just the
    headline revenue/ADR/occupancy figures. Without this, questions like
    "average booking window between X and Y" have no data to answer from even
    though the snapshot engine computed it for every listing already.
    """
    from historical_snapshot.chat.parser_rules import _normalize_listing_key

    if not snapshot or not listing_names:
        return []
    by_key = {
        _normalize_listing_key(_listing_label(row)): row
        for row in (snapshot.get("breakdown_snapshots") or [])
    }
    compact: list[dict[str, Any]] = []
    for name in listing_names:
        row = by_key.get(_normalize_listing_key(name))
        if row is None:
            compact.append(
                {
                    "listing": name,
                    "room_revenue": 0,
                    "bookings_count": 0,
                    "room_nights_sold": 0,
                    "adr": 0,
                    "occupancy_pct": 0,
                    "revpar": 0,
                    "average_los": 1.0,
                    "booking_window": {"mean_days": None, "median_days": None},
                    "pickup": {},
                    "los_distribution": {},
                    "channel_mix": {},
                    "arrival_day_of_week_mix": {},
                }
            )
            continue
        compact.append(
            {
                "listing": _listing_label(row),
                "room_revenue": row.get("room_revenue"),
                "bookings_count": row.get("bookings_count"),
                "room_nights_sold": row.get("room_nights_sold"),
                "adr": row.get("adr"),
                "occupancy_pct": row.get("occupancy_pct"),
                "revpar": row.get("revpar"),
                "average_los": row.get("average_los"),
                "booking_window": row.get("booking_window") or {},
                "pickup": row.get("pickup") or {},
                "los_distribution": row.get("los_distribution") or {},
                "channel_mix": row.get("channel_mix") or {},
                "arrival_day_of_week_mix": row.get("arrival_day_of_week_mix") or {},
            }
        )
    return compact


def _lead_time_distribution(portfolio: dict | None) -> dict[str, Any] | None:
    if not portfolio:
        return None
    pickup = portfolio.get("pickup") or {}
    if not pickup:
        return None
    return {
        "bookings_by_band": pickup.get("bookings_by_band") or {},
        "revenue_by_band": pickup.get("revenue_by_band") or {},
        "revenue_share_pct_by_band": pickup.get("revenue_share_pct_by_band") or {},
        "bookings_share_pct_by_band": pickup.get("bookings_share_pct_by_band") or {},
    }


def _unavailable(field: str) -> dict[str, Any]:
    return {
        "available": False,
        "field": field,
        "reason": "Not computed by the current snapshot engine.",
    }


def _resolve_optional_field(
    field: str,
    *,
    result: Any,
    pace: dict | None,
    prior_pace: dict | None,
    prior_final: dict | None,
    listing_limit: int = 10,
) -> dict[str, Any]:
    if field == "listing_breakdown":
        return {
            "current": _compact_listing_rows(result.pace_current, limit=listing_limit),
            "ly_pace": _compact_listing_rows(result.pace_prior, limit=listing_limit),
            "ly_final": _compact_listing_rows(result.prior_final, limit=listing_limit),
        }

    if field == "channel_breakdown":
        return {
            "current": (pace or {}).get("channel_mix") or {},
            "ly_pace": (prior_pace or {}).get("channel_mix") or {},
            "ly_final": (prior_final or {}).get("channel_mix") or {},
        }

    if field == "los_distribution":
        return {
            "current": (pace or {}).get("los_distribution") or {},
            "ly_pace": (prior_pace or {}).get("los_distribution") or {},
            "ly_final": (prior_final or {}).get("los_distribution") or {},
        }

    if field == "lead_time_distribution":
        return {
            "current": _lead_time_distribution(pace),
            "ly_pace": _lead_time_distribution(prior_pace),
            "ly_final": _lead_time_distribution(prior_final),
        }

    if field in {"cancellations", "cancellation_rate"}:
        return _unavailable(field)

    return {"available": False, "field": field, "reason": f"Unknown optional field: {field}"}


def resolve_and_run_snapshot(
    *,
    property_token: str,
    start_date: str,
    end_date: str,
    prior_start_date: str | None = None,
    prior_end_date: str | None = None,
    listing_name: str | None = None,
    compare_prior_year: bool = True,
    fields: list[str] | None = None,
    reply_mode: str = "full",
    compare_listings: list[str] | None = None,
    compare_focus: str = "ly_final",
    answer_plan: Any | None = None,
    data_root: str = "data",
) -> dict[str, Any]:
    config = _resolve_property_config(property_token)
    if config is None:
        return {
            "ok": False,
            "error": (
                f"Unknown property '{property_token}'. "
                "Call list_properties or clarify with the user."
            ),
        }

    try:
        start = _parse_iso_date(start_date)
        end = _parse_iso_date(end_date)
    except ValueError:
        return {
            "ok": False,
            "error": "Dates must be YYYY-MM-DD. Call clarify if dates are not confirmed.",
        }

    if end < start:
        start, end = end, start

    prior_stay_start = None
    prior_stay_end = None
    if prior_start_date and prior_end_date:
        try:
            prior_stay_start = _parse_iso_date(prior_start_date)
            prior_stay_end = _parse_iso_date(prior_end_date)
        except ValueError:
            return {
                "ok": False,
                "error": "prior_start_date and prior_end_date must be YYYY-MM-DD.",
            }
        if prior_stay_end < prior_stay_start:
            prior_stay_start, prior_stay_end = prior_stay_end, prior_stay_start

    listing = (listing_name or "").strip() or None
    if listing and listing in config.listing_aliases:
        listing = config.listing_aliases[listing]

    compare_names = [str(name).strip() for name in (compare_listings or []) if str(name).strip()]
    if (
        not compare_names
        and answer_plan is not None
        and getattr(answer_plan, "intent", None) == "listing_compare"
    ):
        compare_names = [
            str(name).strip()
            for name in (getattr(answer_plan, "listings", ()) or ())
            if str(name).strip()
        ]
    if compare_names:
        # Comparison is portfolio-scoped; never filter the snapshot to one unit.
        listing = None
        reply_mode = "listing_compare"

    selected_fields = _select_fields(fields)
    if compare_names and "listing_breakdown" not in selected_fields:
        selected_fields.append("listing_breakdown")

    query = ChatSnapshotQuery(
        property_folder=config.folder,
        property_id=config.property_id,
        property_name=config.property_name,
        start_date=start,
        end_date=end,
        listing_name=listing,
        compare_prior_year=bool(compare_prior_year),
        pace=True,
        inventory_listings=_inventory_for(config, start, end),
        inventory_mode=config.inventory_mode,
        prior_stay_start_date=prior_stay_start,
        prior_stay_end_date=prior_stay_end,
    )
    result = execute_query(query, data_root=data_root)

    if listing:
        pace = result.listing_pace_current
        prior_pace = result.listing_pace_prior
        prior_final = result.listing_prior_final
    else:
        pace = _portfolio_from_snapshot(result.pace_current) or {}
        prior_pace = _portfolio_from_snapshot(result.pace_prior)
        prior_final = _portfolio_from_snapshot(result.prior_final)

    # A literal "top N" in the user's own words should widen how many rows we fetch, not
    # just how many the renderer shows - otherwise a property with 15+ listings would
    # silently truncate to 10 rows before "top 15" ever got a chance to render.
    requested_top_n = getattr(answer_plan, "top_n", None)
    listing_limit = max(10, requested_top_n) if requested_top_n else 10

    metrics: dict[str, Any] = {}
    for field in selected_fields:
        if field in OPTIONAL_FIELDS:
            metrics[field] = _resolve_optional_field(
                field,
                result=result,
                pace=pace,
                prior_pace=prior_pace,
                prior_final=prior_final,
                listing_limit=listing_limit,
            )
        else:
            metrics[field] = _build_metric(field, pace, prior_pace, prior_final)

    if compare_names:
        metrics["listing_compare"] = {
            "names": compare_names,
            "focus": compare_focus or "ly_final",
        }
        metrics["listing_breakdown"] = {
            "current": _listing_rows_for_names(result.pace_current, compare_names),
            "ly_pace": _listing_rows_for_names(result.pace_prior, compare_names),
            "ly_final": _listing_rows_for_names(result.prior_final, compare_names),
        }

    if answer_plan is not None:
        report = render_answer_plan(answer_plan, result, metrics)
        reply_mode = getattr(answer_plan, "interpretation_mode", None) or reply_mode
    else:
        report = build_formatted_report(result, metrics, reply_mode=reply_mode)

    payload: dict[str, Any] = {
        "ok": True,
        "query": query.to_dict(),
        "formatted_report": report,
        "fields": selected_fields,
        "metrics": metrics,
        "reply_mode": reply_mode,
        "snapshots": result.to_dict(),
    }
    if answer_plan is not None and hasattr(answer_plan, "to_dict"):
        payload["answer_plan"] = answer_plan.to_dict()
    if result.comparable_listings:
        payload["comparable_units"] = len(result.comparable_listings)
    return payload


class ToolSession:
    """Tracks latest successful snapshot across a Claude tool-use loop."""

    def __init__(self, *, data_root: str = "data", user_message: str = "") -> None:
        self.data_root = data_root
        self.user_message = user_message
        self.last_query: dict[str, Any] | None = None
        self.last_snapshots: dict[str, Any] | None = None
        self.last_formatted_report: str | None = None
        self.last_metrics: dict[str, Any] | None = None
        self.last_answer_plan: dict[str, Any] | None = None
        self.last_reply_mode: str = "full"
        self.clarification: str | None = None
        self.clarification_structured: dict[str, Any] | None = None

    @staticmethod
    def _format_clarification(message: str, proposed: dict | None = None) -> str:
        return format_clarification(message, proposed)

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_properties":
            return list_properties_payload()

        if name == "clarify":
            message = str(arguments.get("message") or "").strip()
            questions = arguments.get("questions")
            proposed = arguments.get("proposed")
            proposed_dict = proposed if isinstance(proposed, dict) else None
            formatted = self._format_clarification(message, proposed_dict)
            self.clarification = formatted
            self.clarification_structured = {
                "questions": list(questions) if isinstance(questions, list) else [],
                "proposed": proposed if isinstance(proposed, dict) else None,
                "message": formatted,
            }
            return {"ok": True, "clarification": formatted, **self.clarification_structured}

        if name == "resolve_and_run_snapshot":
            from historical_snapshot.chat.answer_plan import log_answer_plan, plan_from_tool_args

            raw_fields = arguments.get("fields")
            fields = list(raw_fields) if isinstance(raw_fields, list) else None
            raw_plan = arguments.get("answer_plan")
            plan = plan_from_tool_args(
                message=self.user_message,
                fields=fields,
                listing_name=arguments.get("listing_name"),
                property_token=str(arguments.get("property") or ""),
                answer_plan_raw=raw_plan if isinstance(raw_plan, dict) else None,
            )
            plan = plan.with_top_n_from_message(self.user_message)
            log_answer_plan(plan, path="claude_tool_loop")

            # Merge plan-required fields into the tool field list.
            merged_fields = list(fields or [])
            for field_name in plan.snapshot_fields():
                if field_name not in merged_fields:
                    merged_fields.append(field_name)

            compare_listings = list(plan.listings) if plan.intent == "listing_compare" else None
            listing_name = arguments.get("listing_name")
            if plan.intent == "listing_detail" and plan.listings and not listing_name:
                listing_name = plan.listings[0]
            if plan.intent == "listing_compare":
                listing_name = None

            payload = resolve_and_run_snapshot(
                property_token=str(arguments.get("property") or ""),
                start_date=str(arguments.get("start_date") or ""),
                end_date=str(arguments.get("end_date") or ""),
                prior_start_date=arguments.get("prior_start_date"),
                prior_end_date=arguments.get("prior_end_date"),
                listing_name=listing_name,
                compare_prior_year=bool(arguments.get("compare_prior_year", True)),
                fields=merged_fields or None,
                reply_mode=plan.interpretation_mode,
                compare_listings=compare_listings,
                compare_focus=(
                    "ly_final"
                    if plan.basis == ("ly_final",)
                    else "both"
                    if "ly_final" in plan.basis and "current" in plan.basis
                    else "current"
                    if plan.basis == ("current",)
                    else "ly_final"
                ),
                answer_plan=plan,
                data_root=self.data_root,
            )
            if payload.get("ok"):
                self.last_query = payload.get("query")
                self.last_snapshots = payload.get("snapshots")
                self.last_formatted_report = payload.get("formatted_report")
                self.last_metrics = payload.get("metrics")
                self.last_answer_plan = payload.get("answer_plan") or plan.to_dict()
                self.last_reply_mode = str(payload.get("reply_mode") or plan.interpretation_mode)
                self.clarification = None
            return {
                key: value
                for key, value in payload.items()
                if key != "snapshots"
            }

        return {"ok": False, "error": f"Unknown tool: {name}"}


def tool_result_content(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str)
