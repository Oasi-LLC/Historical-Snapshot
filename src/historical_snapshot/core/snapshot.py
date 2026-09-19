from __future__ import annotations

from decimal import Decimal

from historical_snapshot.core.metrics import SnapshotMetrics


def _decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _adr_stats_to_dict(stats) -> dict:
    return {
        "mean": _decimal_to_float(stats.mean),
        "median": _decimal_to_float(stats.median),
        "max": _decimal_to_float(stats.max),
        "p25": _decimal_to_float(stats.p25),
        "p75": _decimal_to_float(stats.p75),
        "count": stats.count,
    }


def snapshot_to_dict(snapshot: SnapshotMetrics) -> dict:
    d = {
        "property_id": snapshot.property_id,
        "property_name": snapshot.property_name,
        "date_range": {
            "start": snapshot.start_date.isoformat(),
            "end": snapshot.end_date.isoformat(),
        },
        "as_of_date": snapshot.as_of_date.isoformat() if snapshot.as_of_date else None,
        "bookings_count": snapshot.bookings_count,
        "room_nights_sold": snapshot.room_nights_sold,
        "room_revenue": _decimal_to_float(snapshot.room_revenue),
        "adr": _decimal_to_float(snapshot.adr),
        "occupancy_pct": _decimal_to_float(snapshot.occupancy_pct),
        "revpar": _decimal_to_float(snapshot.revpar),
        "average_los": _decimal_to_float(snapshot.average_los),
        "pickup": {
            "bookings_by_band": snapshot.pickup_bookings_by_band,
            "room_nights_by_band": snapshot.pickup_room_nights_by_band,
            "revenue_by_band": {
                band: _decimal_to_float(value)
                for band, value in snapshot.pickup_revenue_by_band.items()
            },
            "bookings_share_pct_by_band": {
                band: _decimal_to_float(value)
                for band, value in snapshot.pickup_bookings_share_by_band.items()
            },
            "room_nights_share_pct_by_band": {
                band: _decimal_to_float(value)
                for band, value in snapshot.pickup_room_nights_share_by_band.items()
            },
            "revenue_share_pct_by_band": {
                band: _decimal_to_float(value)
                for band, value in snapshot.pickup_revenue_share_by_band.items()
            },
        },
        "booking_window": {
            "mean_days": _decimal_to_float(snapshot.booking_window_mean_days),
            "median_days": _decimal_to_float(snapshot.booking_window_median_days),
        },
        "los_distribution": snapshot.los_distribution,
        "arrival_day_of_week_mix": snapshot.arrival_day_of_week_mix,
        "channel_mix": {
            channel: {
                "bookings_count": metrics["bookings_count"],
                "room_nights": metrics["room_nights"],
                "revenue": _decimal_to_float(metrics["revenue"]) if metrics.get("revenue") is not None else None,
                "adr": _decimal_to_float(metrics["adr"]) if metrics.get("adr") is not None else None,
                "revenue_share_pct": _decimal_to_float(metrics["revenue_share_pct"]) if metrics.get("revenue_share_pct") is not None else None,
                "nights_share_pct": _decimal_to_float(metrics["nights_share_pct"]) if metrics.get("nights_share_pct") is not None else None,
            }
            for channel, metrics in snapshot.channel_mix.items()
        },
    }

    if snapshot.adr_median is not None:
        d["adr_median"] = _decimal_to_float(snapshot.adr_median)
        d["adr_max"] = _decimal_to_float(snapshot.adr_max)
        d["adr_p25"] = _decimal_to_float(snapshot.adr_p25)
        d["adr_p75"] = _decimal_to_float(snapshot.adr_p75)

    if snapshot.day_type_metrics is not None:
        d["day_type_metrics"] = {
            dt: {
                "available_room_nights": m.available_room_nights,
                "bookings_touching": m.bookings_touching,
                "room_nights_sold": m.room_nights_sold,
                "room_revenue": _decimal_to_float(m.room_revenue),
                "adr_weighted": _decimal_to_float(m.adr_weighted),
                "adr_stats": _adr_stats_to_dict(m.adr_stats),
                "occupancy_pct": _decimal_to_float(m.occupancy_pct),
                "revpar": _decimal_to_float(m.revpar),
            }
            for dt, m in snapshot.day_type_metrics.items()
        }

    if snapshot.channel_deep_metrics is not None:
        d["channel_deep_metrics"] = {
            ch: {
                "bookings_count": m.bookings_count,
                "room_nights": m.room_nights,
                "room_revenue": _decimal_to_float(m.room_revenue),
                "adr_weighted": _decimal_to_float(m.adr_weighted),
                "adr_stats": _adr_stats_to_dict(m.adr_stats),
                "revenue_share_pct": _decimal_to_float(m.revenue_share_pct),
                "nights_share_pct": _decimal_to_float(m.nights_share_pct),
                "revpar_of_total_available": _decimal_to_float(m.revpar_of_total_available),
            }
            for ch, m in snapshot.channel_deep_metrics.items()
        }

    if snapshot.dow_metrics is not None:
        d["dow_metrics"] = {
            dow: {
                "available_room_nights": m.available_room_nights,
                "room_nights_sold": m.room_nights_sold,
                "room_revenue": _decimal_to_float(m.room_revenue),
                "adr_weighted": _decimal_to_float(m.adr_weighted),
                "occupancy_pct": _decimal_to_float(m.occupancy_pct),
                "revpar": _decimal_to_float(m.revpar),
            }
            for dow, m in snapshot.dow_metrics.items()
        }

    return d


def snapshot_to_text(snapshot: SnapshotMetrics, invalid_row_count: int) -> str:
    def maybe(value: Decimal | None, suffix: str = "") -> str:
        return f"{value}{suffix}" if value is not None else "n/a"

    lines = [
        f"Property: {snapshot.property_name} ({snapshot.property_id})",
        f"Date range: {snapshot.start_date.isoformat()} -> {snapshot.end_date.isoformat()}",
        f"Bookings count: {snapshot.bookings_count}",
        f"Room nights sold: {snapshot.room_nights_sold}",
        f"Room revenue: {snapshot.room_revenue}",
        f"ADR: {maybe(snapshot.adr)}",
        f"Occupancy %: {maybe(snapshot.occupancy_pct, '%')}",
        f"RevPAR: {maybe(snapshot.revpar)}",
        f"Average LOS: {maybe(snapshot.average_los)}",
        "Pickup by booking window band:",
    ]
    for label, bookings in snapshot.pickup_bookings_by_band.items():
        revenue = snapshot.pickup_revenue_by_band.get(label, Decimal("0.00"))
        lines.append(f"  - {label}: bookings={bookings}, revenue={revenue}")

    if invalid_row_count:
        lines.append(f"Invalid rows skipped: {invalid_row_count}")
    return "\n".join(lines)


def multi_snapshot_to_text(
    portfolio: SnapshotMetrics,
    listings: list[SnapshotMetrics],
    invalid_row_count: int,
    breakdown_label: str = "Listing",
) -> str:
    lines = ["=== Portfolio Snapshot ===", snapshot_to_text(portfolio, invalid_row_count=0), ""]
    lines.append(f"=== {breakdown_label} Snapshots ===")
    for listing in listings:
        lines.append(
            f"- {listing.property_id}: bookings={listing.bookings_count}, nights={listing.room_nights_sold}, "
            f"revenue={listing.room_revenue}, adr={listing.adr}, occ%={listing.occupancy_pct}, revpar={listing.revpar}, los={listing.average_los}"
        )
    if invalid_row_count:
        lines.append("")
        lines.append(f"Invalid rows skipped: {invalid_row_count}")
    return "\n".join(lines)
