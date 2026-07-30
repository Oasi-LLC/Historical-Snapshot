from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from historical_snapshot.chat.prompts import RM_DOCTRINE, build_interpretation_guidance, build_system_prompt
from historical_snapshot.chat.slack_format import format_interpretation, render_interpretation_struct
from historical_snapshot.chat.tools import TOOL_SCHEMAS, ToolSession, tool_result_content

LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-5"
MAX_TOOL_ROUNDS = 5
# Extra app-level retries beyond the SDK defaults — 529 overloaded is common and often brief.
CLAUDE_OVERLOAD_RETRIES = int(os.environ.get("CLAUDE_OVERLOAD_RETRIES", "4"))
CLAUDE_OVERLOAD_BASE_DELAY_SEC = float(os.environ.get("CLAUDE_OVERLOAD_BASE_DELAY_SEC", "2.0"))

T = TypeVar("T")


@dataclass
class ClaudeChatResult:
    reply: str
    query: dict[str, Any] | None
    snapshots: dict[str, Any] | None
    needs_clarification: bool
    confidence: float = 0.7
    pending_proposed: dict[str, Any] | None = None
    answer_plan: dict[str, Any] | None = None
    reply_mode: str = "full"


def anthropic_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _model_name() -> str:
    return os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def is_claude_overload_error(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        "overloaded" in name
        or "overloaded" in text
        or "529" in text
        or "rate_limit" in name
        or "rate limit" in text
        or "429" in text
    )


def call_claude_with_retries(operation: Callable[[], T], *, label: str = "claude") -> T:
    """Retry transient Anthropic overload / rate-limit errors with exponential backoff."""
    attempts = max(1, CLAUDE_OVERLOAD_RETRIES + 1)
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_claude_overload_error(exc) or attempt >= attempts:
                raise
            delay = CLAUDE_OVERLOAD_BASE_DELAY_SEC * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.5)
            LOGGER.warning(
                "%s overloaded/rate-limited (attempt %s/%s); retrying in %.1fs: %s",
                label,
                attempt,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _anthropic_client():
    import anthropic

    # SDK also retries 529/429; keep a modest floor and rely on call_claude_with_retries for longer waits.
    return anthropic.Anthropic(max_retries=2)


def _extract_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of the model's response, fenced or bare."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        bare = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        candidate = bare.group(1) if bare else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_interpretation(text: str, *, reply_mode: str = "full") -> str | None:
    if not text:
        return None

    parsed = _extract_json_block(text)
    if parsed is not None:
        rendered = render_interpretation_struct(parsed, reply_mode=reply_mode)
        if rendered:
            return rendered
        LOGGER.warning(
            "Interpretation JSON parsed but had no usable fields for reply_mode=%s",
            reply_mode,
        )
        return None

    cleaned = text.strip().replace("**", "*")
    cleaned = re.sub(r"^---+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)

    match = re.search(r"\*+Interpretation\*+\s*(.*)$", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if match:
        body = match.group(1).strip()
    elif re.search(r"^Interpretation\s*$", cleaned, flags=re.IGNORECASE | re.MULTILINE):
        body = re.sub(r"(?im)^Interpretation\s*$", "", cleaned).strip()
    elif "Snapshot as of" in cleaned or re.search(r"^\s*Metric\b", cleaned, re.MULTILINE):
        return None
    else:
        body = cleaned

    return format_interpretation(body)


def _compose_final_reply(report: str | None, model_text: str, *, reply_mode: str = "full") -> str:
    interpretation = _normalize_interpretation(model_text, reply_mode=reply_mode)
    if report and interpretation:
        return f"{report}\n\n{interpretation}".strip()
    if report:
        return report.strip()
    if interpretation:
        return interpretation
    return model_text.strip()


def _interpretation_system_prompt(*, reply_mode: str = "full") -> str:
    intro = (
        "You write the Interpretation section for a Slack revenue-management snapshot reply, "
        "as a follow-up within an existing thread. Use only numbers present in the `metrics` "
        "and `report` you're given below, or numbers returned by `fetch_more_metrics` if you "
        "call it — never invent stats. Never rewrite or repeat the report table. Plain, concise "
        "English. No asterisks or markdown.\n\n"
        "If the metrics you were given don't cover the user's question (e.g. they asked about "
        "a per-listing figure, channel mix, or booking-window detail that wasn't part of the "
        "original fetch), call `fetch_more_metrics` once with the additional fields/listing "
        "names you need before writing your answer. The property and stay dates are already "
        "confirmed and cannot be changed here — only additional fields/listings can be "
        "requested. Do not conclude data is unavailable without calling this tool first."
    )
    return f"{intro}\n\n{RM_DOCTRINE}\n\n### Bullet scope for this reply\n{build_interpretation_guidance(reply_mode=reply_mode)}"


FETCH_MORE_METRICS_TOOL: dict[str, Any] = {
    "name": "fetch_more_metrics",
    "description": (
        "Fetch additional metrics for the SAME already-confirmed property and stay dates — "
        "property and dates cannot be changed here, only what's fetched. Use this when the "
        "metrics you were given don't cover the user's question instead of assuming the data "
        "doesn't exist; most per-listing and portfolio metrics the engine computes (booking "
        "window, pickup bands, LOS distribution, channel mix, arrival-day mix) are one call away."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "description": (
                    "Additional metric fields to fetch, e.g. listing_breakdown, "
                    "channel_breakdown, lead_time_distribution, los_distribution."
                ),
                "items": {"type": "string"},
            },
            "listing_names": {
                "type": "array",
                "description": (
                    "Specific listing/unit names to fetch full per-listing detail for "
                    "(booking window, pickup, LOS, channel mix) — use when the question names "
                    "one or more specific units."
                ),
                "items": {"type": "string"},
            },
        },
        "additionalProperties": False,
    },
}


def _fetch_supplemental_metrics(
    *,
    query: dict[str, Any],
    fields: Any,
    listing_names: Any,
    data_root: str,
) -> dict[str, Any]:
    """Re-run the snapshot for the SAME confirmed property/dates with extra fields/listings.

    Used by the interpretation tool loop so a follow-up isn't stuck with only the metrics the
    upstream classifier/planner originally chose to fetch — Claude can ask for more instead of
    defaulting to "data not available" when the engine actually has it.
    """
    from historical_snapshot.chat.thread_state import snapshot_args_from_query
    from historical_snapshot.chat.tools import resolve_and_run_snapshot

    field_list = [str(item).strip() for item in (fields or []) if str(item).strip()]
    names = [str(item).strip() for item in (listing_names or []) if str(item).strip()]
    if not field_list and not names:
        return {"ok": False, "error": "No fields or listing_names provided."}

    compare_listings = names or None
    if not field_list and compare_listings:
        field_list = ["listing_breakdown"]

    try:
        args = snapshot_args_from_query(query)
        payload = resolve_and_run_snapshot(
            **args,
            fields=field_list or None,
            compare_listings=compare_listings,
            compare_focus=str(query.get("compare_focus") or "ly_final"),
            data_root=data_root,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Could not fetch supplemental metrics: {exc}"}

    return {
        "ok": bool(payload.get("ok")),
        "metrics": payload.get("metrics"),
        "error": payload.get("error"),
    }


def run_claude_interpretation(
    message: str,
    *,
    query: dict[str, Any],
    metrics: dict[str, Any],
    report: str,
    reply_mode: str = "full",
    data_root: str = "data",
) -> str | None:
    """Generate Interpretation bullets from an already-run snapshot.

    Allows at most one supplemental `fetch_more_metrics` round-trip: if the metrics originally
    fetched by the upstream classifier/planner don't cover the question (e.g. a per-listing
    booking-window ask when only headline fields were pulled), Claude can request more data for
    the same confirmed property/dates before writing the reply, instead of declaring the data
    unavailable. Costs nothing extra when the original metrics already answer the question.
    """
    if not anthropic_configured():
        return None

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None

    client = _anthropic_client()
    system = _interpretation_system_prompt(reply_mode=reply_mode)
    user_content = (
        f"User follow-up: {message.strip()}\n\n"
        f"Query: {json.dumps(query, default=str)}\n\n"
        f"Metrics: {json.dumps(metrics, default=str)}\n\n"
        f"Formatted report:\n{report}"
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

    for round_idx in range(2):
        offer_tool = round_idx == 0
        create_kwargs: dict[str, Any] = {
            "model": _model_name(),
            "max_tokens": 512,
            "system": system,
            "messages": messages,
        }
        if offer_tool:
            create_kwargs["tools"] = [FETCH_MORE_METRICS_TOOL]
        try:
            response = call_claude_with_retries(
                lambda kwargs=create_kwargs: client.messages.create(**kwargs),
                label="interpretation",
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Interpretation call failed: %s", exc)
            return None

        if offer_tool and response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            requested_any = False
            for block in response.content:
                if getattr(block, "type", None) != "tool_use" or block.name != "fetch_more_metrics":
                    continue
                requested_any = True
                args = dict(block.input or {})
                extra = _fetch_supplemental_metrics(
                    query=query,
                    fields=args.get("fields"),
                    listing_names=args.get("listing_names"),
                    data_root=data_root,
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(extra, default=str),
                    }
                )
            if requested_any:
                messages.append({"role": "user", "content": tool_results})
                continue

        text = _extract_text(response.content)
        return _normalize_interpretation(text, reply_mode=reply_mode)

    return None


def run_claude_chat(
    message: str,
    *,
    data_root: str = "data",
    conversation: list[dict[str, str]] | None = None,
) -> ClaudeChatResult:
    if not anthropic_configured():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    try:
        import anthropic  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "anthropic package is not installed. Run: pip install anthropic"
        ) from exc

    client = _anthropic_client()
    session = ToolSession(data_root=data_root, user_message=message.strip())
    system_prompt = build_system_prompt()
    messages: list[dict[str, Any]] = []
    for turn in conversation or []:
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message.strip()})

    for round_idx in range(MAX_TOOL_ROUNDS):
        LOGGER.info("Claude chat round %s/%s", round_idx + 1, MAX_TOOL_ROUNDS)
        response = call_claude_with_retries(
            lambda: client.messages.create(
                model=_model_name(),
                max_tokens=4096,
                system=system_prompt,
                tools=TOOL_SCHEMAS,
                messages=messages,
            ),
            label=f"chat-round-{round_idx + 1}",
        )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                payload = session.dispatch(block.name, dict(block.input or {}))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_result_content(payload),
                    }
                )
                if block.name == "clarify" and session.clarification:
                    proposed = None
                    if session.clarification_structured:
                        proposed = session.clarification_structured.get("proposed")
                    return ClaudeChatResult(
                        reply=session.clarification,
                        query=None,
                        snapshots=None,
                        needs_clarification=True,
                        confidence=0.9,
                        pending_proposed=proposed if isinstance(proposed, dict) else None,
                    )

            if not tool_results:
                break
            messages.append({"role": "user", "content": tool_results})
            continue

        text = _extract_text(response.content)
        if not text and session.last_snapshots is None and session.clarification:
            text = session.clarification
        if not text:
            text = (
                "I couldn't produce an answer from that. "
                "Try naming a property and date range, e.g. `Onera July 31 2026`."
            )

        reply_mode = session.last_reply_mode or "full"
        reply = _compose_final_reply(
            session.last_formatted_report,
            text,
            reply_mode=reply_mode,
        )
        return ClaudeChatResult(
            reply=reply,
            query=session.last_query,
            snapshots=session.last_snapshots,
            needs_clarification=False,
            confidence=0.75,
            answer_plan=session.last_answer_plan,
            reply_mode=reply_mode,
        )

    if session.clarification:
        proposed = None
        if session.clarification_structured:
            proposed = session.clarification_structured.get("proposed")
        return ClaudeChatResult(
            reply=session.clarification,
            query=None,
            snapshots=None,
            needs_clarification=True,
            confidence=0.8,
            pending_proposed=proposed if isinstance(proposed, dict) else None,
        )

    if session.last_formatted_report:
        return ClaudeChatResult(
            reply=session.last_formatted_report,
            query=session.last_query,
            snapshots=session.last_snapshots,
            needs_clarification=False,
            confidence=0.6,
            answer_plan=session.last_answer_plan,
            reply_mode=session.last_reply_mode or "full",
        )

    return ClaudeChatResult(
        reply=(
            "I hit the tool-call limit before finishing. "
            "Try a simpler ask with an explicit property and dates."
        ),
        query=session.last_query,
        snapshots=session.last_snapshots,
        needs_clarification=True,
        confidence=0.4,
        answer_plan=session.last_answer_plan,
        reply_mode=session.last_reply_mode or "full",
    )