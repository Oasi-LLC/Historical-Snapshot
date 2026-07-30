from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from historical_snapshot.chat.interaction_retrieval import (
    channel_context_summary,
    find_similar_interactions,
    format_retrieval_context,
)
from historical_snapshot.chat.llm_claude import (
    _anthropic_client,
    _model_name,
    anthropic_configured,
    call_claude_with_retries,
)
from historical_snapshot.chat.thread_state import (
    TurnClassification,
    TurnKind,
    ThreadState,
    classify_turn,
    fields_for_intent,
)

LOGGER = logging.getLogger(__name__)

INTENT_ROUTER_ENABLED_ENV = "INTENT_ROUTER_ENABLED"
INTENT_ROUTER_MIN_CONFIDENCE_ENV = "INTENT_ROUTER_MIN_CONFIDENCE"
DEFAULT_MIN_CONFIDENCE = 0.65

VALID_KINDS = {
    "confirm",
    "follow_up",
    "date_override",
    "listing_drilldown",
    "interpret_only",
    "pass",
    "new_topic",
}

FIELD_ALIASES = {
    "listings": "listing_breakdown",
    "listing": "listing_breakdown",
    "listing_breakdown": "listing_breakdown",
    "channels": "channel_breakdown",
    "channel": "channel_breakdown",
    "channel_breakdown": "channel_breakdown",
    "lead_time": "lead_time_distribution",
    "lead_time_distribution": "lead_time_distribution",
}


def intent_router_enabled() -> bool:
    raw = os.environ.get(INTENT_ROUTER_ENABLED_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def intent_router_min_confidence() -> float:
    raw = os.environ.get(INTENT_ROUTER_MIN_CONFIDENCE_ENV, "").strip()
    if not raw:
        return DEFAULT_MIN_CONFIDENCE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_CONFIDENCE


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_extra_fields(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    fields: list[str] = []
    for item in raw:
        name = FIELD_ALIASES.get(str(item).strip().lower())
        if name and name not in fields:
            fields.append(name)
    if "listing_breakdown" not in fields and fields:
        pass
    return tuple(fields)


def parse_intent_router_response(payload: dict[str, Any]) -> TurnClassification | None:
    kind_raw = str(payload.get("kind") or "pass").strip().lower()
    if kind_raw not in VALID_KINDS or kind_raw in {"pass", "new_topic"}:
        return None

    confidence_raw = payload.get("confidence")
    confidence = float(confidence_raw) if confidence_raw is not None else None
    if confidence is not None and confidence < intent_router_min_confidence():
        return None

    try:
        kind = TurnKind(kind_raw)
    except ValueError:
        return None

    listing_name = payload.get("listing_name")
    listing = str(listing_name).strip() if listing_name else None

    extra_fields = _normalize_extra_fields(payload.get("extra_fields"))
    if kind == TurnKind.FOLLOW_UP and not extra_fields:
        if any(
            token in str(payload.get("reasoning") or "").lower()
            for token in ("channel", "ota", "direct")
        ):
            extra_fields = ("channel_breakdown", "listing_breakdown")
        else:
            extra_fields = fields_for_intent(TurnKind.FOLLOW_UP)
    if kind == TurnKind.LISTING_DRILLDOWN and not extra_fields:
        extra_fields = fields_for_intent(TurnKind.LISTING_DRILLDOWN)

    query_override: dict[str, Any] | None = None
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if kind == TurnKind.DATE_OVERRIDE and start_date and end_date:
        query_override = {
            "start_date": str(start_date),
            "end_date": str(end_date),
        }

    return TurnClassification(
        kind=kind,
        extra_fields=extra_fields,
        listing_name=listing,
        query_override=query_override,
        confidence=confidence,
    )


def _build_router_prompt(
    message: str,
    state: ThreadState,
    *,
    data_root: str,
    channel_id: str,
) -> str:
    similar_block = format_retrieval_context(
        message,
        data_root=data_root,
        channel_id=channel_id,
        limit=5,
    )
    channel_block = json.dumps(
        channel_context_summary(data_root, channel_id),
        indent=2,
        default=str,
    )
    state_block = json.dumps(
        {
            "status": state.status,
            "pending_query": state.pending_query,
            "last_query": state.last_query,
        },
        indent=2,
        default=str,
    )
    return f"""Classify this Slack follow-up for a revenue-management snapshot bot.

Return ONLY a JSON object (no markdown):
{{
  "kind": "confirm|follow_up|date_override|listing_drilldown|interpret_only|pass|new_topic",
  "confidence": 0.0,
  "listing_name": null,
  "extra_fields": ["listing_breakdown"],
  "start_date": null,
  "end_date": null,
  "reasoning": "one sentence"
}}

Rules:
- confirm: user accepts proposed dates/property in a pending clarification
- follow_up: same stay window; user wants more detail (listings, channels, curve)
- date_override: user changes stay dates; set start_date/end_date YYYY-MM-DD
- listing_drilldown: user asks about one named unit in the current property
- interpret_only: user asks why/explain without changing dates
- pass: unclear but likely same thread; low confidence
- new_topic: different property or unrelated new question

Inherit property and prior-year dates from thread state unless date_override provides new stay dates.
Prefer kinds that reuse last_query or pending_query. Use similar past interactions as examples.

Thread state:
{state_block}

Channel profile:
{channel_block}

{similar_block}

User message:
{message.strip()}
"""


def route_turn_with_llm(
    message: str,
    state: ThreadState,
    *,
    data_root: str,
    channel_id: str,
) -> TurnClassification | None:
    if not intent_router_enabled() or not anthropic_configured():
        return None

    similar = find_similar_interactions(
        message,
        data_root=data_root,
        channel_id=channel_id,
        limit=3,
    )
    if not similar and state.status == "confirmed" and not state.last_query:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    client = _anthropic_client()
    prompt = _build_router_prompt(
        message,
        state,
        data_root=data_root,
        channel_id=channel_id,
    )
    try:
        response = call_claude_with_retries(
            lambda: client.messages.create(
                model=_model_name(),
                max_tokens=512,
                system=(
                    "You classify follow-up intent for a short-term rental snapshot bot. "
                    "Respond with JSON only."
                ),
                messages=[{"role": "user", "content": prompt}],
            ),
            label="intent-router",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Intent router call failed after retries: %s", exc)
        return None

    text = ""
    for block in response.content:
        text += getattr(block, "text", "") or ""
    payload = _extract_json_object(text)
    if not payload:
        LOGGER.info("Intent router returned non-JSON: %s", text[:200])
        return None

    classification = parse_intent_router_response(payload)
    if classification is None:
        LOGGER.info("Intent router below threshold or pass: %s", payload)
        return None

    if classification.kind == TurnKind.DATE_OVERRIDE and classification.query_override:
        base = dict(state.last_query or state.pending_query or {})
        base.update(classification.query_override)
        return TurnClassification(
            kind=classification.kind,
            extra_fields=classification.extra_fields,
            listing_name=classification.listing_name,
            query_override=base,
            confidence=classification.confidence,
        )

    LOGGER.info(
        "Intent router: kind=%s confidence=%s",
        classification.kind.value,
        classification.confidence,
    )
    return classification


def classify_turn_hybrid(
    message: str,
    state: ThreadState | None,
    *,
    data_root: str,
    channel_id: str,
) -> tuple[TurnClassification, str]:
    """Rules first, then LLM router with retrieved examples."""
    rules = classify_turn(message, state)
    if rules.kind != TurnKind.PASS:
        return rules, "thread_classifier"

    if state is None:
        return rules, "thread_classifier"

    routed = route_turn_with_llm(
        message,
        state,
        data_root=data_root,
        channel_id=channel_id,
    )
    if routed is not None:
        return routed, "intent_router"

    return rules, "thread_classifier"
