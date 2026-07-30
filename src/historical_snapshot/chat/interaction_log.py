from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

LOGGER = logging.getLogger(__name__)

INTERACTION_LOGGING_ENV = "INTERACTION_LOGGING"
USER_HASH_SALT_ENV = "INTERACTION_USER_HASH_SALT"

STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "shall", "can", "need", "dare",
        "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
        "from", "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "out", "off", "over", "under", "again", "further",
        "then", "once", "here", "there", "when", "where", "why", "how", "all",
        "each", "few", "more", "most", "other", "some", "such", "no", "nor",
        "not", "only", "own", "same", "so", "than", "too", "very", "just",
        "and", "but", "if", "or", "because", "until", "while", "although",
        "this", "that", "these", "those", "i", "me", "my", "we", "our", "you",
        "your", "he", "him", "his", "she", "her", "it", "its", "they", "them",
        "their", "what", "which", "who", "whom", "about", "up", "down", "out",
        "snapshot", "bot", "please", "thanks", "thank", "hi", "hello",
    }
)


def interaction_logging_enabled() -> bool:
    raw = os.environ.get(INTERACTION_LOGGING_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _interactions_root(data_root: Path | str) -> Path:
    return Path(data_root) / ".cache" / "interactions"


def _daily_log_path(data_root: Path | str, day: date | None = None) -> Path:
    day = day or datetime.now(timezone.utc).date()
    return _interactions_root(data_root) / f"{day.isoformat()}.jsonl"


def _channel_profile_path(data_root: Path | str, channel_id: str) -> Path:
    safe = re.sub(r"[^\w.-]", "_", channel_id)
    return _interactions_root(data_root) / "channels" / f"{safe}.json"


def _hash_user_id(user_id: str | None) -> str | None:
    if not user_id or user_id == "unknown":
        return None
    salt = os.environ.get(USER_HASH_SALT_ENV, "historical-snapshot").encode()
    digest = hashlib.sha256(salt + user_id.encode()).hexdigest()[:16]
    return digest


def tokenize_for_retrieval(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {token for token in tokens if len(token) > 2 and token not in STOPWORDS}


@dataclass
class RouteInfo:
    kind: str
    source: str
    confidence: float | None = None
    extra_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "source": self.source,
        }
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        if self.extra_fields:
            payload["extra_fields"] = self.extra_fields
        return payload


@dataclass
class ActionInfo:
    tool: str | None = None
    fields: list[str] = field(default_factory=list)
    query: dict[str, Any] | None = None
    answer_plan: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.tool:
            payload["tool"] = self.tool
        if self.fields:
            payload["fields"] = self.fields
        if self.query:
            payload["query"] = self.query
        if self.answer_plan:
            payload["answer_plan"] = self.answer_plan
        return payload


@dataclass
class OutcomeInfo:
    ok: bool
    parser: str
    needs_clarification: bool = False
    latency_ms: int | None = None
    error: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "parser": self.parser,
            "needs_clarification": self.needs_clarification,
        }
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        if self.error:
            payload["error"] = self.error
        if self.confidence is not None:
            payload["confidence"] = self.confidence
        return payload


@dataclass
class ReportSummary:
    property_folder: str | None = None
    property_name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    prior_start_date: str | None = None
    prior_end_date: str | None = None
    bookings_count: int | None = None
    room_revenue: float | None = None
    listing_name: str | None = None

    @classmethod
    def from_query_and_snapshots(
        cls,
        query: dict[str, Any] | None,
        snapshots: dict[str, Any] | None,
    ) -> ReportSummary:
        if not query:
            return cls()
        portfolio = None
        if snapshots:
            pace = snapshots.get("pace_current") or {}
            portfolio = pace.get("portfolio_snapshot")
        bookings = None
        revenue = None
        if portfolio:
            bookings = portfolio.get("bookings_count")
            revenue = portfolio.get("room_revenue")
        return cls(
            property_folder=query.get("property_folder") or query.get("property"),
            property_name=query.get("property_name"),
            start_date=query.get("start_date"),
            end_date=query.get("end_date"),
            prior_start_date=query.get("prior_stay_start_date") or query.get("prior_start_date"),
            prior_end_date=query.get("prior_stay_end_date") or query.get("prior_end_date"),
            bookings_count=int(bookings) if bookings is not None else None,
            room_revenue=float(revenue) if revenue is not None else None,
            listing_name=query.get("listing_name"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class InteractionEvent:
    event_id: str
    ts: str
    channel_id: str | None
    thread_ts: str | None
    user_id_hash: str | None
    message: str
    message_tokens: list[str]
    thread_turn_index: int
    conversation_turns: int
    thread_state_status: str | None
    route: RouteInfo
    action: ActionInfo
    outcome: OutcomeInfo
    report_summary: ReportSummary
    pending_proposed: dict[str, Any] | None = None
    similar_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "ts": self.ts,
            "message": self.message,
            "message_tokens": self.message_tokens,
            "thread_turn_index": self.thread_turn_index,
            "conversation_turns": self.conversation_turns,
            "route": self.route.to_dict(),
            "action": self.action.to_dict(),
            "outcome": self.outcome.to_dict(),
            "report_summary": self.report_summary.to_dict(),
        }
        if self.channel_id:
            payload["channel_id"] = self.channel_id
        if self.thread_ts:
            payload["thread_ts"] = self.thread_ts
        if self.user_id_hash:
            payload["user_id_hash"] = self.user_id_hash
        if self.thread_state_status:
            payload["thread_state_status"] = self.thread_state_status
        if self.pending_proposed:
            payload["pending_proposed"] = self.pending_proposed
        if self.similar_events:
            payload["similar_events"] = self.similar_events
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> InteractionEvent:
        route_raw = payload.get("route") or {}
        action_raw = payload.get("action") or {}
        outcome_raw = payload.get("outcome") or {}
        summary_raw = payload.get("report_summary") or {}
        return cls(
            event_id=str(payload.get("event_id") or uuid4().hex),
            ts=str(payload.get("ts") or datetime.now(timezone.utc).isoformat()),
            channel_id=payload.get("channel_id"),
            thread_ts=payload.get("thread_ts"),
            user_id_hash=payload.get("user_id_hash"),
            message=str(payload.get("message") or ""),
            message_tokens=list(payload.get("message_tokens") or []),
            thread_turn_index=int(payload.get("thread_turn_index") or 0),
            conversation_turns=int(payload.get("conversation_turns") or 0),
            thread_state_status=payload.get("thread_state_status"),
            route=RouteInfo(
                kind=str(route_raw.get("kind") or "unknown"),
                source=str(route_raw.get("source") or "unknown"),
                confidence=route_raw.get("confidence"),
                extra_fields=list(route_raw.get("extra_fields") or []),
            ),
            action=ActionInfo(
                tool=action_raw.get("tool"),
                fields=list(action_raw.get("fields") or []),
                query=action_raw.get("query"),
                answer_plan=action_raw.get("answer_plan"),
            ),
            outcome=OutcomeInfo(
                ok=bool(outcome_raw.get("ok")),
                parser=str(outcome_raw.get("parser") or "unknown"),
                needs_clarification=bool(outcome_raw.get("needs_clarification")),
                latency_ms=outcome_raw.get("latency_ms"),
                error=outcome_raw.get("error"),
                confidence=outcome_raw.get("confidence"),
            ),
            report_summary=ReportSummary(**{
                key: summary_raw.get(key)
                for key in ReportSummary.__dataclass_fields__
                if key in summary_raw
            }),
            pending_proposed=payload.get("pending_proposed"),
            similar_events=list(payload.get("similar_events") or []),
        )


def append_interaction_event(event: InteractionEvent, *, data_root: Path | str = "data") -> None:
    if not interaction_logging_enabled():
        return
    root = _interactions_root(data_root)
    root.mkdir(parents=True, exist_ok=True)
    path = _daily_log_path(data_root)
    line = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    if event.channel_id:
        update_channel_profile(event, data_root=data_root)


def update_channel_profile(event: InteractionEvent, *, data_root: Path | str = "data") -> None:
    if not event.channel_id:
        return
    path = _channel_profile_path(data_root, event.channel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile: dict[str, Any] = {}
    if path.is_file():
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            profile = {}

    profile["channel_id"] = event.channel_id
    profile["last_seen_at"] = event.ts
    profile["interaction_count"] = int(profile.get("interaction_count") or 0) + 1

    if event.outcome.ok and not event.outcome.needs_clarification:
        profile["successful_count"] = int(profile.get("successful_count") or 0) + 1
    if event.outcome.needs_clarification:
        profile["clarification_count"] = int(profile.get("clarification_count") or 0) + 1

    summary = event.report_summary
    if summary.property_folder:
        props: dict[str, int] = dict(profile.get("properties") or {})
        key = str(summary.property_folder).lower()
        props[key] = int(props.get(key) or 0) + 1
        profile["properties"] = props

    routes: dict[str, int] = dict(profile.get("route_kinds") or {})
    route_key = event.route.kind
    routes[route_key] = int(routes.get(route_key) or 0) + 1
    profile["route_kinds"] = routes

    parsers: dict[str, int] = dict(profile.get("parsers") or {})
    parser_key = event.outcome.parser
    parsers[parser_key] = int(parsers.get(parser_key) or 0) + 1
    profile["parsers"] = parsers

    tokens: dict[str, int] = dict(profile.get("message_token_freq") or {})
    for token in event.message_tokens[:40]:
        tokens[token] = int(tokens.get(token) or 0) + 1
    profile["message_token_freq"] = dict(
        sorted(tokens.items(), key=lambda item: item[1], reverse=True)[:200]
    )

    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def load_channel_profile(
    data_root: Path | str,
    channel_id: str,
) -> dict[str, Any] | None:
    path = _channel_profile_path(data_root, channel_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def iter_interaction_events(
    data_root: Path | str,
    *,
    max_days: int = 30,
    limit: int | None = None,
) -> list[InteractionEvent]:
    root = _interactions_root(data_root)
    if not root.is_dir():
        return []

    paths = sorted(root.glob("*.jsonl"), reverse=True)
    events: list[InteractionEvent] = []
    for path in paths[:max_days]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(InteractionEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if limit is not None and len(events) >= limit:
                return events
    return events


def build_interaction_event(
    *,
    message: str,
    response_parser: str,
    route_kind: str,
    route_source: str,
    ok: bool,
    needs_clarification: bool,
    confidence: float,
    query: dict[str, Any] | None = None,
    snapshots: dict[str, Any] | None = None,
    pending_proposed: dict[str, Any] | None = None,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    user_id: str | None = None,
    conversation: list[dict[str, str]] | None = None,
    thread_state_status: str | None = None,
    route_confidence: float | None = None,
    extra_fields: list[str] | None = None,
    fields_used: list[str] | None = None,
    latency_ms: int | None = None,
    error: str | None = None,
    tool: str | None = None,
    similar_events: list[dict[str, Any]] | None = None,
    answer_plan: dict[str, Any] | None = None,
) -> InteractionEvent:
    tokens = sorted(tokenize_for_retrieval(message))
    turn_index = len(conversation or []) + 1
    return InteractionEvent(
        event_id=uuid4().hex,
        ts=datetime.now(timezone.utc).isoformat(),
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_id_hash=_hash_user_id(user_id),
        message=message.strip(),
        message_tokens=tokens,
        thread_turn_index=turn_index,
        conversation_turns=len(conversation or []),
        thread_state_status=thread_state_status,
        route=RouteInfo(
            kind=route_kind,
            source=route_source,
            confidence=route_confidence,
            extra_fields=list(extra_fields or []),
        ),
        action=ActionInfo(
            tool=tool,
            fields=list(fields_used or []),
            query=query,
            answer_plan=answer_plan,
        ),
        outcome=OutcomeInfo(
            ok=ok,
            parser=response_parser,
            needs_clarification=needs_clarification,
            latency_ms=latency_ms,
            error=error,
            confidence=confidence,
        ),
        report_summary=ReportSummary.from_query_and_snapshots(query, snapshots),
        pending_proposed=pending_proposed,
        similar_events=list(similar_events or []),
    )


def record_interaction(
    event: InteractionEvent,
    *,
    data_root: Path | str = "data",
) -> InteractionEvent:
    if not interaction_logging_enabled():
        return event
    try:
        append_interaction_event(event, data_root=data_root)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to record interaction event: %s", exc)
    return event
