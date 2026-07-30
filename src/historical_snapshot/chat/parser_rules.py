from __future__ import annotations

import re
from datetime import date, datetime

from historical_snapshot.chat.models import ChatSnapshotQuery, ParseResult
from historical_snapshot.config import PropertyConfig, list_property_configs
from historical_snapshot.core.metrics import count_live_listings_in_scope

PROPERTY_ALIASES: dict[str, str] = {
    "lafave": "lafave",
    "la fave": "lafave",
    "onera": "onera",
    "fbg": "onera",
    "fredericksburg": "onera",
    "wmb": "wmb",
    "wimberley": "wmb",
    "flohom": "flohom",
    "atx": "atx",
    "austin": "atx",
}

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

ISO_DATE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
MDY_DATE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?",
    re.IGNORECASE,
)
DATE_RANGE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|–|to)\s*"
    r"(?:(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+)?"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?",
    re.IGNORECASE,
)

YOY_PATTERN = re.compile(
    r"\b(vs\.?|versus|compared?\s+to|against)\s+(last\s+year|prior\s+year|ly\b|yoy\b)|\byoy\b|\blast\s+year\b",
    re.IGNORECASE,
)
NO_YOY_PATTERN = re.compile(r"\b(no\s+yoy|without\s+yoy|current\s+only)\b", re.IGNORECASE)
PACE_PATTERN = re.compile(r"\b(pace|on the books|as of today|otb)\b", re.IGNORECASE)
LISTING_AT_PATTERN = re.compile(
    r"\b(.+?)\s+at\s+(lafave|onera|fbg|fredericksburg|wmb|wimberley|flohom|atx|austin)\b",
    re.IGNORECASE,
)


def _default_year(text: str) -> int:
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return int(match.group(1))
    return date.today().year


def _parse_month_day(month_token: str, day: int, year: int) -> date:
    key = month_token.lower()
    month = MONTHS.get(key, MONTHS[key[:3]])
    return date(year, month, day)


def _parse_dates(text: str) -> tuple[date, date] | None:
    for match in ISO_DATE.finditer(text):
        start = datetime.strptime(match.group(0), "%Y-%m-%d").date()
        return start, start

    range_match = DATE_RANGE.search(text)
    if range_match:
        year = _default_year(text)
        if range_match.group(5):
            year = int(range_match.group(5))
        start = _parse_month_day(range_match.group(1), int(range_match.group(2)), year)
        if range_match.group(3):
            end = _parse_month_day(range_match.group(3), int(range_match.group(4)), year)
        else:
            end = _parse_month_day(range_match.group(1), int(range_match.group(4)), year)
        return start, end

    single = MDY_DATE.search(text)
    if single:
        year = int(single.group(3)) if single.group(3) else _default_year(text)
        day = _parse_month_day(single.group(1), int(single.group(2)), year)
        return day, day

    if re.search(r"\bfourth of july\b|\bjuly 4th\b|\bjul 4\b", text, re.IGNORECASE):
        year = _default_year(text)
        return date(year, 7, 4), date(year, 7, 4)

    return None


def _resolve_property(text: str) -> PropertyConfig | None:
    lowered = text.lower()
    listing_match = LISTING_AT_PATTERN.search(text)
    if listing_match:
        lowered = listing_match.group(2).lower()

    for alias, folder in sorted(PROPERTY_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            for config in list_property_configs():
                if config.folder.lower() == folder:
                    return config
    return None


def _normalize_listing_key(name: str) -> str:
    """Collapse punctuation so 'Great Lodge: King Room' ≈ 'great lodge king room'."""
    text = (name or "").lower()
    text = re.sub(r"[:|/•,_\-–—]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resolve_listing(text: str, config: PropertyConfig) -> str | None:
    listings = _resolve_all_listings(text, config)
    return listings[0] if listings else None


def _resolve_all_listings(text: str, config: PropertyConfig) -> list[str]:
    """Return every distinct listing mentioned, longest non-overlapping phrases first."""
    listing_match = LISTING_AT_PATTERN.search(text)
    if listing_match:
        candidate = listing_match.group(1).strip()
        return [_canonical_listing_name(candidate, config)]

    known = set(config.listing_inventory.keys()) | set(config.listing_aliases.keys())
    haystack = _normalize_listing_key(text)
    spans: list[tuple[int, int, str]] = []
    for name in sorted(known, key=lambda n: len(_normalize_listing_key(n)), reverse=True):
        key = _normalize_listing_key(name)
        if not key:
            continue
        for match in re.finditer(rf"(?<!\w){re.escape(key)}(?!\w)", haystack):
            start, end = match.span()
            if any(start < existing_end and end > existing_start for existing_start, existing_end, _ in spans):
                continue
            spans.append((start, end, _canonical_listing_name(name, config)))

    spans.sort(key=lambda item: item[0])
    seen: set[str] = set()
    ordered: list[str] = []
    for _, _, canonical in spans:
        if canonical not in seen:
            seen.add(canonical)
            ordered.append(canonical)
    return ordered


def _canonical_listing_name(name: str, config: PropertyConfig) -> str:
    # Exact alias first, then punctuation-normalized alias (CSV vs config naming).
    if name in config.listing_aliases:
        return config.listing_aliases[name]
    needle = _normalize_listing_key(name)
    for alias, canonical in config.listing_aliases.items():
        if _normalize_listing_key(alias) == needle:
            return canonical
    for inventory_name in config.listing_inventory:
        if _normalize_listing_key(inventory_name) == needle:
            return inventory_name
    return name


def _inventory_for(config: PropertyConfig, start: date, end: date) -> int:
    if config.inventory_mode == "live_listings" and config.listing_live_dates:
        return count_live_listings_in_scope(
            config.listing_live_dates,
            start,
            end,
            listing_inventory=config.listing_inventory or None,
        )
    return int(config.default_inventory_listings or 30)


def _clarification_message() -> str:
    props = ", ".join(config.property_name for config in list_property_configs())
    return (
        "I can look that up — which property and dates?\n\n"
        "Examples:\n"
        "• Onera July 31 2026\n"
        "• LaFave Jul 4-5 2025 vs last year\n"
        "• Diamond at Onera on July 31 2026\n\n"
        f"Properties: {props}"
    )


def parse_message(message: str) -> ParseResult:
    text = message.strip()
    if not text:
        return ParseResult(query=None, confidence=0.0, clarification=_clarification_message())

    config = _resolve_property(text)
    dates = _parse_dates(text)
    if config is None or dates is None:
        return ParseResult(query=None, confidence=0.2, clarification=_clarification_message())

    start, end = dates
    if end < start:
        start, end = end, start

    compare_prior_year = bool(YOY_PATTERN.search(text)) and not NO_YOY_PATTERN.search(text)
    if not NO_YOY_PATTERN.search(text) and not re.search(r"\b(current only|this year only)\b", text, re.I):
        compare_prior_year = True

    pace = bool(PACE_PATTERN.search(text))
    listing = _resolve_listing(text, config)
    inventory = _inventory_for(config, start, end)

    confidence = 0.85
    if listing:
        confidence += 0.05
    if compare_prior_year and YOY_PATTERN.search(text):
        confidence += 0.05

    query = ChatSnapshotQuery(
        property_folder=config.folder,
        property_id=config.property_id,
        property_name=config.property_name,
        start_date=start,
        end_date=end,
        listing_name=listing,
        compare_prior_year=compare_prior_year,
        pace=pace,
        inventory_listings=inventory,
        inventory_mode=config.inventory_mode,
    )
    return ParseResult(query=query, confidence=min(confidence, 1.0), parser="rules")
