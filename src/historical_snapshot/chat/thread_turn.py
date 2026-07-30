from __future__ import annotations

import logging
from typing import Any

from historical_snapshot.chat.answer_plan import (
    AnswerPlan,
    log_answer_plan,
    plan_from_classification,
    reply_mode_for_plan,
)
from historical_snapshot.chat.answer_planner import propose_answer_plan
from historical_snapshot.chat.intent_router import classify_turn_hybrid
from historical_snapshot.chat.interaction_record import finalize_chat_response
from historical_snapshot.chat.llm_claude import run_claude_interpretation
from historical_snapshot.chat.models import ChatResponse
from historical_snapshot.chat.reply_modes import compose_thread_reply
from historical_snapshot.chat.thread_state import (
    TurnKind,
    attach_thread_footer,
    confirmed_state_from_query,
    load_thread_state,
    recover_state_from_conversation,
    resolve_query_for_turn,
    save_thread_state,
    snapshot_args_from_query,
)
from historical_snapshot.chat.tools import resolve_and_run_snapshot

LOGGER = logging.getLogger(__name__)


def _merge_fields(base: list[str] | tuple[str, ...]) -> list[str]:
    return list(dict.fromkeys(base))


def _focus_from_basis(basis: tuple[str, ...]) -> str:
    has_ly = "ly_final" in basis
    has_current = "current" in basis
    if has_ly and has_current:
        return "both"
    if has_current and not has_ly:
        return "current"
    return "ly_final"


def _apply_plan_to_query(query: dict[str, Any], plan: AnswerPlan) -> dict[str, Any]:
    updated = dict(query)
    if plan.intent == "listing_compare" and plan.listings:
        updated.pop("listing_name", None)
        updated["compare_listings"] = list(plan.listings)
        updated["compare_focus"] = _focus_from_basis(plan.basis)
    elif plan.intent == "listing_detail" and plan.listings:
        updated["listing_name"] = plan.listings[0]
        updated.pop("compare_listings", None)
        updated.pop("compare_focus", None)
    elif plan.intent in {"listing_rank", "channel_mix", "portfolio_snapshot", "custom"}:
        updated.pop("listing_name", None)
        updated.pop("compare_listings", None)
        updated.pop("compare_focus", None)
    return updated


def _run_snapshot_for_plan(
    query: dict[str, Any],
    plan: AnswerPlan,
    *,
    data_root: str,
) -> dict[str, Any]:
    args = snapshot_args_from_query(query)
    compare_listings = query.get("compare_listings")
    compare_focus = query.get("compare_focus") or _focus_from_basis(plan.basis)
    fields = _merge_fields(list(plan.snapshot_fields()))
    return resolve_and_run_snapshot(
        **args,
        fields=fields or None,
        reply_mode=reply_mode_for_plan(plan),
        compare_listings=list(compare_listings) if isinstance(compare_listings, list) else None,
        compare_focus=str(compare_focus),
        answer_plan=plan,
        data_root=data_root,
    )


def _execute_with_plan(
    message: str,
    *,
    data_root: str,
    channel_id: str,
    thread_ts: str,
    conversation: list[dict[str, str]] | None,
    user_id: str | None,
    started_at: float,
    thread_state_status: str | None,
    state,
    classification,
    route_source: str,
    plan: AnswerPlan,
) -> ChatResponse:
    query = resolve_query_for_turn(classification, state)
    if query is None and state.last_query:
        query = dict(state.last_query)
    if not query:
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status or state.status,
            started_at=started_at,
            reply="I couldn't resolve that follow-up against the current thread.",
            query=state.last_query or state.pending_query,
            confidence=0.4,
            needs_clarification=True,
            snapshots=None,
            parser="thread",
            route_kind=getattr(classification, "kind", TurnKind.PASS).value,
            route_source=route_source,
            error="unresolved_query",
        )

    query = _apply_plan_to_query(query, plan)
    log_answer_plan(plan, path="thread_turn")

    LOGGER.info(
        "Thread turn plan intent=%s blocks=%s source=%s route=%s channel=%s thread=%s",
        plan.intent,
        plan.blocks,
        plan.source,
        route_source,
        channel_id,
        thread_ts,
    )

    reply_mode = reply_mode_for_plan(plan)
    payload = _run_snapshot_for_plan(query, plan, data_root=data_root)
    if not payload.get("ok"):
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status or state.status,
            started_at=started_at,
            reply=str(payload.get("error") or "Could not run that snapshot."),
            query=query,
            confidence=0.5,
            needs_clarification=True,
            snapshots=None,
            parser="thread",
            route_kind=plan.intent,
            route_source=route_source,
            extra_fields=list(plan.fields),
            route_confidence=plan.confidence
            or (classification.confidence if classification else None),
            error=str(payload.get("error") or "snapshot_failed"),
            tool="resolve_and_run_snapshot",
        )

    report = str(payload.get("formatted_report") or "")
    interpretation = None
    if plan.include_interpretation:
        interpretation = run_claude_interpretation(
            message,
            query=payload.get("query") or query,
            metrics=payload.get("metrics") or {},
            report=report,
            reply_mode=reply_mode,
            data_root=data_root,
        )
    reply_body = compose_thread_reply(
        report=report,
        interpretation=interpretation,
        reply_mode=reply_mode,
    )

    confirmed = dict(payload.get("query") or query)
    confirmed.pop("compare_listings", None)
    confirmed.pop("compare_focus", None)

    new_state = confirmed_state_from_query(
        confirmed,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    save_thread_state(new_state, data_root=data_root)

    reply = attach_thread_footer(reply_body, new_state.to_footer())
    fields_used = list(payload.get("fields") or [])
    return finalize_chat_response(
        message=message,
        data_root=data_root,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id=user_id,
        conversation=conversation,
        thread_state_status=new_state.status,
        started_at=started_at,
        reply=reply,
        query=confirmed,
        confidence=plan.confidence
        or (classification.confidence if classification else None)
        or 0.9,
        needs_clarification=False,
        snapshots=payload.get("snapshots"),
        parser="thread",
        route_kind=plan.intent,
        route_source=f"{route_source}+plan:{plan.source}",
        fields_used=fields_used,
        extra_fields=list(plan.fields),
        route_confidence=plan.confidence
        or (classification.confidence if classification else None),
        tool="resolve_and_run_snapshot",
        answer_plan=plan.to_dict(),
    )


def try_thread_turn(
    message: str,
    *,
    data_root: str,
    channel_id: str,
    thread_ts: str,
    conversation: list[dict[str, str]] | None = None,
    user_id: str | None = None,
    started_at: float,
    thread_state_status: str | None = None,
) -> ChatResponse | None:
    state = load_thread_state(data_root, channel_id, thread_ts)
    if state is None and conversation:
        state = recover_state_from_conversation(
            conversation,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
    if state is None:
        return None

    classification, route_source = classify_turn_hybrid(
        message,
        state,
        data_root=data_root,
        channel_id=channel_id,
    )

    if classification.kind != TurnKind.PASS:
        plan = plan_from_classification(classification).with_top_n_from_message(message)
        return _execute_with_plan(
            message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            conversation=conversation,
            user_id=user_id,
            started_at=started_at,
            thread_state_status=thread_state_status,
            state=state,
            classification=classification,
            route_source=route_source,
            plan=plan,
        )

    plan = propose_answer_plan(message, state)
    if plan is None:
        return None
    plan = plan.with_top_n_from_message(message)

    return _execute_with_plan(
        message,
        data_root=data_root,
        channel_id=channel_id,
        thread_ts=thread_ts,
        conversation=conversation,
        user_id=user_id,
        started_at=started_at,
        thread_state_status=thread_state_status,
        state=state,
        classification=classification,
        route_source="answer_planner",
        plan=plan,
    )
