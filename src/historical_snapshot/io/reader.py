from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from historical_snapshot.models import BookingRecord, ValidationIssue

SCHEMA_ALIASES: dict[str, set[str]] = {
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


def _normalize(name: str) -> str:
    normalized = name.strip().lower().replace(" ", "_")
    normalized = normalized.replace("&", "and").replace("#", "")
    return normalized


def _resolve_columns(fieldnames: Iterable[str]) -> dict[str, str]:
    normalized = {_normalize(name): name for name in fieldnames if name}
    mapping: dict[str, str] = {}
    for canonical, aliases in SCHEMA_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[canonical] = normalized[alias]
                break
    missing = REQUIRED_FIELDS - mapping.keys()
    if missing:
        missing_fields = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_fields}")
    return mapping


def _parse_date(value: str) -> datetime.date:
    patterns = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%m/%d/%y", "%m/%d/%y %H:%M")
    for pattern in patterns:
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}")


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


def read_bookings_csv(path: str | Path) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    records: list[BookingRecord] = []
    issues: list[ValidationIssue] = []

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row.")

        column_map = _resolve_columns(reader.fieldnames)
        for row_number, row in enumerate(reader, start=2):
            try:
                booking_date = _parse_date(row[column_map["booking_date"]])
                if "reservation_date" in column_map:
                    reservation_date = _parse_date(row[column_map["reservation_date"]])
                else:
                    reservation_date = booking_date
                check_in_date = _parse_date(row[column_map["check_in_date"]])
                check_out_date = _parse_date(row[column_map["check_out_date"]])
                room_revenue = _parse_decimal(row[column_map["room_revenue"]])
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
                status = row.get(column_map.get("status", ""), "").strip().lower() if "status" in column_map else "confirmed"

                # Lafave fallback mapping.
                if not listing_name and "listing_name" not in column_map:
                    listing_name = row.get("Listing Name", "").strip()
                if not property_name:
                    property_name = "LaFave"
                if not property_id:
                    property_id = listing_name or property_name
                if not status:
                    status = "confirmed"

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

    return records, issues
