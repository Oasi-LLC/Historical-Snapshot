from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
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

DOW_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKEND_WEEKDAYS = {4, 5}  # Friday=4, Saturday=5


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def classify_day_type(d: date) -> str:
    """Return 'Fri-Sat' or 'Sun-Thu' for a given date."""
    return "Fri-Sat" if d.weekday() in WEEKEND_WEEKDAYS else "Sun-Thu"


def prorate_revenue(total_revenue: Decimal, total_nights: int) -> Decimal:
    """Even per-night proration of a booking's revenue."""
    if total_nights <= 0:
        return Decimal("0")
    return total_revenue / Decimal(total_nights)


# ---------------------------------------------------------------------------
# Intermediate detail layers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NightDetail:
    """One row per (booking x occupied night) within the analysis window."""
    night_date: date
    day_of_week: str
    day_type: str
    listing_name: str
    channel: str
    booking_window_days: int
    prorated_revenue: Decimal
    check_in_date: date
    check_out_date: date
    reservation_date: date
    booking_record: BookingRecord


@dataclass(frozen=True)
class BookingDetail:
    """One row per booking touching the analysis window."""
    listing_name: str
    channel: str
    reservation_date: date
    check_in_date: date
    check_out_date: date
    los: int
    nights_in_range: int
    revenue_in_range: Decimal
    booking_adr: Decimal | None
    booking_window_days: int
    day_types_touched: frozenset[str]
    booking_record: BookingRecord


@dataclass(frozen=True)
class CalendarDetail:
    """One row per (date x listing) for every available date in the window."""
    night_date: date
    day_of_week: str
    day_type: str
    listing_name: str
    is_available: bool
    is_sold: bool
    revenue: Decimal


# ---------------------------------------------------------------------------
# Builder functions
# ---------------------------------------------------------------------------

def build_night_details(
    records: list[BookingRecord],
    start_date: date,
    end_date: date,
    *,
    date_basis: DateBasis = "stay",
    as_of_date: date | None = None,
) -> list[NightDetail]:
    """Build the night-level detail table from filtered bookings."""
    details: list[NightDetail] = []
    for r in _filter_records_no_property(records, start_date, end_date, date_basis, as_of_date):
        if date_basis != "stay":
            continue  # night-level only meaningful for stay-overlap
        per_night = prorate_revenue(r.room_revenue, r.room_nights)
        bw = booking_window_days_for_record(r)
        ch = (r.channel or "Unknown").strip() or "Unknown"
        overlap_start = max(r.check_in_date, start_date)
        overlap_end_excl = min(r.check_out_date, end_date + timedelta(days=1))
        d = overlap_start
        while d < overlap_end_excl:
            details.append(NightDetail(
                night_date=d,
                day_of_week=DOW_LABELS[d.weekday()],
                day_type=classify_day_type(d),
                listing_name=r.listing_name or r.property_id,
                channel=ch,
                booking_window_days=bw,
                prorated_revenue=per_night,
                check_in_date=r.check_in_date,
                check_out_date=r.check_out_date,
                reservation_date=r.reservation_date,
                booking_record=r,
            ))
            d += timedelta(days=1)
    return details


def build_booking_details(
    records: list[BookingRecord],
    start_date: date,
    end_date: date,
    *,
    date_basis: DateBasis = "stay",
    as_of_date: date | None = None,
) -> list[BookingDetail]:
    """Build the booking-level detail table from filtered bookings."""
    details: list[BookingDetail] = []
    for r in _filter_records_no_property(records, start_date, end_date, date_basis, as_of_date):
        if date_basis == "stay":
            nir = nights_in_range(r, start_date, end_date)
            rir = revenue_in_range(r, start_date, end_date)
        else:
            nir = r.room_nights
            rir = r.room_revenue

        adr = _safe_div(rir, Decimal(nir)) if nir > 0 else None
        ch = (r.channel or "Unknown").strip() or "Unknown"

        touched: set[str] = set()
        if date_basis == "stay":
            d = max(r.check_in_date, start_date)
            end_excl = min(r.check_out_date, end_date + timedelta(days=1))
            while d < end_excl:
                touched.add(classify_day_type(d))
                d += timedelta(days=1)
        else:
            d = r.check_in_date
            while d < r.check_out_date:
                touched.add(classify_day_type(d))
                d += timedelta(days=1)

        details.append(BookingDetail(
            listing_name=r.listing_name or r.property_id,
            channel=ch,
            reservation_date=r.reservation_date,
            check_in_date=r.check_in_date,
            check_out_date=r.check_out_date,
            los=r.room_nights,
            nights_in_range=nir,
            revenue_in_range=rir,
            booking_adr=_quantize_2(adr),
            booking_window_days=booking_window_days_for_record(r),
            day_types_touched=frozenset(touched),
            booking_record=r,
        ))
    return details


def build_calendar_details(
    night_details: list[NightDetail],
    start_date: date,
    end_date: date,
    listing_live_dates: dict[str, date] | None = None,
    listings: Iterable[str] | None = None,
) -> list[CalendarDetail]:
    """Build the calendar table by joining against NightDetail for sold/revenue.

    One row per (date x listing) for every available date in the window.
    """
    if listing_live_dates is None:
        listing_live_dates = {}

    listing_set: set[str]
    if listings is not None:
        listing_set = set(listings)
    else:
        listing_set = set(listing_live_dates.keys())
        listing_set.update(nd.listing_name for nd in night_details)

    sold_map: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for nd in night_details:
        sold_map[(nd.night_date, nd.listing_name)] += nd.prorated_revenue

    details: list[CalendarDetail] = []
    d = start_date
    while d <= end_date:
        dow = DOW_LABELS[d.weekday()]
        dt = classify_day_type(d)
        for listing in sorted(listing_set):
            live = listing_live_dates.get(listing)
            is_avail = live is not None and d >= live
            key = (d, listing)
            rev = sold_map.get(key, Decimal("0"))
            is_sold = rev > 0 or key in sold_map
            details.append(CalendarDetail(
                night_date=d,
                day_of_week=dow,
                day_type=dt,
                listing_name=listing,
                is_available=is_avail,
                is_sold=key in sold_map,
                revenue=rev,
            ))
        d += timedelta(days=1)
    return details


def _filter_records_no_property(
    records: list[BookingRecord],
    start_date: date,
    end_date: date,
    date_basis: DateBasis,
    as_of_date: date | None,
) -> list[BookingRecord]:
    """Filter records by date basis and as-of, without property_id filter."""
    return [
        r for r in records
        if _matches_date_filter(r, start_date, end_date, date_basis)
        and r.status not in CANCELLED_STATUSES
        and (as_of_date is None or r.reservation_date <= as_of_date)
    ]


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
    pickup_bookings_by_band: dict[str, int]
    pickup_room_nights_by_band: dict[str, int]
    pickup_revenue_by_band: dict[str, Decimal]
    pickup_bookings_share_by_band: dict[str, Decimal]
    pickup_room_nights_share_by_band: dict[str, Decimal]
    pickup_revenue_share_by_band: dict[str, Decimal]
    booking_window_mean_days: Decimal | None
    booking_window_median_days: Decimal | None
    los_distribution: dict[str, int]
    arrival_day_of_week_mix: dict[str, int]
    channel_mix: dict[str, dict[str, Decimal | int | None]]
    as_of_date: date | None = None
    # Enriched fields (populated by the new aggregation layer)
    adr_median: Decimal | None = None
    adr_max: Decimal | None = None
    adr_p25: Decimal | None = None
    adr_p75: Decimal | None = None
    day_type_metrics: dict | None = None
    channel_deep_metrics: dict | None = None
    dow_metrics: dict | None = None


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
    return DOW_LABELS[d.weekday()]


def _los_bucket(nights: int) -> str:
    if nights <= 1:
        return "1"
    if nights == 2:
        return "2"
    if nights == 3:
        return "3"
    return "4+"


def listing_available_nights_in_window(
    live_date: date,
    start_date: date,
    end_date: date,
) -> int:
    """Nights a unit counts toward inventory within [start_date, end_date]."""
    effective_start = max(start_date, live_date)
    if effective_start > end_date:
        return 0
    return (end_date - effective_start).days + 1


def live_listings_in_scope(
    listing_live_dates: dict[str, date],
    start_date: date,
    end_date: date,
) -> frozenset[str]:
    """Listing names with at least one available night in the snapshot window."""
    return frozenset(
        name
        for name, live_date in listing_live_dates.items()
        if listing_available_nights_in_window(live_date, start_date, end_date) > 0
    )


def comparable_live_listings(
    listing_live_dates: dict[str, date],
    start_date: date,
    end_date: date,
    yoy_start_date: date,
    yoy_end_date: date,
) -> frozenset[str]:
    """Units in scope for both the measured period and the YoY comparison period."""
    current = live_listings_in_scope(listing_live_dates, start_date, end_date)
    other = live_listings_in_scope(listing_live_dates, yoy_start_date, yoy_end_date)
    return current & other


def count_live_listings_in_scope(
    listing_live_dates: dict[str, date],
    start_date: date,
    end_date: date,
    *,
    listings: Iterable[str] | None = None,
    listing_inventory: dict[str, int] | None = None,
) -> int:
    """Units with at least one available night in the snapshot window."""
    if listings is not None:
        names = listings
    else:
        names = live_listings_in_scope(listing_live_dates, start_date, end_date)
    total = 0
    for name in names:
        if name not in listing_live_dates:
            continue
        if (
            listing_available_nights_in_window(
                listing_live_dates[name], start_date, end_date
            )
            <= 0
        ):
            continue
        total += (listing_inventory or {}).get(name, 1)
    return total


def total_available_room_nights(
    listing_live_dates: dict[str, date],
    start_date: date,
    end_date: date,
    *,
    listings: Iterable[str] | None = None,
    listing_inventory: dict[str, int] | None = None,
) -> int:
    keys = listings if listings is not None else listing_live_dates.keys()
    return sum(
        listing_available_nights_in_window(listing_live_dates[listing], start_date, end_date)
        * (listing_inventory or {}).get(listing, 1)
        for listing in keys
        if listing in listing_live_dates
    )


def count_active_listings(
    records: list[BookingRecord],
    start_date: date,
    end_date: date,
) -> int:
    """Listings whose booking history overlaps the snapshot window."""
    first_check_in: dict[str, date] = {}
    last_check_out: dict[str, date] = {}
    for record in records:
        if record.status in CANCELLED_STATUSES:
            continue
        listing = (record.listing_name or record.property_id).strip()
        if not listing:
            continue
        current_first = first_check_in.get(listing)
        if current_first is None or record.check_in_date < current_first:
            first_check_in[listing] = record.check_in_date
        current_last = last_check_out.get(listing)
        if current_last is None or record.check_out_date > current_last:
            last_check_out[listing] = record.check_out_date

    active = 0
    for listing, first in first_check_in.items():
        last = last_check_out[listing]
        if first <= end_date and last > start_date:
            active += 1
    return max(active, 1)


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
    available_room_nights: int | None = None,
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

    if available_room_nights is not None:
        available_room_nights_total = max(available_room_nights, 0)
    elif inventory_units is not None:
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

    pickup_bookings: dict[str, int] = defaultdict(int)
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
        pickup_bookings[label] += 1
        pickup_nights[label] += nights
        pickup_revenue[label] += revenue

        channel = (record.channel or "Unknown").strip() or "Unknown"
        channel_nights[channel] += nights
        channel_revenue[channel] += revenue

        los_dist[_los_bucket(nights)] += 1
        arrival_dow[_dow_label(record.check_in_date)] += 1

    # Shares for pickup bands
    total_bookings_dec = Decimal(len(filtered))
    total_nights_dec = Decimal(room_nights_sold)
    total_rev_dec = room_revenue
    pickup_bookings_share: dict[str, Decimal] = {}
    pickup_nights_share: dict[str, Decimal] = {}
    pickup_rev_share: dict[str, Decimal] = {}
    for band, bookings in pickup_bookings.items():
        pickup_bookings_share[band] = (
            _quantize_2(
                _safe_div(Decimal(bookings) * Decimal(100), total_bookings_dec) or Decimal(0)
            )
            or Decimal("0.00")
        )
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
        pickup_bookings_by_band=dict(pickup_bookings),
        pickup_room_nights_by_band=dict(pickup_nights),
        pickup_revenue_by_band={k: _quantize_2(v) or Decimal("0.00") for k, v in pickup_revenue.items()},
        pickup_bookings_share_by_band=pickup_bookings_share,
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
    available_room_nights: int | None = None,
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
        available_room_nights=available_room_nights,
        as_of_date=as_of_date,
    )
