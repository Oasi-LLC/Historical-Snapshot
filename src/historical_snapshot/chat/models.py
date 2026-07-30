from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ChatSnapshotQuery:
    property_folder: str
    property_id: str
    property_name: str
    start_date: date
    end_date: date
    listing_name: str | None = None
    compare_prior_year: bool = True
    pace: bool = False
    date_basis: str = "stay"
    breakdown_by: str = "listing"
    inventory_listings: int = 30
    inventory_mode: str = "manual"
    prior_stay_start_date: date | None = None
    prior_stay_end_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "property_folder": self.property_folder,
            "property_id": self.property_id,
            "property_name": self.property_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "listing_name": self.listing_name,
            "compare_prior_year": self.compare_prior_year,
            "pace": self.pace,
            "date_basis": self.date_basis,
            "breakdown_by": self.breakdown_by,
            "inventory_listings": self.inventory_listings,
            "inventory_mode": self.inventory_mode,
            "prior_stay_start_date": (
                self.prior_stay_start_date.isoformat() if self.prior_stay_start_date else None
            ),
            "prior_stay_end_date": (
                self.prior_stay_end_date.isoformat() if self.prior_stay_end_date else None
            ),
        }


@dataclass(frozen=True)
class ParseResult:
    query: ChatSnapshotQuery | None
    confidence: float
    clarification: str | None = None
    parser: str = "rules"

    @property
    def needs_clarification(self) -> bool:
        return self.query is None or self.clarification is not None


@dataclass
class ChatSnapshotResult:
    query: ChatSnapshotQuery
    current_total: dict
    pace_current: dict
    pace_prior: dict | None = None
    prior_final: dict | None = None
    pace_as_of_current: date | None = None
    pace_as_of_prior: date | None = None
    listing_current_total: dict | None = None
    listing_pace_current: dict | None = None
    listing_pace_prior: dict | None = None
    listing_prior_final: dict | None = None
    comparable_listings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "current_total": self.current_total,
            "pace_current": self.pace_current,
            "pace_prior": self.pace_prior,
            "prior_final": self.prior_final,
        }
        if self.pace_as_of_current:
            payload["pace_as_of_current"] = self.pace_as_of_current.isoformat()
        if self.pace_as_of_prior:
            payload["pace_as_of_prior"] = self.pace_as_of_prior.isoformat()
        for key in (
            "listing_current_total",
            "listing_pace_current",
            "listing_pace_prior",
            "listing_prior_final",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.comparable_listings:
            payload["comparable_listings"] = self.comparable_listings
        return payload


@dataclass(frozen=True)
class ChatResponse:
    reply: str
    query: dict[str, Any] | None
    confidence: float
    needs_clarification: bool
    snapshots: dict[str, Any] | None
    parser: str
    pending_proposed: dict[str, Any] | None = None
    route_kind: str | None = None
    route_source: str | None = None
    fields_used: list[str] | None = None
    latency_ms: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "reply": self.reply,
            "query": self.query,
            "confidence": self.confidence,
            "needs_clarification": self.needs_clarification,
            "snapshots": self.snapshots,
            "parser": self.parser,
        }
        if self.pending_proposed:
            payload["pending_proposed"] = self.pending_proposed
        if self.route_kind:
            payload["route_kind"] = self.route_kind
        if self.route_source:
            payload["route_source"] = self.route_source
        if self.fields_used:
            payload["fields_used"] = self.fields_used
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        if self.error:
            payload["error"] = self.error
        return payload
