from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from historical_snapshot.core.bands import Band, band_for_days
from historical_snapshot.models import BookingRecord


def booking_window_days_for_record(record: BookingRecord) -> int:
    """Booking window = arrival date minus reservation date."""
    return (record.check_in_date - record.reservation_date).days

CANCELLED_STATUSES = {"cancelled", "canceled", "void"}

DateBasis = str  # "stay", "arrival", or "reservation"


@dataclass(frozen=True)
class SnapshotMetrics:
    property_id: str
    property_name: str
    start_date: date
    end_date: date
    bookings_count: int
    room_nights_sold: int
    room_revenue: Decimal
    adr: Decimal | None
    occupancy_pct: Decimal | None
    revpar: Decimal | None
    average_los: Decimal | None
    pickup_room_nights_by_band: dict[str, int]
    pickup_revenue_by_band: dict[str, Decimal]
    pickup_room_nights_share_by_band: dict[str, Decimal]
    pickup_revenue_share_by_band: dict[str, Decimal]
    booking_window_mean_days: Decimal | None
    booking_window_median_days: Decimal | None
    los_distribution: dict[str, int]
    arrival_day_of_week_mix: dict[str, int]
    channel_mix: dict[str, dict[str, Decimal | int | None]]
    as_of_date: date | None = None


def _safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _quantize_2(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal("0.01"))


def _mean(values: Iterable[int]) -> Decimal | None:
    vals = list(values)
    if not vals:
        return None
    return Decimal(sum(vals)) / Decimal(len(vals))


def _median(values: Iterable[int]) -> Decimal | None:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return Decimal(vals[mid])
    return (Decimal(vals[mid - 1]) + Decimal(vals[mid])) / Decimal(2)


def _dow_label(d: date) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


def _los_bucket(nights: int) -> str:
    if nights <= 1:
        return "1"
    if nights == 2:
        return "2"
    if nights == 3:
        return "3"
    return "4+"


def stay_overlaps_range(record: BookingRecord, start_date: date, end_date: date) -> bool:
    """True when the booking occupies at least one night in [start_date, end_date]."""
    return record.check_in_date <= end_date and record.check_out_date > start_date


def nights_in_range(record: BookingRecord, start_date: date, end_date: date) -> int:
    """Occupied nights from check-in through night before check-out, clipped to the range."""
    overlap_start = max(record.check_in_date, start_date)
    overlap_end_exclusive = min(record.check_out_date, end_date + timedelta(days=1))
    return max(0, (overlap_end_exclusive - overlap_start).days)


def revenue_in_range(record: BookingRecord, start_date: date, end_date: date) -> Decimal:
    nights = nights_in_range(record, start_date, end_date)
    if nights <= 0:
        return Decimal("0")
    if record.room_nights <= 0:
        return Decimal("0")
    return record.room_revenue * Decimal(nights) / Decimal(record.room_nights)


def _matches_date_filter(
    record: BookingRecord,
    start_date: date,
    end_date: date,
    date_basis: DateBasis,
) -> bool:
    if date_basis == "stay":
        return stay_overlaps_range(record, start_date, end_date)
    if date_basis == "arrival":
        target = record.check_in_date
    else:
        target = record.reservation_date
    return start_date <= target <= end_date


def compute_snapshot_metrics(
    records: list[BookingRecord],
    property_id: str,
    start_date: date,
    end_date: date,
    bands: list[Band],
    date_basis: DateBasis = "stay",
    inventory_units: int | None = None,
    as_of_date: date | None = None,
) -> SnapshotMetrics:
    filtered = [
        r
        for r in records
        if r.property_id == property_id
        and _matches_date_filter(r, start_date, end_date, date_basis)
        and r.status not in CANCELLED_STATUSES
        and (as_of_date is None or r.reservation_date <= as_of_date)
    ]

    property_name = filtered[0].property_name if filtered else ""

    if date_basis == "stay":
        room_nights_sold = sum(nights_in_range(r, start_date, end_date) for r in filtered)
        room_revenue = sum(
            (revenue_in_range(r, start_date, end_date) for r in filtered),
            start=Decimal("0"),
        )
    else:
        room_nights_sold = sum(r.room_nights for r in filtered)
        room_revenue = sum((r.room_revenue for r in filtered), start=Decimal("0"))

    if inventory_units is not None:
        nights_in_window = (end_date - start_date).days + 1
        available_room_nights_total = max(nights_in_window, 0) * inventory_units
    else:
        available_room_nights_total = sum(
            (r.available_room_nights or 0) for r in filtered if r.available_room_nights is not None
        )

    adr = _safe_div(room_revenue, Decimal(room_nights_sold))
    occupancy = (
        _safe_div(Decimal(room_nights_sold), Decimal(available_room_nights_total))
        if available_room_nights_total > 0
        else None
    )
    revpar = (
        _safe_div(room_revenue, Decimal(available_room_nights_total))
        if available_room_nights_total > 0
        else None
    )
    los = _safe_div(Decimal(room_nights_sold), Decimal(len(filtered)))

    pickup_nights: dict[str, int] = defaultdict(int)
    pickup_revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    channel_nights: dict[str, int] = defaultdict(int)
    channel_revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    los_dist: dict[str, int] = defaultdict(int)
    arrival_dow: dict[str, int] = defaultdict(int)
    booking_window_days: list[int] = []

    for record in filtered:
        days = booking_window_days_for_record(record)
        label = band_for_days(days, bands)
        booking_window_days.append(days)
        if date_basis == "stay":
            nights = nights_in_range(record, start_date, end_date)
            revenue = revenue_in_range(record, start_date, end_date)
        else:
            nights = record.room_nights
            revenue = record.room_revenue
        pickup_nights[label] += nights
        pickup_revenue[label] += revenue

        channel = (record.channel or "Unknown").strip() or "Unknown"
        channel_nights[channel] += nights
        channel_revenue[channel] += revenue

        los_dist[_los_bucket(nights)] += 1
        arrival_dow[_dow_label(record.check_in_date)] += 1

    # Shares for pickup bands
    total_nights_dec = Decimal(room_nights_sold)
    total_rev_dec = room_revenue
    pickup_nights_share: dict[str, Decimal] = {}
    pickup_rev_share: dict[str, Decimal] = {}
    for band, nights in pickup_nights.items():
        pickup_nights_share[band] = _quantize_2(_safe_div(Decimal(nights) * Decimal(100), total_nights_dec) or Decimal(0)) or Decimal("0.00")
        pickup_rev_share[band] = _quantize_2(_safe_div(pickup_revenue[band] * Decimal(100), total_rev_dec) or Decimal(0)) or Decimal("0.00")

    # Channel mix (revenue/nights + ADR + shares)
    channel_mix: dict[str, dict[str, Decimal | int | None]] = {}
    for channel, nights in channel_nights.items():
        rev = channel_revenue[channel]
        channel_mix[channel] = {
            "bookings_count": sum(1 for r in filtered if ((r.channel or "Unknown").strip() or "Unknown") == channel),
            "room_nights": nights,
            "revenue": _quantize_2(rev) or Decimal("0.00"),
            "adr": _quantize_2(_safe_div(rev, Decimal(nights)) if nights else None),
            "revenue_share_pct": _quantize_2(_safe_div(rev * Decimal(100), total_rev_dec) if total_rev_dec else None),
            "nights_share_pct": _quantize_2(_safe_div(Decimal(nights) * Decimal(100), total_nights_dec) if total_nights_dec else None),
        }

    return SnapshotMetrics(
        property_id=property_id,
        property_name=property_name,
        start_date=start_date,
        end_date=end_date,
        bookings_count=len(filtered),
        room_nights_sold=room_nights_sold,
        room_revenue=_quantize_2(room_revenue) or Decimal("0.00"),
        adr=_quantize_2(adr),
        occupancy_pct=_quantize_2(occupancy * Decimal(100)) if occupancy is not None else None,
        revpar=_quantize_2(revpar),
        average_los=_quantize_2(los),
        pickup_room_nights_by_band=dict(pickup_nights),
        pickup_revenue_by_band={k: _quantize_2(v) or Decimal("0.00") for k, v in pickup_revenue.items()},
        pickup_room_nights_share_by_band=pickup_nights_share,
        pickup_revenue_share_by_band=pickup_rev_share,
        booking_window_mean_days=_quantize_2(_mean(booking_window_days)),
        booking_window_median_days=_quantize_2(_median(booking_window_days)),
        los_distribution=dict(los_dist),
        arrival_day_of_week_mix=dict(arrival_dow),
        channel_mix=channel_mix,
        as_of_date=as_of_date,
    )


def compute_portfolio_snapshot_metrics(
    records: list[BookingRecord],
    property_name: str,
    start_date: date,
    end_date: date,
    bands: list[Band],
    date_basis: DateBasis = "stay",
    inventory_units: int | None = None,
    as_of_date: date | None = None,
) -> SnapshotMetrics:
    synthetic = "__portfolio__"
    lifted: list[BookingRecord] = []
    for r in records:
        lifted.append(
            BookingRecord(
                property_id=synthetic,
                property_name=property_name,
                listing_name=r.listing_name,
                channel=r.channel,
                grouping=r.grouping,
                reservation_date=r.reservation_date,
                booking_date=r.booking_date,
                check_in_date=r.check_in_date,
                check_out_date=r.check_out_date,
                room_revenue=r.room_revenue,
                room_nights=r.room_nights,
                status=r.status,
                available_room_nights=r.available_room_nights,
            )
        )
    return compute_snapshot_metrics(
        records=lifted,
        property_id=synthetic,
        start_date=start_date,
        end_date=end_date,
        bands=bands,
        date_basis=date_basis,
        inventory_units=inventory_units,
        as_of_date=as_of_date,
    )
