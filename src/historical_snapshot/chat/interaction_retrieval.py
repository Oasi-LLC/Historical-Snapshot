from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from historical_snapshot.chat.interaction_log import (
    InteractionEvent,
    iter_interaction_events,
    load_channel_profile,
    tokenize_for_retrieval,
)


@dataclass(frozen=True)
class SimilarInteraction:
    event: InteractionEvent
    score: float
    reasons: tuple[str, ...]


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = left & right
    union = left | right
    return len(intersection) / len(union)


def score_interaction_similarity(
    message: str,
    candidate: InteractionEvent,
    *,
    channel_id: str | None = None,
    prefer_successful: bool = True,
) -> tuple[float, tuple[str, ...]]:
    query_tokens = tokenize_for_retrieval(message)
    candidate_tokens = set(candidate.message_tokens) or tokenize_for_retrieval(candidate.message)
    token_score = _jaccard(query_tokens, candidate_tokens)
    reasons: list[str] = []
    score = token_score
    if token_score > 0:
        reasons.append(f"token_overlap={token_score:.2f}")

    if channel_id and candidate.channel_id == channel_id:
        score += 0.15
        reasons.append("same_channel")

    if candidate.route.kind not in {"unknown", "pass"}:
        score += 0.05
        reasons.append(f"route={candidate.route.kind}")

    if prefer_successful and candidate.outcome.ok and not candidate.outcome.needs_clarification:
        score += 0.1
        reasons.append("successful")

    if candidate.action.query and candidate.report_summary.property_folder:
        score += 0.05
        reasons.append("has_query")

    return score, tuple(reasons)


def find_similar_interactions(
    message: str,
    *,
    data_root: str = "data",
    channel_id: str | None = None,
    limit: int = 5,
    min_score: float = 0.12,
    max_candidates: int = 300,
) -> list[SimilarInteraction]:
    """Retrieve similar past interactions for few-shot routing (Phase 1.5 hook)."""
    candidates = iter_interaction_events(data_root, limit=max_candidates)
    ranked: list[SimilarInteraction] = []
    for candidate in candidates:
        if channel_id and candidate.channel_id not in {channel_id, None}:
            continue
        score, reasons = score_interaction_similarity(
            message,
            candidate,
            channel_id=channel_id,
        )
        if score < min_score:
            continue
        ranked.append(
            SimilarInteraction(event=candidate, score=score, reasons=reasons)
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked[:limit]


def channel_context_summary(
    data_root: str,
    channel_id: str,
) -> dict[str, Any]:
    profile = load_channel_profile(data_root, channel_id)
    if not profile:
        return {"channel_id": channel_id, "interaction_count": 0}
    top_properties = sorted(
        (profile.get("properties") or {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    top_tokens = sorted(
        (profile.get("message_token_freq") or {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:15]
    return {
        "channel_id": channel_id,
        "interaction_count": profile.get("interaction_count", 0),
        "successful_count": profile.get("successful_count", 0),
        "clarification_count": profile.get("clarification_count", 0),
        "top_properties": top_properties,
        "top_route_kinds": sorted(
            (profile.get("route_kinds") or {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )[:8],
        "top_message_tokens": top_tokens,
        "last_seen_at": profile.get("last_seen_at"),
    }


def format_retrieval_context(
    message: str,
    *,
    data_root: str = "data",
    channel_id: str | None = None,
    limit: int = 3,
) -> str:
    """Format similar past turns for injection into a future LLM intent router."""
    similar = find_similar_interactions(
        message,
        data_root=data_root,
        channel_id=channel_id,
        limit=limit,
    )
    if not similar:
        return ""

    lines = ["Similar past interactions:"]
    for item in similar:
        event = item.event
        summary = event.report_summary
        query = event.action.query or {}
        lines.append(
            f"- score={item.score:.2f} message={event.message!r} "
            f"route={event.route.kind}/{event.route.source} "
            f"property={summary.property_folder or query.get('property')} "
            f"dates={summary.start_date}..{summary.end_date} "
            f"ok={event.outcome.ok}"
        )
    if channel_id:
        ctx = channel_context_summary(data_root, channel_id)
        if ctx.get("interaction_count"):
            lines.append(
                f"Channel profile: {ctx['interaction_count']} interactions, "
                f"top properties={ctx.get('top_properties')}"
            )
    return "\n".join(lines)
