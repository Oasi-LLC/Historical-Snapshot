from __future__ import annotations

import time
from typing import Any

from historical_snapshot.chat.interaction_log import build_interaction_event, record_interaction
from historical_snapshot.chat.interaction_retrieval import find_similar_interactions
from historical_snapshot.chat.models import ChatResponse


def similar_event_summaries(
    message: str,
    *,
    data_root: str,
    channel_id: str | None,
) -> list[dict[str, Any]]:
    return [
        {
            "score": round(item.score, 3),
            "message": item.event.message,
            "route_kind": item.event.route.kind,
            "route_source": item.event.route.source,
            "property": item.event.report_summary.property_folder,
        }
        for item in find_similar_interactions(
            message,
            data_root=data_root,
            channel_id=channel_id,
            limit=3,
        )
    ]


def finalize_chat_response(
    *,
    message: str,
    data_root: str,
    channel_id: str | None,
    thread_ts: str | None,
    user_id: str | None,
    conversation: list[dict[str, str]] | None,
    thread_state_status: str | None,
    started_at: float,
    reply: str,
    query: dict[str, Any] | None,
    confidence: float,
    needs_clarification: bool,
    snapshots: dict[str, Any] | None,
    parser: str,
    pending_proposed: dict[str, Any] | None = None,
    route_kind: str | None = None,
    route_source: str | None = None,
    fields_used: list[str] | None = None,
    error: str | None = None,
    tool: str | None = None,
    extra_fields: list[str] | None = None,
    route_confidence: float | None = None,
    answer_plan: dict[str, Any] | None = None,
) -> ChatResponse:
    latency_ms = int((time.perf_counter() - started_at) * 1000)
    kind = route_kind or ("clarify" if needs_clarification else "snapshot")
    source = route_source or parser
    similar = similar_event_summaries(message, data_root=data_root, channel_id=channel_id)
    event = build_interaction_event(
        message=message,
        response_parser=parser,
        route_kind=kind,
        route_source=source,
        ok=error is None,
        needs_clarification=needs_clarification,
        confidence=confidence,
        query=query,
        snapshots=snapshots,
        pending_proposed=pending_proposed,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id=user_id,
        conversation=conversation,
        thread_state_status=thread_state_status,
        route_confidence=route_confidence,
        extra_fields=extra_fields,
        fields_used=fields_used,
        latency_ms=latency_ms,
        error=error,
        tool=tool,
        similar_events=similar,
        answer_plan=answer_plan,
    )
    record_interaction(event, data_root=data_root)
    return ChatResponse(
        reply=reply,
        query=query,
        confidence=confidence,
        needs_clarification=needs_clarification,
        snapshots=snapshots,
        parser=parser,
        pending_proposed=pending_proposed,
        route_kind=kind,
        route_source=source,
        fields_used=fields_used,
        latency_ms=latency_ms,
        error=error,
    )
