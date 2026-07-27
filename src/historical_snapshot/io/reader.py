from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from historical_snapshot.config import PmsProfile, PropertyConfig, load_pms_profile
from historical_snapshot.io.postprocess import apply_property_postprocess
from historical_snapshot.models import BookingRecord, ValidationIssue

# Baseline aliases used when no PMS profile is supplied (tests, generic CSVs).
BASE_SCHEMA_ALIASES: dict[str, set[str]] = {
    "property_id": {"property_id", "propertyid", "hotel_id", "hotelid"},
    "property_name": {"property_name", "property", "hotel_name", "hotel"},
    "booking_date": {
        "date_and_time",
        "booking_date",
        "booked_date",
        "created_date",
        "reservation_date",
    },
    "reservation_date": {"reservation_date", "reservationdate"},
    "check_in_date": {"check_in_date", "arrival_date", "checkin_date", "arrival"},
    "check_out_date": {"check_out_date", "departure_date", "checkout_date", "departure"},
    "room_revenue": {"room_revenue", "revenue", "net_revenue", "room_rev", "amount"},
    "room_nights": {"room_nights", "nights", "_nights", "roomnights", "rn"},
    "status": {"status", "booking_status", "reservation_status"},
    "listing_name": {"listing_name", "listing"},
    "channel": {"channel"},
    "grouping": {"grouping"},
    "available_room_nights": {
        "available_room_nights",
        "available_nights",
        "inventory_room_nights",
    },
}

REQUIRED_FIELDS = {
    "booking_date",
    "check_in_date",
    "check_out_date",
    "room_revenue",
    "room_nights",
}


def _aliases_for_profile(pms_profile: PmsProfile | None) -> dict[str, set[str]]:
    if pms_profile is None:
        return {key: set(values) for key, values in BASE_SCHEMA_ALIASES.items()}

    merged: dict[str, set[str]] = {key: set(values) for key, values in BASE_SCHEMA_ALIASES.items()}
    for canonical, aliases in pms_profile.column_aliases.items():
        merged.setdefault(canonical, set()).update(_normalize(alias) for alias in aliases)
    return merged


def _normalize(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "_").replace("-", "_")
    normalized = normalized.replace("&", "and").replace("#", "")
    return normalized


_ALIAS_PRIORITY: dict[str, tuple[str, ...]] = {
    "listing_name": ("listing_name", "listing"),
    "booking_date": ("reservation_date", "booking_date", "booked_date", "created_date"),
}


def _ordered_aliases(
    canonical: str,
    canonical_aliases: set[str],
    pms_profile: PmsProfile | None,
) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()

    if pms_profile and canonical in pms_profile.column_aliases:
        for alias in pms_profile.column_aliases[canonical]:
            norm = _normalize(alias)
            if norm not in seen:
                ordered.append(norm)
                seen.add(norm)

    for alias in _ALIAS_PRIORITY.get(canonical, ()):
        if alias not in seen and alias in canonical_aliases:
            ordered.append(alias)
            seen.add(alias)

    for alias in sorted(canonical_aliases):
        if alias not in seen:
            ordered.append(alias)
            seen.add(alias)

    return tuple(ordered)


def _resolve_columns(
    fieldnames: Iterable[str],
    aliases: dict[str, set[str]],
    *,
    pms_profile: PmsProfile | None = None,
) -> dict[str, str]:
    normalized = {_normalize(name): name for name in fieldnames if name}
    mapping: dict[str, str] = {}
    for canonical, canonical_aliases in aliases.items():
        for alias in _ordered_aliases(canonical, canonical_aliases, pms_profile):
            if alias in normalized:
                mapping[canonical] = normalized[alias]
                break
    missing = REQUIRED_FIELDS - mapping.keys()
    if missing:
        missing_fields = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_fields}")
    return mapping


DEFAULT_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%m/%d/%y", "%m/%d/%y %H:%M")


def _parse_date(value: str, *, patterns: tuple[str, ...] = DEFAULT_DATE_FORMATS) -> datetime.date:
    for pattern in patterns:
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}")


WMB_US_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")


def _date_patterns_for_profile(
    pms_profile: PmsProfile | None,
    property_config: PropertyConfig | None = None,
) -> tuple[str, ...]:
    if pms_profile and pms_profile.date_formats:
        return pms_profile.date_formats
    if property_config and property_config.date_formats:
        return property_config.date_formats
    if property_config and property_config.folder.upper() == "WMB":
        return WMB_US_DATE_FORMATS
    return DEFAULT_DATE_FORMATS


def _parse_decimal(value: str) -> Decimal:
    cleaned = value.strip().replace(",", "").replace("$", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal: {value}") from exc


def _parse_int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid int: {value}") from exc


def _looks_like_us_date(value: str) -> bool:
    cleaned = value.strip()
    return "/" in cleaned and len(cleaned) <= 12 and not cleaned.startswith("$")


def _pick_room_revenue(row: dict[str, str], column_map: dict[str, str]) -> str:
    raw = row[column_map["room_revenue"]].strip()
    if raw and not _looks_like_us_date(raw):
        return raw
    for key in ("grand_total", "accommodation_total"):
        if key not in column_map:
            continue
        alt = row[column_map[key]].strip()
        if alt and not _looks_like_us_date(alt):
            return alt
    return raw


def _pick_listing_name(
    row: dict[str, str],
    *,
    pms_profile: PmsProfile | None,
    default_property_id: str,
) -> str:
    candidates: list[str] = ["listing_name", "listing", "room_type"]
    if pms_profile and "listing_name" in pms_profile.column_aliases:
        candidates = list(pms_profile.column_aliases["listing_name"])
    seen: set[str] = set()
    for alias in candidates:
        norm = _normalize(alias)
        if norm in seen:
            continue
        seen.add(norm)
        for key, value in row.items():
            if _normalize(key) != norm:
                continue
            cleaned = (value or "").strip()
            if cleaned and cleaned != default_property_id:
                return cleaned
            break
    return ""


def _pick_status(row: dict[str, str], column_map: dict[str, str]) -> str:
    status = row.get(column_map.get("status", ""), "").strip()
    if status and "united states" in status.lower():
        meal_plan = row.get("Meal Plan", "").strip()
        if meal_plan:
            return meal_plan
    return status


def _read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode CSV with a supported encoding: {path}")


def read_bookings_rows(
    rows: Iterable[dict[str, str]],
    fieldnames: Iterable[str],
    *,
    property_config: PropertyConfig | None = None,
    pms_profile: PmsProfile | None = None,
    apply_postprocess: bool = True,
    source_label: str = "",
    row_offset: int = 2,
) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    if pms_profile is None and property_config is not None:
        use_sheets = property_config.data_source is not None and property_config.data_source.is_google_sheets
        if not use_sheets:
            pms_profile = load_pms_profile(property_config.pms)

    aliases = _aliases_for_profile(pms_profile)
    date_patterns = _date_patterns_for_profile(pms_profile, property_config)
    default_property_name = (
        property_config.property_name
        if property_config
        else (pms_profile.defaults.get("property_name", "") if pms_profile else "")
    )
    default_property_id = property_config.property_id if property_config else ""

    records: list[BookingRecord] = []
    issues: list[ValidationIssue] = []

    column_map = _resolve_columns(fieldnames, aliases, pms_profile=pms_profile)
    for row_index, row in enumerate(rows, start=row_offset):
        try:
            booking_date = _parse_date(row[column_map["booking_date"]], patterns=date_patterns)
            if "reservation_date" in column_map:
                reservation_date = _parse_date(
                    row[column_map["reservation_date"]], patterns=date_patterns
                )
            else:
                reservation_date = booking_date
            check_in_date = _parse_date(row[column_map["check_in_date"]], patterns=date_patterns)
            check_out_date = _parse_date(row[column_map["check_out_date"]], patterns=date_patterns)
            raw_revenue = _pick_room_revenue(row, column_map)
            room_revenue = Decimal("0") if not raw_revenue else _parse_decimal(raw_revenue)
            room_nights = _parse_int(row[column_map["room_nights"]])
            available_room_nights = None
            if "available_room_nights" in column_map:
                raw_avail = row[column_map["available_room_nights"]].strip()
                if raw_avail:
                    available_room_nights = _parse_int(raw_avail)

            if check_out_date <= check_in_date:
                raise ValueError("check_out_date must be after check_in_date")
            if room_nights < 0:
                raise ValueError("room_nights cannot be negative")
            if room_revenue < 0:
                continue

            property_id = row.get(column_map.get("property_id", ""), "").strip()
            property_name = row.get(column_map.get("property_name", ""), "").strip()
            listing_name = _pick_listing_name(
                row,
                pms_profile=pms_profile,
                default_property_id=default_property_id,
            )
            channel = row.get(column_map.get("channel", ""), "").strip()
            grouping = row.get(column_map.get("grouping", ""), "").strip()
            if "status" in column_map:
                status = _pick_status(row, column_map).strip().lower()
            else:
                status = "confirmed"

            if not listing_name and "listing_name" not in column_map:
                listing_name = row.get("Listing Name", "").strip()
            if not property_name:
                property_name = default_property_name or "LaFave"
            if not property_id:
                property_id = default_property_id or listing_name or property_name
            if not status:
                status = "confirmed"

            if property_config and property_config.allowed_reservation_statuses:
                if status not in property_config.allowed_reservation_statuses:
                    continue

            if (
                property_config
                and property_config.allowed_payment_statuses
                and "payment_status" in column_map
            ):
                payment_status = row[column_map["payment_status"]].strip().lower()
                if payment_status not in property_config.allowed_payment_statuses:
                    continue

            records.append(
                BookingRecord(
                    property_id=property_id,
                    property_name=property_name,
                    listing_name=listing_name or property_id,
                    channel=channel,
                    grouping=grouping,
                    reservation_date=reservation_date,
                    booking_date=booking_date,
                    check_in_date=check_in_date,
                    check_out_date=check_out_date,
                    room_revenue=room_revenue,
                    room_nights=room_nights,
                    status=status,
                    available_room_nights=available_room_nights,
                )
            )
        except (KeyError, ValueError) as exc:
            label = f"{source_label} " if source_label else ""
            issues.append(ValidationIssue(row_number=row_index, reason=f"{label}{exc}"))

    if apply_postprocess:
        records = apply_property_postprocess(records, property_config)
    return records, issues


def read_bookings_csv(
    path: str | Path,
    *,
    property_config: PropertyConfig | None = None,
    pms_profile: PmsProfile | None = None,
    apply_postprocess: bool = True,
) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    csv_text = _read_csv_text(Path(path))
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")

    return read_bookings_rows(
        reader,
        reader.fieldnames,
        property_config=property_config,
        pms_profile=pms_profile,
        apply_postprocess=apply_postprocess,
        source_label=str(path),
    )
