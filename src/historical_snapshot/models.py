from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class BookingRecord:
    property_id: str
    property_name: str
    listing_name: str
    channel: str
    grouping: str
    reservation_date: date
    booking_date: date
    check_in_date: date
    check_out_date: date
    room_revenue: Decimal
    room_nights: int
    status: str
    available_room_nights: int | None = None


@dataclass(frozen=True)
class ValidationIssue:
    row_number: int
    reason: str
