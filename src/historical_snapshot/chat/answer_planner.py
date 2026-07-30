from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from historical_snapshot.chat.answer_plan import AnswerPlan, parse_answer_plan
from historical_snapshot.chat.llm_claude import (
    _anthropic_client,
    _model_name,
    anthropic_configured,
    call_claude_with_retries,
)
from historical_snapshot.chat.parser_rules import _resolve_all_listings
from historical_snapshot.chat.thread_state import ThreadState
from historical_snapshot.config import load_property_config

LOGGER = logging.getLogger(__name__)

ANSWER_PLANNER_ENABLED_ENV = "ANSWER_PLANNER_ENABLED"
ANSWER_PLANNER_MIN_CONFIDENCE_ENV = "ANSWER_PLANNER_MIN_CONFIDENCE"
DEFAULT_MIN_CONFIDENCE = 0.65


def answer_planner_enabled() -> bool:
    raw = os.environ.get(ANSWER_PLANNER_ENABLED_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def answer_planner_min_confidence() -> float:
    raw = os.environ.get(ANSWER_PLANNER_MIN_CONFIDENCE_ENV, "").strip()
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
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        bare = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
        candidate = bare.group(1) if bare else None
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


_PLANNER_SYSTEM = """
You design a Slack answer shape for Snapshot Bot. The stay window and property are already
confirmed in thread. Do NOT invent metrics. Do NOT suggest new dates or properties.

Return exactly one fenced json code block with an AnswerPlan:
```json
{
  "intent": "portfolio_snapshot|listing_rank|channel_mix|listing_detail|listing_compare|explain|custom",
  "basis": ["current", "ly_pace", "ly_final"],
  "blocks": ["header", "performance", "top_listings", "listing_compare", "channel_mix", "none"],
  "listings": ["exact listing names if relevant"],
  "fields": ["listing_breakdown", "channel_breakdown"],
  "include_interpretation": true,
  "interpretation_mode": "full|delta_listings|delta_channels|delta_listing|listing_compare|interpretation_only",
  "subtitle": "optional short subtitle",
  "confidence": 0.0
}
```

Rules:
- Pick the MINIMUM blocks that answer the question. Do not default to full performance.
- "last year" / LY → basis should emphasize ly_final (and omit current from compare tables unless asked).
- Compare two+ listings → intent listing_compare, blocks [header, listing_compare], listings filled.
- "top listings" → listing_rank + top_listings blocks.
- "why / explain" with no new table needed → explain + blocks [none].
- confidence < 0.5 if the question is a new property/topic (caller will ignore the plan).
""".strip()


def _normalize_plan_listings(plan: AnswerPlan, *, message: str, state: ThreadState) -> AnswerPlan:
    """Resolve free-text listing names against property inventory when possible."""
    if not plan.listings:
        # Try to discover listings from the message for compare/detail intents.
        last = state.last_query or {}
        token = str(last.get("property") or last.get("property_folder") or "")
        try:
            config = load_property_config(token) if token else None
        except Exception:  # noqa: BLE001
            config = None
        if config is None:
            return plan
        discovered = _resolve_all_listings(message, config)
        if discovered and plan.intent in {"listing_compare", "listing_detail", "custom"}:
            return AnswerPlan(
                intent=plan.intent if len(discovered) > 1 or plan.intent != "listing_detail" else plan.intent,
                basis=plan.basis,
                blocks=(
                    ("header", "listing_compare")
                    if len(discovered) >= 2 and "listing_compare" not in plan.blocks and plan.intent != "explain"
                    else plan.blocks
                ),
                listings=tuple(discovered),
                fields=plan.fields,
                include_interpretation=plan.include_interpretation,
                interpretation_mode=(
                    "listing_compare" if len(discovered) >= 2 else plan.interpretation_mode
                ),
                confidence=plan.confidence,
                source=plan.source,
                subtitle=plan.subtitle,
            )
        return plan

    last = state.last_query or {}
    token = str(last.get("property") or last.get("property_folder") or "")
    try:
        config = load_property_config(token) if token else None
    except Exception:  # noqa: BLE001
        config = None
    if config is None:
        return plan

    resolved: list[str] = []
    for name in plan.listings:
        hits = _resolve_all_listings(name, config)
        resolved.append(hits[0] if hits else name)
    if tuple(resolved) == plan.listings:
        return plan
    return AnswerPlan(
        intent=plan.intent,
        basis=plan.basis,
        blocks=plan.blocks,
        listings=tuple(resolved),
        fields=plan.fields,
        include_interpretation=plan.include_interpretation,
        interpretation_mode=plan.interpretation_mode,
        confidence=plan.confidence,
        source=plan.source,
        subtitle=plan.subtitle,
    )


def propose_answer_plan(
    message: str,
    state: ThreadState,
) -> AnswerPlan | None:
    """LLM AnswerPlan for freeform follow-ups when regex/intent modes don't match."""
    if not answer_planner_enabled() or not anthropic_configured():
        return None
    if state.status != "confirmed" or not state.last_query:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    user_content = (
        f"User message: {message.strip()}\n\n"
        f"Confirmed query: {json.dumps(state.last_query, default=str)}\n"
    )
    try:
        client = _anthropic_client()
        response = call_claude_with_retries(
            lambda: client.messages.create(
                model=_model_name(),
                max_tokens=512,
                system=_PLANNER_SYSTEM,
                messages=[{"role": "user", "content": user_content}],
            ),
            label="answer-planner",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Answer planner failed after retries: %s", exc)
        return None

    text = ""
    for block in response.content:
        part = getattr(block, "text", None)
        if part:
            text += part
    payload = _extract_json_object(text)
    if not payload:
        LOGGER.info("Answer planner returned no JSON")
        return None

    plan = parse_answer_plan(payload, source="llm")
    if plan is None:
        return None
    if plan.confidence is not None and plan.confidence < answer_planner_min_confidence():
        LOGGER.info(
            "Answer planner confidence %.2f below threshold",
            plan.confidence,
        )
        return None

    plan = _normalize_plan_listings(plan, message=message, state=state)
    LOGGER.info(
        "Answer planner intent=%s blocks=%s listings=%s confidence=%s",
        plan.intent,
        plan.blocks,
        plan.listings,
        plan.confidence,
    )
    return plan
