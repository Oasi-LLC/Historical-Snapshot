from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, replace as dataclasses_replace
from typing import Any

from historical_snapshot.chat.thread_state import TurnClassification, TurnKind

LOGGER = logging.getLogger(__name__)

# Matches "top 3", "top 10 listings", etc. so a literal count in the user's own words is
# always respected, regardless of which classifier (rules/intent router/answer planner/
# Claude's own tool call) produced the AnswerPlan.
TOP_N_PATTERN = re.compile(r"\btop\s+(\d{1,2})\b", re.IGNORECASE)
DEFAULT_TOP_N = 5
MAX_TOP_N = 20


def extract_top_n(message: str) -> int | None:
    match = TOP_N_PATTERN.search(message or "")
    if not match:
        return None
    value = int(match.group(1))
    if value <= 0:
        return None
    return min(value, MAX_TOP_N)

# Presentation blocks the renderer can compose. Order in `blocks` is render order.
VALID_BLOCKS = frozenset(
    {
        "header",
        "performance",
        "top_listings",
        "listing_compare",
        "channel_mix",
        "booking_window",  # included inside performance today; reserved for future split
        "none",
    }
)

VALID_BASIS = frozenset({"current", "ly_pace", "ly_final"})

VALID_INTENTS = frozenset(
    {
        "portfolio_snapshot",
        "listing_rank",
        "channel_mix",
        "listing_detail",
        "listing_compare",
        "explain",
        "custom",
    }
)


@dataclass(frozen=True)
class AnswerPlan:
    """Declarative description of what to fetch and how to present it.

    Reply modes were a closed enum of full report templates. An AnswerPlan is compositional:
    the question picks intent + basis + blocks + entities; the renderer assembles only those
    pieces. Deterministic classifiers still produce plans; freeform questions can too via LLM.
    """

    intent: str
    basis: tuple[str, ...] = ("current", "ly_pace", "ly_final")
    blocks: tuple[str, ...] = ("header", "performance")
    listings: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    include_interpretation: bool = True
    interpretation_mode: str = "full"
    confidence: float | None = None
    source: str = "rules"  # rules | llm
    subtitle: str | None = None
    top_n: int | None = None  # explicit "top N" count from the user's own words, if any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_top_n_from_message(self, message: str) -> AnswerPlan:
        """Override top_n with an explicit count from the user's message, if present.

        A literal "top 3" in the question should always win over whatever default limit
        the renderer would otherwise use - applied as a final step regardless of which
        classifier produced this plan, so it's consistent across all routing paths.
        """
        requested = extract_top_n(message)
        if requested is None or requested == self.top_n:
            return self
        return dataclasses_replace(self, top_n=requested)

    @property
    def needs_listing_breakdown(self) -> bool:
        return (
            "top_listings" in self.blocks
            or "listing_compare" in self.blocks
            or "listing_breakdown" in self.fields
            or bool(self.listings)
        )

    @property
    def needs_channel_breakdown(self) -> bool:
        return "channel_mix" in self.blocks or "channel_breakdown" in self.fields

    def snapshot_fields(self) -> list[str]:
        selected: list[str] = list(self.fields)
        if self.needs_listing_breakdown and "listing_breakdown" not in selected:
            selected.append("listing_breakdown")
        if self.needs_channel_breakdown and "channel_breakdown" not in selected:
            selected.append("channel_breakdown")
        return selected


def _clean_basis(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)):
        return ("current", "ly_pace", "ly_final")
    basis = tuple(item for item in raw if item in VALID_BASIS)
    return basis or ("current", "ly_pace", "ly_final")


def _clean_blocks(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)):
        return ("header", "performance")
    blocks = tuple(item for item in raw if item in VALID_BLOCKS)
    if not blocks or blocks == ("none",):
        return ("none",)
    # Header first when present among data blocks.
    ordered: list[str] = []
    if "header" in blocks:
        ordered.append("header")
    for block in blocks:
        if block not in ordered and block != "none":
            ordered.append(block)
    return tuple(ordered) if ordered else ("none",)


def parse_answer_plan(payload: dict[str, Any], *, source: str = "llm") -> AnswerPlan | None:
    intent = str(payload.get("intent") or "custom").strip().lower()
    if intent not in VALID_INTENTS:
        intent = "custom"

    listings_raw = payload.get("listings") or payload.get("listing_names") or []
    if isinstance(listings_raw, str):
        listings_raw = [listings_raw]
    listings = tuple(str(item).strip() for item in listings_raw if str(item).strip())

    fields_raw = payload.get("fields") or []
    if isinstance(fields_raw, str):
        fields_raw = [fields_raw]
    fields = tuple(str(item).strip() for item in fields_raw if str(item).strip())

    confidence_raw = payload.get("confidence")
    confidence = float(confidence_raw) if confidence_raw is not None else None

    include_interp = payload.get("include_interpretation")
    if include_interp is None:
        include_interp = True

    interpretation_mode = str(payload.get("interpretation_mode") or _default_interp_mode(intent))
    subtitle = payload.get("subtitle")
    subtitle_text = str(subtitle).strip() if subtitle else None

    plan = AnswerPlan(
        intent=intent,
        basis=_clean_basis(payload.get("basis")),
        blocks=_clean_blocks(payload.get("blocks")),
        listings=listings,
        fields=fields,
        include_interpretation=bool(include_interp),
        interpretation_mode=interpretation_mode,
        confidence=confidence,
        source=source,
        subtitle=subtitle_text,
    )
    return plan


def _default_interp_mode(intent: str) -> str:
    return {
        "portfolio_snapshot": "full",
        "listing_rank": "delta_listings",
        "channel_mix": "delta_channels",
        "listing_detail": "delta_listing",
        "listing_compare": "listing_compare",
        "explain": "interpretation_only",
        "custom": "interpretation_only",
    }.get(intent, "full")


def plan_from_classification(classification: TurnClassification) -> AnswerPlan:
    """Map today's TurnKind / reply-mode behavior onto an AnswerPlan (no behavior change)."""
    kind = classification.kind

    if kind == TurnKind.INTERPRET_ONLY:
        return AnswerPlan(
            intent="explain",
            basis=("current", "ly_pace", "ly_final"),
            blocks=("none",),
            fields=classification.extra_fields,
            include_interpretation=True,
            interpretation_mode="interpretation_only",
            source="rules",
        )

    if kind == TurnKind.LISTING_COMPARE:
        focus = classification.compare_focus or "ly_final"
        if focus == "current":
            basis: tuple[str, ...] = ("current",)
        elif focus == "both":
            basis = ("ly_final", "current")
        else:
            basis = ("ly_final",)
        return AnswerPlan(
            intent="listing_compare",
            basis=basis,
            blocks=("header", "listing_compare"),
            listings=classification.listing_names,
            fields=("listing_breakdown",),
            include_interpretation=True,
            interpretation_mode="listing_compare",
            source="rules",
            subtitle="Listing comparison for this stay window.",
        )

    if kind == TurnKind.LISTING_DRILLDOWN:
        return AnswerPlan(
            intent="listing_detail",
            basis=("current", "ly_pace", "ly_final"),
            blocks=("header", "performance"),
            listings=(classification.listing_name,) if classification.listing_name else (),
            fields=classification.extra_fields or ("listing_breakdown",),
            include_interpretation=True,
            interpretation_mode="delta_listing",
            source="rules",
        )

    if kind == TurnKind.FOLLOW_UP:
        fields = set(classification.extra_fields)
        if "channel_breakdown" in fields:
            return AnswerPlan(
                intent="channel_mix",
                basis=("current", "ly_pace", "ly_final"),
                blocks=("header", "channel_mix"),
                fields=("channel_breakdown", "listing_breakdown"),
                include_interpretation=True,
                interpretation_mode="delta_channels",
                source="rules",
                subtitle="Channel mix for this stay window.",
            )
        return AnswerPlan(
            intent="listing_rank",
            basis=("current", "ly_final"),
            blocks=("header", "top_listings"),
            fields=("listing_breakdown",),
            include_interpretation=True,
            interpretation_mode="delta_listings",
            source="rules",
            subtitle="Listing breakdown for this stay window.",
        )

    # CONFIRM / DATE_OVERRIDE / default
    return AnswerPlan(
        intent="portfolio_snapshot",
        basis=("current", "ly_pace", "ly_final"),
        blocks=("header", "performance"),
        fields=classification.extra_fields,
        include_interpretation=True,
        interpretation_mode="full",
        source="rules",
    )


def reply_mode_for_plan(plan: AnswerPlan) -> str:
    """Bridge to existing interpretation guidance keys."""
    return plan.interpretation_mode or "full"


def plan_from_tool_args(
    *,
    message: str = "",
    fields: list[str] | None = None,
    listing_name: str | None = None,
    property_token: str | None = None,
    answer_plan_raw: dict[str, Any] | None = None,
) -> AnswerPlan:
    """Derive an AnswerPlan for Path A (Claude tool loop).

    Prefer an explicit `answer_plan` from the tool call; otherwise infer from fields,
    listing filter, and listings mentioned in the user message.
    """
    if isinstance(answer_plan_raw, dict):
        parsed = parse_answer_plan(answer_plan_raw, source="claude_tool")
        if parsed is not None:
            return _enrich_plan_listings(parsed, message=message, property_token=property_token)

    field_set = {str(item).strip() for item in (fields or []) if str(item).strip()}
    listing = (listing_name or "").strip() or None
    discovered = _discover_listings(message, property_token)

    if len(discovered) >= 2:
        return AnswerPlan(
            intent="listing_compare",
            basis=("ly_final",) if _message_wants_ly(message) else ("current", "ly_final"),
            blocks=("header", "listing_compare"),
            listings=discovered,
            fields=("listing_breakdown",),
            include_interpretation=True,
            interpretation_mode="listing_compare",
            source="tool_inferred",
            subtitle="Listing comparison for this stay window.",
        )

    if listing or len(discovered) == 1:
        name = listing or discovered[0]
        return AnswerPlan(
            intent="listing_detail",
            basis=("current", "ly_pace", "ly_final"),
            blocks=("header", "performance"),
            listings=(name,),
            fields=tuple(field_set) or ("listing_breakdown",),
            include_interpretation=True,
            interpretation_mode="delta_listing",
            source="tool_inferred",
        )

    if "channel_breakdown" in field_set and "listing_breakdown" not in field_set:
        return AnswerPlan(
            intent="channel_mix",
            basis=("current", "ly_pace", "ly_final"),
            blocks=("header", "channel_mix"),
            fields=("channel_breakdown",),
            include_interpretation=True,
            interpretation_mode="delta_channels",
            source="tool_inferred",
            subtitle="Channel mix for this stay window.",
        )

    if "channel_breakdown" in field_set:
        return AnswerPlan(
            intent="channel_mix",
            basis=("current", "ly_pace", "ly_final"),
            blocks=("header", "channel_mix"),
            fields=("channel_breakdown", "listing_breakdown"),
            include_interpretation=True,
            interpretation_mode="delta_channels",
            source="tool_inferred",
            subtitle="Channel mix for this stay window.",
        )

    if "listing_breakdown" in field_set:
        return AnswerPlan(
            intent="listing_rank",
            basis=("current", "ly_final"),
            blocks=("header", "top_listings"),
            fields=("listing_breakdown",),
            include_interpretation=True,
            interpretation_mode="delta_listings",
            source="tool_inferred",
            subtitle="Listing breakdown for this stay window.",
        )

    return AnswerPlan(
        intent="portfolio_snapshot",
        basis=("current", "ly_pace", "ly_final"),
        blocks=("header", "performance"),
        fields=tuple(field_set),
        include_interpretation=True,
        interpretation_mode="full",
        source="tool_inferred",
    )


def _message_wants_ly(message: str) -> bool:
    text = (message or "").lower()
    return any(token in text for token in ("last year", "ly final", " ly", "prior year", "yoy"))


def _discover_listings(message: str, property_token: str | None) -> tuple[str, ...]:
    if not message or not property_token:
        return ()
    try:
        from historical_snapshot.chat.parser_rules import _resolve_all_listings
        from historical_snapshot.config import load_property_config

        config = load_property_config(property_token)
    except Exception:  # noqa: BLE001
        return ()
    if config is None:
        return ()
    return tuple(_resolve_all_listings(message, config))


def _enrich_plan_listings(
    plan: AnswerPlan,
    *,
    message: str,
    property_token: str | None,
) -> AnswerPlan:
    if plan.listings or not message:
        return plan
    discovered = _discover_listings(message, property_token)
    if not discovered:
        return plan
    if len(discovered) >= 2 and plan.intent in {"listing_compare", "custom", "explain"}:
        return AnswerPlan(
            intent="listing_compare",
            basis=plan.basis if plan.basis != ("current", "ly_pace", "ly_final") else ("ly_final",),
            blocks=("header", "listing_compare"),
            listings=discovered,
            fields=plan.fields or ("listing_breakdown",),
            include_interpretation=plan.include_interpretation,
            interpretation_mode="listing_compare",
            confidence=plan.confidence,
            source=plan.source,
            subtitle=plan.subtitle or "Listing comparison for this stay window.",
        )
    if len(discovered) == 1 and plan.intent in {"listing_detail", "custom"}:
        return AnswerPlan(
            intent="listing_detail",
            basis=plan.basis,
            blocks=plan.blocks if plan.blocks != ("none",) else ("header", "performance"),
            listings=discovered,
            fields=plan.fields,
            include_interpretation=plan.include_interpretation,
            interpretation_mode=plan.interpretation_mode,
            confidence=plan.confidence,
            source=plan.source,
            subtitle=plan.subtitle,
        )
    return plan


def log_answer_plan(plan: AnswerPlan, *, path: str) -> None:
    LOGGER.info(
        "answer_plan path=%s intent=%s blocks=%s basis=%s listings=%s source=%s confidence=%s",
        path,
        plan.intent,
        list(plan.blocks),
        list(plan.basis),
        list(plan.listings),
        plan.source,
        plan.confidence,
    )
