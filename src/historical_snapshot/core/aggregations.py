"""Aggregation functions over NightDetail, BookingDetail, and CalendarDetail.

Each function is a pure computation that reads from the intermediate detail
tables and returns structured metrics. Adding a new metric = adding a new
function here — no changes to the data pipeline or Slack bot.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

from historical_snapshot.core.bands import Band, band_for_days
from historical_snapshot.core.metrics import (
    BookingDetail,
    CalendarDetail,
    NightDetail,
    SnapshotMetrics,
    _quantize_2,
    _safe_div,
    booking_window_days_for_record,
)


# ---------------------------------------------------------------------------
# ADR statistics helper
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ADRStats:
    mean: Decimal | None
    median: Decimal | None
    max: Decimal | None
    p25: Decimal | None
    p75: Decimal | None
    count: int


def _adr_stats_from_values(values: list[float]) -> ADRStats:
    if not values:
        return ADRStats(mean=None, median=None, max=None, p25=None, p75=None, count=0)
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    q = statistics.quantiles(sorted_vals, n=4) if n >= 2 else [sorted_vals[0]] * 3
    return ADRStats(
        mean=_quantize_2(Decimal(str(statistics.mean(sorted_vals)))),
        median=_quantize_2(Decimal(str(statistics.median(sorted_vals)))),
        max=_quantize_2(Decimal(str(max(sorted_vals)))),
        p25=_quantize_2(Decimal(str(q[0]))),
        p75=_quantize_2(Decimal(str(q[2] if len(q) > 2 else q[-1]))),
        count=n,
    )


# ---------------------------------------------------------------------------
# Day-type sub-metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DayTypeMetrics:
    day_type: str
    available_room_nights: int
    bookings_touching: int
    room_nights_sold: int
    room_revenue: Decimal
    adr_weighted: Decimal | None
    adr_stats: ADRStats
    occupancy_pct: Decimal | None
    revpar: Decimal | None


def aggregate_by_day_type(
    night_details: list[NightDetail],
    booking_details: list[BookingDetail],
    calendar_details: list[CalendarDetail],
) -> dict[str, DayTypeMetrics]:
    """Metrics broken out by day-type (Sun-Thu, Fri-Sat)."""
    night_rev: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    night_count: dict[str, int] = defaultdict(int)
    for nd in night_details:
        night_rev[nd.day_type] += nd.prorated_revenue
        night_count[nd.day_type] += 1

    avail: dict[str, int] = defaultdict(int)
    for cd in calendar_details:
        if cd.is_available:
            avail[cd.day_type] += 1

    booking_sets: dict[str, set[int]] = defaultdict(set)
    booking_adrs: dict[str, list[float]] = defaultdict(list)
    for i, bd in enumerate(booking_details):
        for dt in bd.day_types_touched:
            booking_sets[dt].add(i)
            if bd.booking_adr is not None:
                booking_adrs[dt].append(float(bd.booking_adr))

    result: dict[str, DayTypeMetrics] = {}
    for dt in ("Sun-Thu", "Fri-Sat"):
        sold = night_count.get(dt, 0)
        rev = night_rev.get(dt, Decimal("0"))
        av = avail.get(dt, 0)
        result[dt] = DayTypeMetrics(
            day_type=dt,
            available_room_nights=av,
            bookings_touching=len(booking_sets.get(dt, set())),
            room_nights_sold=sold,
            room_revenue=_quantize_2(rev) or Decimal("0.00"),
            adr_weighted=_quantize_2(_safe_div(rev, Decimal(sold))) if sold else None,
            adr_stats=_adr_stats_from_values(booking_adrs.get(dt, [])),
            occupancy_pct=_quantize_2(_safe_div(Decimal(sold) * Decimal(100), Decimal(av))) if av else None,
            revpar=_quantize_2(_safe_div(rev, Decimal(av))) if av else None,
        )
    return result


# ---------------------------------------------------------------------------
# ADR stats (global or grouped)
# ---------------------------------------------------------------------------

def aggregate_adr_stats(
    booking_details: list[BookingDetail],
    *,
    groupby: str | None = None,
) -> dict[str, ADRStats]:
    """ADR distribution stats, optionally grouped by 'channel', 'day_type', or 'band'.

    When groupby='day_type', a booking appears in every day-type it touches.
    Returns a dict keyed by group label (or '__all__' when ungrouped).
    """
    buckets: dict[str, list[float]] = defaultdict(list)
    for bd in booking_details:
        if bd.booking_adr is None:
            continue
        val = float(bd.booking_adr)
        if groupby is None:
            buckets["__all__"].append(val)
        elif groupby == "channel":
            buckets[bd.channel].append(val)
        elif groupby == "day_type":
            for dt in bd.day_types_touched:
                buckets[dt].append(val)
        elif groupby == "band":
            buckets[str(bd.booking_window_days)].append(val)
        else:
            buckets["__all__"].append(val)
    return {k: _adr_stats_from_values(v) for k, v in buckets.items()}


# ---------------------------------------------------------------------------
# Channel deep metrics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChannelMetrics:
    channel: str
    bookings_count: int
    room_nights: int
    room_revenue: Decimal
    adr_weighted: Decimal | None
    adr_stats: ADRStats
    revenue_share_pct: Decimal | None
    nights_share_pct: Decimal | None
    revpar_of_total_available: Decimal | None


def aggregate_by_channel(
    booking_details: list[BookingDetail],
    night_details: list[NightDetail],
    calendar_details: list[CalendarDetail],
) -> dict[str, ChannelMetrics]:
    """Enriched channel mix with ADR stats and occupancy share."""
    ch_bk: dict[str, int] = defaultdict(int)
    ch_nts: dict[str, int] = defaultdict(int)
    ch_rev: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    ch_adrs: dict[str, list[float]] = defaultdict(list)

    for bd in booking_details:
        ch_bk[bd.channel] += 1
        ch_nts[bd.channel] += bd.nights_in_range
        ch_rev[bd.channel] += bd.revenue_in_range
        if bd.booking_adr is not None:
            ch_adrs[bd.channel].append(float(bd.booking_adr))

    total_nts = sum(ch_nts.values())
    total_rev = sum(ch_rev.values(), Decimal("0"))
    total_avail = sum(1 for cd in calendar_details if cd.is_available)

    result: dict[str, ChannelMetrics] = {}
    for ch in sorted(ch_bk):
        nts = ch_nts[ch]
        rev = ch_rev[ch]
        result[ch] = ChannelMetrics(
            channel=ch,
            bookings_count=ch_bk[ch],
            room_nights=nts,
            room_revenue=_quantize_2(rev) or Decimal("0.00"),
            adr_weighted=_quantize_2(_safe_div(rev, Decimal(nts))) if nts else None,
            adr_stats=_adr_stats_from_values(ch_adrs.get(ch, [])),
            revenue_share_pct=_quantize_2(_safe_div(rev * Decimal(100), total_rev)) if total_rev else None,
            nights_share_pct=_quantize_2(_safe_div(Decimal(nts) * Decimal(100), Decimal(total_nts))) if total_nts else None,
            revpar_of_total_available=_quantize_2(_safe_div(rev, Decimal(total_avail))) if total_avail else None,
        )
    return result


# ---------------------------------------------------------------------------
# DOW grid
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DOWMetrics:
    day_of_week: str
    available_room_nights: int
    room_nights_sold: int
    room_revenue: Decimal
    adr_weighted: Decimal | None
    occupancy_pct: Decimal | None
    revpar: Decimal | None


def aggregate_by_dow(
    night_details: list[NightDetail],
    calendar_details: list[CalendarDetail],
) -> dict[str, DOWMetrics]:
    """Per-day-of-week metrics (Mon through Sun)."""
    from historical_snapshot.core.metrics import DOW_LABELS

    dow_sold: dict[str, int] = defaultdict(int)
    dow_rev: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for nd in night_details:
        dow_sold[nd.day_of_week] += 1
        dow_rev[nd.day_of_week] += nd.prorated_revenue

    dow_avail: dict[str, int] = defaultdict(int)
    for cd in calendar_details:
        if cd.is_available:
            dow_avail[cd.day_of_week] += 1

    result: dict[str, DOWMetrics] = {}
    for dow in DOW_LABELS:
        sold = dow_sold.get(dow, 0)
        rev = dow_rev.get(dow, Decimal("0"))
        av = dow_avail.get(dow, 0)
        result[dow] = DOWMetrics(
            day_of_week=dow,
            available_room_nights=av,
            room_nights_sold=sold,
            room_revenue=_quantize_2(rev) or Decimal("0.00"),
            adr_weighted=_quantize_2(_safe_div(rev, Decimal(sold))) if sold else None,
            occupancy_pct=_quantize_2(_safe_div(Decimal(sold) * Decimal(100), Decimal(av))) if av else None,
            revpar=_quantize_2(_safe_div(rev, Decimal(av))) if av else None,
        )
    return result


# ---------------------------------------------------------------------------
# Date-level heatmap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DateMetrics:
    night_date: date
    day_of_week: str
    day_type: str
    available_room_nights: int
    room_nights_sold: int
    room_revenue: Decimal
    occupancy_pct: Decimal | None


def aggregate_by_date(
    night_details: list[NightDetail],
    calendar_details: list[CalendarDetail],
) -> list[DateMetrics]:
    """Date-level occupancy and revenue heatmap."""
    sold_map: dict[date, int] = defaultdict(int)
    rev_map: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for nd in night_details:
        sold_map[nd.night_date] += 1
        rev_map[nd.night_date] += nd.prorated_revenue

    avail_map: dict[date, int] = defaultdict(int)
    dow_map: dict[date, str] = {}
    dt_map: dict[date, str] = {}
    for cd in calendar_details:
        if cd.is_available:
            avail_map[cd.night_date] += 1
        dow_map[cd.night_date] = cd.day_of_week
        dt_map[cd.night_date] = cd.day_type

    all_dates = sorted(set(list(sold_map.keys()) + list(avail_map.keys())))
    result: list[DateMetrics] = []
    for d in all_dates:
        sold = sold_map.get(d, 0)
        av = avail_map.get(d, 0)
        rev = rev_map.get(d, Decimal("0"))
        result.append(DateMetrics(
            night_date=d,
            day_of_week=dow_map.get(d, ""),
            day_type=dt_map.get(d, ""),
            available_room_nights=av,
            room_nights_sold=sold,
            room_revenue=_quantize_2(rev) or Decimal("0.00"),
            occupancy_pct=_quantize_2(_safe_div(Decimal(sold) * Decimal(100), Decimal(av))) if av else None,
        ))
    return result


# ---------------------------------------------------------------------------
# Period rollup (monthly/weekly)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PeriodMetrics:
    period_label: str
    start_date: date
    end_date: date
    available_room_nights: int
    bookings_count: int
    room_nights_sold: int
    room_revenue: Decimal
    adr_weighted: Decimal | None
    adr_stats: ADRStats
    occupancy_pct: Decimal | None
    revpar: Decimal | None


def _month_windows(start: date, end: date) -> list[tuple[str, date, date]]:
    windows: list[tuple[str, date, date]] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        ms = max(start, date(y, m, 1))
        if m == 12:
            me = min(end, date(y, 12, 31))
        else:
            me = min(end, date(y, m + 1, 1) - timedelta(days=1))
        if ms <= me:
            windows.append((f"{y}-{m:02d}", ms, me))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return windows


def _week_windows(start: date, end: date) -> list[tuple[str, date, date]]:
    windows: list[tuple[str, date, date]] = []
    d = start
    while d <= end:
        we = min(d + timedelta(days=6 - d.weekday()), end)
        windows.append((f"{d.isoformat()}", d, we))
        d = we + timedelta(days=1)
    return windows


def aggregate_by_period(
    night_details: list[NightDetail],
    booking_details: list[BookingDetail],
    calendar_details: list[CalendarDetail],
    *,
    period: str = "monthly",
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[PeriodMetrics]:
    """Roll up metrics by month or week."""
    if not calendar_details and not night_details:
        return []

    all_dates = set()
    for cd in calendar_details:
        all_dates.add(cd.night_date)
    for nd in night_details:
        all_dates.add(nd.night_date)
    s = start_date or min(all_dates)
    e = end_date or max(all_dates)

    if period == "weekly":
        windows = _week_windows(s, e)
    else:
        windows = _month_windows(s, e)

    result: list[PeriodMetrics] = []
    for label, ws, we in windows:
        p_nights = [nd for nd in night_details if ws <= nd.night_date <= we]
        p_calendar = [cd for cd in calendar_details if ws <= cd.night_date <= we]
        p_bookings_set: set[int] = set()
        p_booking_adrs: list[float] = []
        for i, bd in enumerate(booking_details):
            if bd.nights_in_range <= 0:
                continue
            overlap_s = max(bd.check_in_date, ws)
            overlap_e_excl = min(bd.check_out_date, we + timedelta(days=1))
            if (overlap_e_excl - overlap_s).days > 0:
                p_bookings_set.add(i)
                if bd.booking_adr is not None:
                    p_booking_adrs.append(float(bd.booking_adr))

        sold = len(p_nights)
        rev = sum((nd.prorated_revenue for nd in p_nights), Decimal("0"))
        av = sum(1 for cd in p_calendar if cd.is_available)

        result.append(PeriodMetrics(
            period_label=label,
            start_date=ws,
            end_date=we,
            available_room_nights=av,
            bookings_count=len(p_bookings_set),
            room_nights_sold=sold,
            room_revenue=_quantize_2(rev) or Decimal("0.00"),
            adr_weighted=_quantize_2(_safe_div(rev, Decimal(sold))) if sold else None,
            adr_stats=_adr_stats_from_values(p_booking_adrs),
            occupancy_pct=_quantize_2(_safe_div(Decimal(sold) * Decimal(100), Decimal(av))) if av else None,
            revpar=_quantize_2(_safe_div(rev, Decimal(av))) if av else None,
        ))
    return result


# ---------------------------------------------------------------------------
# Blended aggregate (backwards-compatible with today's SnapshotMetrics)
# ---------------------------------------------------------------------------

def aggregate_blended(
    booking_details: list[BookingDetail],
    night_details: list[NightDetail],
    calendar_details: list[CalendarDetail],
    *,
    property_id: str,
    property_name: str,
    start_date: date,
    end_date: date,
    bands: list[Band],
    available_room_nights: int | None = None,
    inventory_units: int | None = None,
    as_of_date: date | None = None,
) -> SnapshotMetrics:
    """Reproduce today's SnapshotMetrics from the intermediate tables.

    When available_room_nights or inventory_units is provided, those override
    the calendar-derived availability (matching the current behaviour where
    the service layer resolves inventory externally).
    """
    room_nights_sold = sum(bd.nights_in_range for bd in booking_details)
    room_revenue = sum((bd.revenue_in_range for bd in booking_details), Decimal("0"))

    if available_room_nights is not None:
        arn = max(available_room_nights, 0)
    elif inventory_units is not None:
        nights_in_window = (end_date - start_date).days + 1
        arn = max(nights_in_window, 0) * inventory_units
    else:
        arn = sum(1 for cd in calendar_details if cd.is_available)

    adr = _safe_div(room_revenue, Decimal(room_nights_sold))
    occupancy = _safe_div(Decimal(room_nights_sold), Decimal(arn)) if arn > 0 else None
    revpar = _safe_div(room_revenue, Decimal(arn)) if arn > 0 else None
    los = _safe_div(Decimal(room_nights_sold), Decimal(len(booking_details))) if booking_details else None

    pickup_bookings: dict[str, int] = defaultdict(int)
    pickup_nights: dict[str, int] = defaultdict(int)
    pickup_revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    channel_nights: dict[str, int] = defaultdict(int)
    channel_revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    los_dist: dict[str, int] = defaultdict(int)
    arrival_dow: dict[str, int] = defaultdict(int)
    booking_window_days_list: list[int] = []

    for bd in booking_details:
        label = band_for_days(bd.booking_window_days, bands)
        booking_window_days_list.append(bd.booking_window_days)
        pickup_bookings[label] += 1
        pickup_nights[label] += bd.nights_in_range
        pickup_revenue[label] += bd.revenue_in_range

        channel_nights[bd.channel] += bd.nights_in_range
        channel_revenue[bd.channel] += bd.revenue_in_range

        los_bucket = _los_bucket(bd.nights_in_range)
        los_dist[los_bucket] += 1

        from historical_snapshot.core.metrics import DOW_LABELS
        arrival_dow[DOW_LABELS[bd.check_in_date.weekday()]] += 1

    total_bookings_dec = Decimal(len(booking_details))
    total_nights_dec = Decimal(room_nights_sold)
    total_rev_dec = room_revenue

    pickup_bookings_share: dict[str, Decimal] = {}
    pickup_nights_share: dict[str, Decimal] = {}
    pickup_rev_share: dict[str, Decimal] = {}
    for band_label in pickup_bookings:
        pickup_bookings_share[band_label] = (
            _quantize_2(_safe_div(Decimal(pickup_bookings[band_label]) * Decimal(100), total_bookings_dec) or Decimal(0))
            or Decimal("0.00")
        )
    for band_label in pickup_nights:
        pickup_nights_share[band_label] = (
            _quantize_2(_safe_div(Decimal(pickup_nights[band_label]) * Decimal(100), total_nights_dec) or Decimal(0))
            or Decimal("0.00")
        )
        pickup_rev_share[band_label] = (
            _quantize_2(_safe_div(pickup_revenue[band_label] * Decimal(100), total_rev_dec) or Decimal(0))
            or Decimal("0.00")
        )

    channel_mix: dict[str, dict] = {}
    for channel, nights in channel_nights.items():
        rev = channel_revenue[channel]
        channel_mix[channel] = {
            "bookings_count": sum(1 for bd in booking_details if bd.channel == channel),
            "room_nights": nights,
            "revenue": _quantize_2(rev) or Decimal("0.00"),
            "adr": _quantize_2(_safe_div(rev, Decimal(nights)) if nights else None),
            "revenue_share_pct": _quantize_2(_safe_div(rev * Decimal(100), total_rev_dec) if total_rev_dec else None),
            "nights_share_pct": _quantize_2(_safe_div(Decimal(nights) * Decimal(100), total_nights_dec) if total_nights_dec else None),
        }

    bw_mean = _bw_mean(booking_window_days_list)
    bw_median = _bw_median(booking_window_days_list)

    global_adr_stats = _adr_stats_from_values(
        [float(bd.booking_adr) for bd in booking_details if bd.booking_adr is not None]
    )
    dt_metrics = aggregate_by_day_type(night_details, booking_details, calendar_details)
    ch_deep = aggregate_by_channel(booking_details, night_details, calendar_details)
    dow = aggregate_by_dow(night_details, calendar_details)

    return SnapshotMetrics(
        property_id=property_id,
        property_name=property_name,
        start_date=start_date,
        end_date=end_date,
        bookings_count=len(booking_details),
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
        booking_window_mean_days=_quantize_2(bw_mean),
        booking_window_median_days=_quantize_2(bw_median),
        los_distribution=dict(los_dist),
        arrival_day_of_week_mix=dict(arrival_dow),
        channel_mix=channel_mix,
        as_of_date=as_of_date,
        adr_median=global_adr_stats.median,
        adr_max=global_adr_stats.max,
        adr_p25=global_adr_stats.p25,
        adr_p75=global_adr_stats.p75,
        day_type_metrics=dt_metrics,
        channel_deep_metrics=ch_deep,
        dow_metrics=dow,
    )


# ---------------------------------------------------------------------------
# Private helpers (mirror metrics.py originals for parity)
# ---------------------------------------------------------------------------

def _los_bucket(nights: int) -> str:
    if nights <= 1:
        return "1"
    if nights == 2:
        return "2"
    if nights == 3:
        return "3"
    return "4+"


def _bw_mean(values: list[int]) -> Decimal | None:
    if not values:
        return None
    return Decimal(sum(values)) / Decimal(len(values))


def _bw_median(values: list[int]) -> Decimal | None:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return Decimal(vals[mid])
    return (Decimal(vals[mid - 1]) + Decimal(vals[mid])) / Decimal(2)
