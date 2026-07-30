from __future__ import annotations

from typing import Any

from historical_snapshot.chat.answer_plan import AnswerPlan
from historical_snapshot.chat.formatter import (
    append_channel_breakdown_tables,
    append_listing_breakdown_tables,
    format_listing_compare_report,
    format_reply,
    _report_header_lines,
)
from historical_snapshot.chat.models import ChatSnapshotResult


def render_answer_plan(
    plan: AnswerPlan,
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
) -> str:
    """Compose Slack report text from an AnswerPlan + snapshot payload.

    Unknown / empty plans degrade safely: no blocks → empty string (interpretation-only).
    """
    if not plan.blocks or plan.blocks == ("none",):
        return ""

    # Specialized intents that already have dedicated formatters — keep one code path.
    if plan.intent == "listing_compare" or "listing_compare" in plan.blocks:
        compare_metrics = dict(metrics)
        compare_metrics["listing_compare"] = {
            "names": list(plan.listings),
            "focus": _focus_from_basis(plan.basis),
        }
        if "listing_breakdown" not in compare_metrics:
            compare_metrics["listing_breakdown"] = metrics.get("listing_breakdown") or {}
        return format_listing_compare_report(result, compare_metrics)

    if plan.intent == "listing_detail" or (
        result.query.listing_name and "performance" in plan.blocks and "top_listings" not in plan.blocks
    ):
        return format_reply(result)

    if plan.intent == "listing_rank" or (
        "top_listings" in plan.blocks and "performance" not in plan.blocks
    ):
        return _render_top_listings(plan, result, metrics)

    if plan.intent == "channel_mix" or (
        "channel_mix" in plan.blocks and "performance" not in plan.blocks
    ):
        return _render_channel_mix(plan, result, metrics)

    if "performance" in plan.blocks:
        report = format_reply(result)
        if result.query.listing_name:
            return report
        extras: list[str] = []
        if "top_listings" in plan.blocks:
            listing_payload = metrics.get("listing_breakdown") or {}
            if listing_payload.get("current") or listing_payload.get("ly_final"):
                extras.append(
                    append_listing_breakdown_tables(
                        "",
                        current_rows=list(listing_payload.get("current") or []),
                        ly_final_rows=list(listing_payload.get("ly_final") or []),
                    )
                )
        if "channel_mix" in plan.blocks:
            channel_payload = metrics.get("channel_breakdown") or {}
            extras.append(
                append_channel_breakdown_tables(
                    "",
                    current_mix=dict(channel_payload.get("current") or {}),
                    ly_pace_mix=dict(channel_payload.get("ly_pace") or {}),
                    ly_final_mix=dict(channel_payload.get("ly_final") or {}),
                )
            )
        parts = [report] + [part for part in extras if part.strip()]
        return "\n\n".join(parts).strip()

    # Generic header-only / custom assembly
    sections: list[str] = []
    if "header" in plan.blocks:
        subtitle = plan.subtitle
        sections.append("\n".join(_report_header_lines(result, subtitle=f"_{subtitle}_" if subtitle else None)))
    if "top_listings" in plan.blocks:
        sections.append(_render_top_listings(plan, result, metrics))
    if "channel_mix" in plan.blocks:
        sections.append(_render_channel_mix(plan, result, metrics))
    return "\n\n".join(section for section in sections if section.strip()).strip()


def _focus_from_basis(basis: tuple[str, ...]) -> str:
    has_ly = "ly_final" in basis
    has_current = "current" in basis
    if has_ly and has_current:
        return "both"
    if has_current and not has_ly:
        return "current"
    return "ly_final"


def _render_top_listings(
    plan: AnswerPlan,
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
) -> str:
    listing_payload = metrics.get("listing_breakdown") or {}
    header = _report_header_lines(
        result,
        subtitle=f"_{plan.subtitle}_" if plan.subtitle else "_Listing breakdown for this stay window._",
    )
    body = append_listing_breakdown_tables(
        "",
        current_rows=list(listing_payload.get("current") or []) if "current" in plan.basis else [],
        ly_final_rows=list(listing_payload.get("ly_final") or []) if "ly_final" in plan.basis else [],
        limit=plan.top_n or 5,
    )
    return "\n".join(header).strip() + ("\n\n" + body if body else "")


def _render_channel_mix(
    plan: AnswerPlan,
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
) -> str:
    channel_payload = metrics.get("channel_breakdown") or {}
    header = _report_header_lines(
        result,
        subtitle=f"_{plan.subtitle}_" if plan.subtitle else "_Channel mix for this stay window._",
    )
    body = append_channel_breakdown_tables(
        "",
        current_mix=dict(channel_payload.get("current") or {}) if "current" in plan.basis else {},
        ly_pace_mix=dict(channel_payload.get("ly_pace") or {}) if "ly_pace" in plan.basis else {},
        ly_final_mix=dict(channel_payload.get("ly_final") or {}) if "ly_final" in plan.basis else {},
    )
    return "\n".join(header).strip() + ("\n\n" + body if body else "")
