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


def _resolve_columns(fieldnames: Iterable[str], aliases: dict[str, set[str]]) -> dict[str, str]:
    normalized = {_normalize(name): name for name in fieldnames if name}
    mapping: dict[str, str] = {}
    for canonical, canonical_aliases in aliases.items():
        for alias in canonical_aliases:
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


def _read_csv_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode CSV with a supported encoding: {path}")


def read_bookings_csv(
    path: str | Path,
    *,
    property_config: PropertyConfig | None = None,
    pms_profile: PmsProfile | None = None,
    apply_postprocess: bool = True,
) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    if pms_profile is None and property_config is not None:
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

    csv_text = _read_csv_text(Path(path))
    reader = csv.DictReader(io.StringIO(csv_text, newline=""))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")

    column_map = _resolve_columns(reader.fieldnames, aliases)
    for row_number, row in enumerate(reader, start=2):
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
            raw_revenue = row[column_map["room_revenue"]].strip()
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
            listing_name = row.get(column_map.get("listing_name", ""), "").strip()
            channel = row.get(column_map.get("channel", ""), "").strip()
            grouping = row.get(column_map.get("grouping", ""), "").strip()
            status = (
                row.get(column_map.get("status", ""), "").strip().lower()
                if "status" in column_map
                else "confirmed"
            )

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
            issues.append(ValidationIssue(row_number=row_number, reason=str(exc)))

    if apply_postprocess:
        records = apply_property_postprocess(records, property_config)
    return records, issues
