from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from typing import Any

from historical_snapshot.chat.executor import execute_query
from historical_snapshot.chat.formatter import format_reply
from historical_snapshot.chat.interaction_record import finalize_chat_response
from historical_snapshot.chat.llm_claude import anthropic_configured, is_claude_overload_error, run_claude_chat
from historical_snapshot.chat.models import ChatResponse, ChatSnapshotQuery
from historical_snapshot.chat.parser_rules import parse_message, _resolve_property
from historical_snapshot.chat.thread_state import (
    attach_thread_footer,
    confirmed_state_from_query,
    load_thread_state,
    pending_state_from_proposed,
    save_thread_state,
)
from historical_snapshot.chat.thread_turn import try_thread_turn

LOGGER = logging.getLogger(__name__)

RULES_CONFIDENCE_THRESHOLD = 0.75

ANALYSIS_PATTERN = re.compile(
    r"(?:"
    r"\?|"
    r"\bwhy\b|"
    r"\bshould\b|"
    r"\bworry\b|"
    r"\bdriving\b|"
    r"\bcloser in\b|"
    r"\bfarther out\b|"
    r"\bexplain\b|"
    r"\binterpret\b|"
    r"\binsight\b|"
    r"\banalys[ei]s\b|"
    r"\bwhat'?s causing\b|"
    r"\bare we booking\b|"
    r"\bis (?:adr|volume|occupancy|revpar)\b|"
    r"\bhow (?:is|are|does|do)\b|"
    r"\btell me about\b|"
    r"\blooking(?:\s+like)?\b|"
    r"\bweekend\b|"
    r"\blabor day\b|"
    r"\bmemorial day\b|"
    r"\bthanksgiving\b|"
    r"\bjuly 4\b|"
    r"\bfourth of july\b"
    r")",
    re.IGNORECASE,
)

AMBIGUOUS_DATE_PATTERN = re.compile(
    r"\b(?:next|this|coming|upcoming)\s+weekend\b|"
    r"\b(?:labor|memorial)\s+day\b|"
    r"\bthanksgiving\b|"
    r"\b(?:july\s*4|fourth\s+of\s+july)\b",
    re.IGNORECASE,
)


def _freeform_unavailable_message(base: str | None) -> str:
    props_hint = base or (
        "I can look that up — which property and dates?\n\n"
        "Examples:\n"
        "• Onera July 31 2026\n"
        "• LaFave Jul 4-5 2025 vs last year"
    )
    return (
        f"{props_hint}\n\n"
        "_Freeform questions need `ANTHROPIC_API_KEY` configured for the Claude fallback._"
    )


def _claude_failure_reply(message: str, parsed, exc: BaseException) -> str:
    """User-facing fallback when Claude is down — never dump raw API payloads to Slack.

    Deliberately plain text, not `format_clarification` — that helper always adds a
    "Clarification" header and a "Reply yes to proceed" line, which is misleading here:
    nothing concrete has actually been proposed to confirm, since Claude (which would
    normally resolve "upcoming weekend" etc. into real dates) is the thing that's down.
    """
    prop = _resolve_property(message)
    overloaded = is_claude_overload_error(exc)
    if overloaded:
        head = (
            "Claude is temporarily overloaded (Anthropic capacity) right now, even after retrying. "
            "Please try again shortly."
        )
    else:
        head = "I couldn't reach Claude just now. Please try again shortly."

    if prop and AMBIGUOUS_DATE_PATTERN.search(message):
        return (
            f"{head}\n\n"
            f"In the meantime: I have {prop.property_name} noted, but I need explicit stay dates "
            f"to run this without Claude — e.g. `{prop.folder} Jul 31-Aug 2 2026`."
        )

    if prop:
        return (
            f"{head}\n\n"
            f"In the meantime: property noted as {prop.property_name}. Include stay dates like "
            f"`{prop.folder} Jul 31-Aug 2 2026` and I can still run a standard snapshot."
        )

    return f"{head}\n\n{parsed.clarification or 'Please include a property and date range.'}"


def _should_use_claude(message: str, *, confidence: float, has_query: bool) -> bool:
    if AMBIGUOUS_DATE_PATTERN.search(message):
        return True
    if not has_query or confidence < RULES_CONFIDENCE_THRESHOLD:
        return True
    return bool(ANALYSIS_PATTERN.search(message))


def _with_pace(query: ChatSnapshotQuery) -> ChatSnapshotQuery:
    return replace(query, pace=True)


def _finalize_thread_reply(
    reply: str,
    *,
    data_root: str,
    channel_id: str | None,
    thread_ts: str | None,
    query: dict[str, Any] | None = None,
    pending_proposed: dict[str, Any] | None = None,
) -> str:
    if not channel_id or not thread_ts:
        return reply

    if query:
        state = confirmed_state_from_query(
            query,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        save_thread_state(state, data_root=data_root)
        return attach_thread_footer(reply, state.to_footer())

    if pending_proposed:
        state = pending_state_from_proposed(
            pending_proposed,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        if state:
            save_thread_state(state, data_root=data_root)
            return attach_thread_footer(reply, state.to_footer())

    return reply


def handle_chat_message(
    message: str,
    *,
    data_root: str = "data",
    conversation: list[dict[str, str]] | None = None,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    user_id: str | None = None,
) -> ChatResponse:
    started_at = time.perf_counter()
    thread_state_status: str | None = None
    if channel_id and thread_ts:
        state = load_thread_state(data_root, channel_id, thread_ts)
        if state:
            thread_state_status = state.status

    if channel_id and thread_ts:
        thread_result = try_thread_turn(
            message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            conversation=conversation,
            user_id=user_id,
            started_at=started_at,
            thread_state_status=thread_state_status,
        )
        if thread_result is not None:
            return thread_result

    parsed = parse_message(message)

    if parsed.query is None and parsed.clarification and parsed.confidence >= 0.95:
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status,
            started_at=started_at,
            reply=parsed.clarification,
            query=None,
            confidence=parsed.confidence,
            needs_clarification=True,
            snapshots=None,
            parser=parsed.parser,
            route_kind="clarify",
            route_source="rules",
        )

    if not message.strip():
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status,
            started_at=started_at,
            reply=parsed.clarification or "Please include a property and date range.",
            query=None,
            confidence=parsed.confidence,
            needs_clarification=True,
            snapshots=None,
            parser=parsed.parser,
            route_kind="clarify",
            route_source="rules",
        )

    use_claude = _should_use_claude(
        message,
        confidence=parsed.confidence,
        has_query=parsed.query is not None,
    )

    if not use_claude and parsed.query is not None:
        query = _with_pace(parsed.query)
        result = execute_query(query, data_root=data_root)
        reply = _finalize_thread_reply(
            format_reply(result),
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            query=query.to_dict(),
        )
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status,
            started_at=started_at,
            reply=reply,
            query=query.to_dict(),
            confidence=parsed.confidence,
            needs_clarification=False,
            snapshots=result.to_dict(),
            parser="rules",
            route_kind="snapshot",
            route_source="rules",
            tool="run_snapshot",
        )

    if not anthropic_configured():
        if parsed.query is not None and parsed.confidence >= RULES_CONFIDENCE_THRESHOLD:
            query = _with_pace(parsed.query)
            result = execute_query(query, data_root=data_root)
            reply = _finalize_thread_reply(
                format_reply(result),
                data_root=data_root,
                channel_id=channel_id,
                thread_ts=thread_ts,
                query=query.to_dict(),
            )
            return finalize_chat_response(
                message=message,
                data_root=data_root,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                conversation=conversation,
                thread_state_status=thread_state_status,
                started_at=started_at,
                reply=reply,
                query=query.to_dict(),
                confidence=parsed.confidence,
                needs_clarification=False,
                snapshots=result.to_dict(),
                parser="rules",
                route_kind="snapshot",
                route_source="rules",
                tool="run_snapshot",
            )
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status,
            started_at=started_at,
            reply=_freeform_unavailable_message(parsed.clarification),
            query=None,
            confidence=parsed.confidence,
            needs_clarification=True,
            snapshots=None,
            parser="rules",
            route_kind="clarify",
            route_source="rules",
        )

    try:
        claude = run_claude_chat(message, data_root=data_root, conversation=conversation)
        if claude.needs_clarification:
            reply = _finalize_thread_reply(
                claude.reply,
                data_root=data_root,
                channel_id=channel_id,
                thread_ts=thread_ts,
                pending_proposed=claude.pending_proposed,
            )
            return finalize_chat_response(
                message=message,
                data_root=data_root,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                conversation=conversation,
                thread_state_status=thread_state_status,
                started_at=started_at,
                reply=reply,
                query=claude.query,
                confidence=claude.confidence,
                needs_clarification=True,
                snapshots=claude.snapshots,
                parser="claude",
                pending_proposed=claude.pending_proposed,
                route_kind="clarify",
                route_source="claude",
                tool="clarify",
            )

        reply = _finalize_thread_reply(
            claude.reply,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            query=claude.query,
        )
        plan_source = None
        if claude.answer_plan:
            plan_source = str(claude.answer_plan.get("source") or "claude_tool")
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status,
            started_at=started_at,
            reply=reply,
            query=claude.query,
            confidence=claude.confidence,
            needs_clarification=claude.needs_clarification,
            snapshots=claude.snapshots,
            parser="claude",
            route_kind=(claude.answer_plan or {}).get("intent") or "snapshot",
            route_source=(
                f"claude+plan:{plan_source}" if plan_source else "claude"
            ),
            tool="resolve_and_run_snapshot",
            answer_plan=claude.answer_plan,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Claude chat failed; falling back")
        if parsed.query is not None and parsed.confidence >= RULES_CONFIDENCE_THRESHOLD:
            query = _with_pace(parsed.query)
            result = execute_query(query, data_root=data_root)
            body = (
                f"{format_reply(result)}\n\n"
                f"_Note: freeform interpretation unavailable ({exc}). Showing standard report._"
            )
            reply = _finalize_thread_reply(
                body,
                data_root=data_root,
                channel_id=channel_id,
                thread_ts=thread_ts,
                query=query.to_dict(),
            )
            return finalize_chat_response(
                message=message,
                data_root=data_root,
                channel_id=channel_id,
                thread_ts=thread_ts,
                user_id=user_id,
                conversation=conversation,
                thread_state_status=thread_state_status,
                started_at=started_at,
                reply=reply,
                query=query.to_dict(),
                confidence=parsed.confidence,
                needs_clarification=False,
                snapshots=result.to_dict(),
                parser="rules",
                route_kind="snapshot",
                route_source="rules_fallback",
                tool="run_snapshot",
                error=str(exc),
            )
        return finalize_chat_response(
            message=message,
            data_root=data_root,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            conversation=conversation,
            thread_state_status=thread_state_status,
            started_at=started_at,
            reply=_claude_failure_reply(message, parsed, exc),
            query=None,
            confidence=parsed.confidence,
            needs_clarification=True,
            snapshots=None,
            parser="rules",
            route_kind="error",
            route_source="claude_overload" if is_claude_overload_error(exc) else "rules_fallback",
            error=str(exc),
        )
