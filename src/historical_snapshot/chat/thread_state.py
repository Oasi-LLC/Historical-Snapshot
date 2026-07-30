from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from historical_snapshot.chat.parser_rules import (
    PROPERTY_ALIASES,
    _parse_dates,
    _resolve_all_listings,
    _resolve_property,
)
from historical_snapshot.config import PropertyConfig, load_property_config, list_property_configs

FOOTER_PATTERN = re.compile(r"<!--\s*snapshot:(\{.*?\})\s*-->", re.DOTALL)

CONFIRM_PATTERN = re.compile(
    r"^(?:yes|yep|yeah|yup|ok(?:ay)?|sure|proceed|go ahead|use that|sounds good|"
    r"that works|do it|confirm|correct|perfect)\.?!?$",
    re.IGNORECASE,
)

LISTING_FOLLOWUP_PATTERN = re.compile(
    r"\b("
    r"top listings?|which units?|listing breakdown|breakdown by listing|"
    r"best units?|units booking|unit breakdown|top units?"
    r")\b",
    re.IGNORECASE,
)

CHANNEL_FOLLOWUP_PATTERN = re.compile(
    r"\b(channel mix|channels?|where.*book(?:ing|ed)|ota mix|direct vs)\b",
    re.IGNORECASE,
)

INTERPRET_ONLY_PATTERN = re.compile(
    r"\b(why|explain|should i worry|what'?s causing|interpret|what does this mean)\b",
    re.IGNORECASE,
)

DATE_OVERRIDE_PATTERN = re.compile(
    r"\b(use|change to|instead|switch to|make it|update to|for \d{4}, use)\b",
    re.IGNORECASE,
)

SAME_WINDOW_PATTERN = re.compile(
    r"\b(same window|that window|those dates|same dates|same period|above)\b",
    re.IGNORECASE,
)

LISTING_COMPARE_PATTERN = re.compile(
    r"\b("
    r"better|worse|compare|comparison|versus|vs\.?|"
    r"or|vs|against|between|which (?:one|listing)|who (?:did|had)"
    r")\b",
    re.IGNORECASE,
)

COMPARE_LY_FOCUS_PATTERN = re.compile(
    r"\b(last year|ly final|ly\b|prior year|yoy|year[- ]over[- ]year)\b",
    re.IGNORECASE,
)

COMPARE_CURRENT_FOCUS_PATTERN = re.compile(
    r"\b(current|pace|on the books|this year|right now)\b",
    re.IGNORECASE,
)


class TurnKind(str, Enum):
    PASS = "pass"
    CONFIRM = "confirm"
    FOLLOW_UP = "follow_up"
    DATE_OVERRIDE = "date_override"
    LISTING_DRILLDOWN = "listing_drilldown"
    LISTING_COMPARE = "listing_compare"
    INTERPRET_ONLY = "interpret_only"


@dataclass
class ThreadState:
    channel_id: str
    thread_ts: str
    status: str  # "pending" | "confirmed"
    pending_query: dict[str, Any] | None = None
    last_query: dict[str, Any] | None = None
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_footer(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "updated_at": self.updated_at,
        }
        if self.pending_query:
            payload["pending_query"] = self.pending_query
        if self.last_query:
            payload["last_query"] = self.last_query
        return payload

    @classmethod
    def from_footer(
        cls,
        payload: dict[str, Any],
        *,
        channel_id: str,
        thread_ts: str,
    ) -> ThreadState:
        return cls(
            channel_id=channel_id,
            thread_ts=thread_ts,
            status=str(payload.get("status") or "confirmed"),
            pending_query=payload.get("pending_query"),
            last_query=payload.get("last_query"),
            updated_at=str(payload.get("updated_at") or datetime.utcnow().isoformat()),
        )


@dataclass(frozen=True)
class TurnClassification:
    kind: TurnKind
    extra_fields: tuple[str, ...] = ()
    listing_name: str | None = None
    listing_names: tuple[str, ...] = ()
    compare_focus: str | None = None  # ly_final | current | both
    query_override: dict[str, Any] | None = None
    confidence: float | None = None


def thread_state_path(data_root: Path | str, channel_id: str, thread_ts: str) -> Path:
    safe_channel = re.sub(r"[^\w.-]", "_", channel_id)
    safe_thread = re.sub(r"[^\w.-]", "_", thread_ts.replace(".", "_"))
    return Path(data_root) / ".cache" / "threads" / f"{safe_channel}_{safe_thread}.json"


def load_thread_state(
    data_root: Path | str,
    channel_id: str,
    thread_ts: str,
) -> ThreadState | None:
    path = thread_state_path(data_root, channel_id, thread_ts)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ThreadState.from_footer(payload, channel_id=channel_id, thread_ts=thread_ts)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def save_thread_state(state: ThreadState, *, data_root: Path | str) -> None:
    path = thread_state_path(data_root, state.channel_id, state.thread_ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "channel_id": state.channel_id,
        "thread_ts": state.thread_ts,
        **state.to_footer(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def extract_thread_footer(text: str) -> dict[str, Any] | None:
    match = FOOTER_PATTERN.search(text or "")
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def strip_thread_footer(text: str) -> str:
    """Remove legacy embedded state footers from assistant text."""
    return FOOTER_PATTERN.sub("", text or "").rstrip()


def attach_thread_footer(text: str, payload: dict[str, Any] | None = None) -> str:
    """Return Slack-visible text only. Thread state is persisted on disk, not in messages."""
    _ = payload
    return strip_thread_footer(text)


def recover_state_from_conversation(
    conversation: list[dict[str, str]],
    *,
    channel_id: str,
    thread_ts: str,
) -> ThreadState | None:
    for turn in reversed(conversation):
        if turn.get("role") != "assistant":
            continue
        footer = extract_thread_footer(str(turn.get("content") or ""))
        if footer:
            return ThreadState.from_footer(footer, channel_id=channel_id, thread_ts=thread_ts)
    return None


def _resolve_property_token(token: str) -> PropertyConfig | None:
    raw = (token or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    folder = PROPERTY_ALIASES.get(lowered)
    if folder:
        return load_property_config(folder)
    for config in list_property_configs():
        if config.folder.lower() == lowered or config.property_id.lower() == lowered:
            return config
        if config.property_name.lower() == lowered:
            return config
    return None


def query_dict_from_proposed(proposed: dict[str, Any]) -> dict[str, Any] | None:
    start = proposed.get("start_date")
    end = proposed.get("end_date")
    property_token = proposed.get("property")
    if not start or not end or not property_token:
        return None
    config = _resolve_property_token(str(property_token))
    if config is None:
        return None
    query: dict[str, Any] = {
        "property": config.folder,
        "start_date": str(start),
        "end_date": str(end),
        "compare_prior_year": True,
    }
    prior_start = proposed.get("prior_start_date")
    prior_end = proposed.get("prior_end_date")
    if prior_start and prior_end:
        query["prior_start_date"] = str(prior_start)
        query["prior_end_date"] = str(prior_end)
    return query


def pending_state_from_proposed(
    proposed: dict[str, Any],
    *,
    channel_id: str,
    thread_ts: str,
) -> ThreadState | None:
    pending_query = query_dict_from_proposed(proposed)
    if pending_query is None:
        return None
    return ThreadState(
        channel_id=channel_id,
        thread_ts=thread_ts,
        status="pending",
        pending_query=pending_query,
    )


def confirmed_state_from_query(
    query: dict[str, Any],
    *,
    channel_id: str,
    thread_ts: str,
) -> ThreadState:
    return ThreadState(
        channel_id=channel_id,
        thread_ts=thread_ts,
        status="confirmed",
        last_query=dict(query),
        pending_query=None,
    )


def fields_for_intent(kind: TurnKind) -> tuple[str, ...]:
    if kind == TurnKind.FOLLOW_UP:
        return ("listing_breakdown",)
    if kind == TurnKind.LISTING_DRILLDOWN:
        return ("listing_breakdown",)
    if kind == TurnKind.LISTING_COMPARE:
        return ("listing_breakdown",)
    return ()


def _compare_focus_for_message(message: str) -> str:
    has_ly = bool(COMPARE_LY_FOCUS_PATTERN.search(message))
    has_current = bool(COMPARE_CURRENT_FOCUS_PATTERN.search(message))
    if has_ly and not has_current:
        return "ly_final"
    if has_current and not has_ly:
        return "current"
    if has_ly and has_current:
        return "both"
    return "ly_final"


def _message_has_new_property(message: str) -> PropertyConfig | None:
    config = _resolve_property(message)
    if config is None:
        return None
    if CONFIRM_PATTERN.match(message.strip()):
        return None
    if SAME_WINDOW_PATTERN.search(message) and not _parse_dates(message):
        return None
    return config


def _message_has_new_dates(message: str) -> tuple[date, date] | None:
    return _parse_dates(message)


def _resolve_listings_in_message(message: str, config: PropertyConfig) -> list[str]:
    return _resolve_all_listings(message, config)


def classify_turn(message: str, state: ThreadState | None) -> TurnClassification:
    text = (message or "").strip()
    if not text:
        return TurnClassification(kind=TurnKind.PASS)

    if state is None:
        return TurnClassification(kind=TurnKind.PASS)

    if state.status == "pending" and state.pending_query and CONFIRM_PATTERN.match(text):
        return TurnClassification(kind=TurnKind.CONFIRM)

    if state.status != "confirmed" or not state.last_query:
        if state.status == "pending" and DATE_OVERRIDE_PATTERN.search(text):
            dates = _message_has_new_dates(text)
            if dates:
                start, end = dates
                override = dict(state.pending_query or {})
                override["start_date"] = start.isoformat()
                override["end_date"] = end.isoformat()
                return TurnClassification(
                    kind=TurnKind.DATE_OVERRIDE,
                    query_override=override,
                )
        return TurnClassification(kind=TurnKind.PASS)

    last = state.last_query
    new_property = _message_has_new_property(text)
    new_dates = _message_has_new_dates(text)

    if new_property:
        last_folder = str(last.get("property_folder") or last.get("property") or "").lower()
        if new_property.folder.lower() != last_folder:
            return TurnClassification(kind=TurnKind.PASS)

    if new_dates and (DATE_OVERRIDE_PATTERN.search(text) or not SAME_WINDOW_PATTERN.search(text)):
        start, end = new_dates
        override = dict(last)
        override["start_date"] = start.isoformat()
        override["end_date"] = end.isoformat()
        return TurnClassification(
            kind=TurnKind.DATE_OVERRIDE,
            query_override=override,
        )

    if LISTING_FOLLOWUP_PATTERN.search(text):
        return TurnClassification(
            kind=TurnKind.FOLLOW_UP,
            extra_fields=fields_for_intent(TurnKind.FOLLOW_UP),
        )

    if CHANNEL_FOLLOWUP_PATTERN.search(text):
        return TurnClassification(
            kind=TurnKind.FOLLOW_UP,
            extra_fields=("channel_breakdown", "listing_breakdown"),
        )

    config = _resolve_property_token(str(last.get("property") or last.get("property_folder") or ""))
    if config:
        listings = _resolve_listings_in_message(text, config)
        # Two+ recognized listing names alone aren't enough to conclude the user wants a
        # side-by-side comparison — e.g. a message could just mention both units in passing.
        # Require actual comparison language (LISTING_COMPARE_PATTERN) too; otherwise fall
        # through so the LLM intent router / answer planner can reason about real intent
        # instead of forcing a comparison table nobody asked for.
        if len(listings) >= 2 and LISTING_COMPARE_PATTERN.search(text):
            return TurnClassification(
                kind=TurnKind.LISTING_COMPARE,
                listing_names=tuple(listings),
                compare_focus=_compare_focus_for_message(text),
                extra_fields=fields_for_intent(TurnKind.LISTING_COMPARE),
            )
        if len(listings) == 1 and not LISTING_FOLLOWUP_PATTERN.search(text):
            return TurnClassification(
                kind=TurnKind.LISTING_DRILLDOWN,
                listing_name=listings[0],
                extra_fields=fields_for_intent(TurnKind.LISTING_DRILLDOWN),
            )

    if INTERPRET_ONLY_PATTERN.search(text) and not new_dates:
        return TurnClassification(kind=TurnKind.INTERPRET_ONLY)

    if SAME_WINDOW_PATTERN.search(text) and not new_dates:
        return TurnClassification(
            kind=TurnKind.FOLLOW_UP,
            extra_fields=fields_for_intent(TurnKind.FOLLOW_UP),
        )

    return TurnClassification(kind=TurnKind.PASS)


def resolve_query_for_turn(
    classification: TurnClassification,
    state: ThreadState,
) -> dict[str, Any] | None:
    if classification.kind == TurnKind.CONFIRM:
        return dict(state.pending_query or {})
    if classification.kind in {
        TurnKind.FOLLOW_UP,
        TurnKind.INTERPRET_ONLY,
        TurnKind.LISTING_DRILLDOWN,
        TurnKind.LISTING_COMPARE,
    }:
        if not state.last_query:
            return None
        query = dict(state.last_query)
        if classification.kind == TurnKind.LISTING_COMPARE:
            # Portfolio-scoped snapshot; comparison rows come from listing_breakdown.
            query.pop("listing_name", None)
            query["compare_listings"] = list(classification.listing_names)
            query["compare_focus"] = classification.compare_focus or "ly_final"
        elif classification.listing_name:
            query["listing_name"] = classification.listing_name
        return query
    if classification.kind == TurnKind.DATE_OVERRIDE:
        return dict(classification.query_override or {})
    return None


def snapshot_args_from_query(query: dict[str, Any]) -> dict[str, Any]:
    property_token = query.get("property") or query.get("property_folder") or query.get("property_id")
    return {
        "property_token": str(property_token or ""),
        "start_date": str(query.get("start_date") or ""),
        "end_date": str(query.get("end_date") or ""),
        "prior_start_date": query.get("prior_start_date"),
        "prior_end_date": query.get("prior_end_date"),
        "listing_name": query.get("listing_name"),
        "compare_prior_year": bool(query.get("compare_prior_year", True)),
    }
