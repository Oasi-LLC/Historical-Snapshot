"""Edge-case tests for the intermediate detail layers and aggregation functions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from historical_snapshot.core.metrics import (
    BookingDetail,
    CalendarDetail,
    NightDetail,
    build_booking_details,
    build_calendar_details,
    build_night_details,
    classify_day_type,
    prorate_revenue,
)
from historical_snapshot.core.aggregations import (
    aggregate_adr_stats,
    aggregate_blended,
    aggregate_by_channel,
    aggregate_by_day_type,
    aggregate_by_dow,
    aggregate_by_date,
    aggregate_by_period,
)
from historical_snapshot.core.bands import parse_bands, DEFAULT_BANDS
from historical_snapshot.models import BookingRecord

BANDS = parse_bands(DEFAULT_BANDS)


def _booking(
    check_in: str,
    check_out: str,
    revenue: float,
    nights: int | None = None,
    channel: str = "Direct",
    listing: str = "Unit A",
    reservation_date: str | None = None,
    status: str = "confirmed",
) -> BookingRecord:
    ci = date.fromisoformat(check_in)
    co = date.fromisoformat(check_out)
    n = nights if nights is not None else (co - ci).days
    rd = date.fromisoformat(reservation_date) if reservation_date else ci
    return BookingRecord(
        property_id=listing,
        property_name="Test",
        listing_name=listing,
        channel=channel,
        grouping="",
        reservation_date=rd,
        booking_date=rd,
        check_in_date=ci,
        check_out_date=co,
        room_revenue=Decimal(str(revenue)),
        room_nights=n,
        status=status,
    )


class TestClassifyDayType:
    def test_friday(self):
        assert classify_day_type(date(2026, 8, 14)) == "Fri-Sat"  # Friday

    def test_saturday(self):
        assert classify_day_type(date(2026, 8, 15)) == "Fri-Sat"  # Saturday

    def test_sunday(self):
        assert classify_day_type(date(2026, 8, 16)) == "Sun-Thu"  # Sunday

    def test_thursday(self):
        assert classify_day_type(date(2026, 8, 13)) == "Sun-Thu"  # Thursday


class TestProrateRevenue:
    def test_even_split(self):
        assert prorate_revenue(Decimal("300"), 3) == Decimal("100")

    def test_zero_nights(self):
        assert prorate_revenue(Decimal("500"), 0) == Decimal("0")


class TestBuildNightDetails:
    def test_booking_spanning_window_boundary(self):
        """Booking starts before window — only nights in window should appear."""
        b = _booking("2026-08-10", "2026-08-15", 500, nights=5)
        nd = build_night_details([b], date(2026, 8, 13), date(2026, 8, 15))
        assert len(nd) == 2  # Aug 13, 14 (Aug 15 is checkout, not a night)

    def test_booking_ending_after_window(self):
        """Booking ends after window — only nights up to end_date."""
        b = _booking("2026-08-13", "2026-08-20", 700, nights=7)
        nd = build_night_details([b], date(2026, 8, 13), date(2026, 8, 15))
        assert len(nd) == 3  # Aug 13, 14, 15

    def test_cancelled_booking_excluded(self):
        b = _booking("2026-08-13", "2026-08-15", 200, status="cancelled")
        nd = build_night_details([b], date(2026, 8, 13), date(2026, 8, 15))
        assert len(nd) == 0

    def test_as_of_filter(self):
        b = _booking("2026-10-01", "2026-10-03", 400, reservation_date="2026-09-01")
        nd = build_night_details([b], date(2026, 10, 1), date(2026, 10, 3), as_of_date=date(2026, 8, 15))
        assert len(nd) == 0  # reserved after as_of

    def test_day_type_boundary(self):
        """Thu-Sat booking should produce both day types."""
        b = _booking("2026-08-13", "2026-08-16", 300, nights=3)  # Thu, Fri, Sat
        nd = build_night_details([b], date(2026, 8, 13), date(2026, 8, 15))
        day_types = {n.day_type for n in nd}
        assert day_types == {"Sun-Thu", "Fri-Sat"}


class TestBuildBookingDetails:
    def test_day_types_touched_cross_boundary(self):
        """Thu-Sun stay touches both day types."""
        b = _booking("2026-08-13", "2026-08-17", 400, nights=4)
        bd = build_booking_details([b], date(2026, 8, 13), date(2026, 8, 16))
        assert len(bd) == 1
        assert bd[0].day_types_touched == frozenset({"Sun-Thu", "Fri-Sat"})

    def test_zero_revenue_booking(self):
        b = _booking("2026-08-13", "2026-08-15", 0, nights=2)
        bd = build_booking_details([b], date(2026, 8, 13), date(2026, 8, 15))
        assert len(bd) == 1
        assert bd[0].booking_adr == Decimal("0.00")

    def test_booking_adr_computed_from_in_range(self):
        b = _booking("2026-08-10", "2026-08-14", 400, nights=4)
        bd = build_booking_details([b], date(2026, 8, 12), date(2026, 8, 14))
        assert bd[0].nights_in_range == 2
        assert bd[0].revenue_in_range == Decimal("200")
        assert bd[0].booking_adr == Decimal("100.00")


class TestBuildCalendarDetails:
    def test_joins_from_night_details(self):
        """CalendarDetail.is_sold should be derived from NightDetail, not independent."""
        b = _booking("2026-08-14", "2026-08-16", 200, nights=2)
        nd = build_night_details([b], date(2026, 8, 14), date(2026, 8, 15))
        live_dates = {"Unit A": date(2026, 1, 1)}
        cd = build_calendar_details(nd, date(2026, 8, 14), date(2026, 8, 15), listing_live_dates=live_dates)
        sold = [c for c in cd if c.is_sold]
        assert len(sold) == 2  # Aug 14, 15

    def test_listing_not_yet_live(self):
        nd: list[NightDetail] = []
        live_dates = {"Unit A": date(2026, 9, 1)}
        cd = build_calendar_details(nd, date(2026, 8, 14), date(2026, 8, 15), listing_live_dates=live_dates)
        assert all(not c.is_available for c in cd)

    def test_no_listings_no_crash(self):
        cd = build_calendar_details([], date(2026, 8, 14), date(2026, 8, 15))
        assert cd == []


class TestAggregateByDayType:
    def test_split_weekday_weekend(self):
        records = [
            _booking("2026-08-13", "2026-08-16", 300, nights=3),  # Thu, Fri, Sat
        ]
        nd = build_night_details(records, date(2026, 8, 13), date(2026, 8, 15))
        bd = build_booking_details(records, date(2026, 8, 13), date(2026, 8, 15))
        live = {"Unit A": date(2026, 1, 1)}
        cd = build_calendar_details(nd, date(2026, 8, 13), date(2026, 8, 15), listing_live_dates=live)
        dt = aggregate_by_day_type(nd, bd, cd)
        assert dt["Sun-Thu"].room_nights_sold == 1  # Thu
        assert dt["Fri-Sat"].room_nights_sold == 2  # Fri, Sat


class TestAggregateADRStats:
    def test_global_stats(self):
        records = [
            _booking("2026-08-13", "2026-08-14", 100, nights=1),
            _booking("2026-08-14", "2026-08-15", 200, nights=1),
            _booking("2026-08-15", "2026-08-16", 300, nights=1),
        ]
        bd = build_booking_details(records, date(2026, 8, 13), date(2026, 8, 15))
        stats = aggregate_adr_stats(bd)
        assert stats["__all__"].median == Decimal("200.00")
        assert stats["__all__"].count == 3

    def test_grouped_by_channel(self):
        records = [
            _booking("2026-08-13", "2026-08-14", 100, channel="Direct"),
            _booking("2026-08-14", "2026-08-15", 200, channel="Airbnb"),
        ]
        bd = build_booking_details(records, date(2026, 8, 13), date(2026, 8, 15))
        stats = aggregate_adr_stats(bd, groupby="channel")
        assert "Direct" in stats
        assert "Airbnb" in stats


class TestAggregateByPeriod:
    def test_monthly_rollup(self):
        records = [
            _booking("2026-07-15", "2026-07-17", 200, nights=2),
            _booking("2026-08-10", "2026-08-12", 300, nights=2),
        ]
        nd = build_night_details(records, date(2026, 7, 1), date(2026, 8, 31))
        bd = build_booking_details(records, date(2026, 7, 1), date(2026, 8, 31))
        live = {"Unit A": date(2026, 1, 1)}
        cd = build_calendar_details(nd, date(2026, 7, 1), date(2026, 8, 31), listing_live_dates=live)
        periods = aggregate_by_period(nd, bd, cd, period="monthly", start_date=date(2026, 7, 1), end_date=date(2026, 8, 31))
        assert len(periods) == 2
        assert periods[0].period_label == "2026-07"
        assert periods[1].period_label == "2026-08"
        assert periods[0].room_nights_sold == 2
        assert periods[1].room_nights_sold == 2


class TestAggregateBlended:
    def test_empty_data(self):
        snap = aggregate_blended(
            [], [], [],
            property_id="X", property_name="X",
            start_date=date(2026, 8, 1), end_date=date(2026, 8, 31),
            bands=BANDS,
        )
        assert snap.bookings_count == 0
        assert snap.room_revenue == Decimal("0.00")

    def test_enriched_fields_populated(self):
        records = [
            _booking("2026-08-13", "2026-08-15", 200, nights=2),
            _booking("2026-08-14", "2026-08-16", 400, nights=2),
        ]
        nd = build_night_details(records, date(2026, 8, 13), date(2026, 8, 16))
        bd = build_booking_details(records, date(2026, 8, 13), date(2026, 8, 16))
        live = {"Unit A": date(2026, 1, 1)}
        cd = build_calendar_details(nd, date(2026, 8, 13), date(2026, 8, 16), listing_live_dates=live)
        snap = aggregate_blended(
            bd, nd, cd,
            property_id="Unit A", property_name="Test",
            start_date=date(2026, 8, 13), end_date=date(2026, 8, 16),
            bands=BANDS,
        )
        assert snap.adr_median is not None
        assert snap.day_type_metrics is not None
        assert snap.channel_deep_metrics is not None
        assert snap.dow_metrics is not None
