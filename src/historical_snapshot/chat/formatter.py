from __future__ import annotations

from datetime import date
from typing import Any

from historical_snapshot.chat.format_utils import (
    fmt_money,
    fmt_number,
    fmt_pct,
    format_date_label,
    format_short_period_label,
    pct_change,
    shift_one_year,
)
from historical_snapshot.chat.models import ChatSnapshotResult
from historical_snapshot.chat.slack_format import format_section_header
from historical_snapshot.core.bands import DEFAULT_BANDS

_BAND_ORDER = [band.strip() for band in DEFAULT_BANDS.split(",") if band.strip()]

CORE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("room_revenue", "Revenue", "money"),
    ("bookings_count", "Bookings", "int"),
    ("adr", "ADR", "money"),
    ("occupancy_pct", "Occupancy", "pct"),
    ("revpar", "RevPAR", "money"),
)


def _format_value(portfolio: dict | None, field: str, kind: str) -> str:
    if not portfolio:
        return "—"
    value = portfolio.get(field)
    if kind == "money":
        return fmt_money(value)
    if kind == "pct":
        return fmt_pct(value)
    if kind == "decimal":
        return fmt_number(value, decimals=1)
    return fmt_number(value)


def _los_near_one(portfolio: dict) -> bool:
    los = portfolio.get("average_los")
    if los is None:
        return True
    return abs(float(los) - 1.0) <= 0.05


def _metric_rows(current: dict) -> list[tuple[str, str, str]]:
    rows = list(CORE_METRICS)
    if not _los_near_one(current):
        rows.insert(2, ("room_nights_sold", "Room nights", "int"))
        rows.append(("average_los", "Avg LOS", "decimal"))
    return rows


def _format_band_label(band: str) -> str:
    if band.endswith("+"):
        return f"{band}d"
    return f"{band.replace('-', '–')}d"


def _performance_table(
    current: dict,
    *,
    prior_pace: dict | None,
    prior_final: dict | None,
    compare_prior_year: bool,
) -> list[str]:
    rows = _metric_rows(current)
    lines = ["```"]

    if compare_prior_year:
        lines.append(
            f"{'Metric':<12} {'Current':>10} {'LY Pace':>10} {'LY Final':>10} "
            f"{'vs Pace':>9} {'vs Final':>9}"
        )
        for field, label, kind in rows:
            cur = _format_value(current, field, kind)
            pace = _format_value(prior_pace, field, kind)
            final = _format_value(prior_final, field, kind)
            vs_pace = pct_change(current.get(field), (prior_pace or {}).get(field)) if prior_pace else "—"
            vs_final = pct_change(current.get(field), (prior_final or {}).get(field)) if prior_final else "—"
            lines.append(
                f"{label:<12} {cur:>10} {pace:>10} {final:>10} {vs_pace:>9} {vs_final:>9}"
            )
        # ADR median/max as additional rows inside the table block
        adr_med = current.get("adr_median")
        if adr_med is not None:
            pace_med = fmt_money((prior_pace or {}).get("adr_median")) if prior_pace else "—"
            final_med = fmt_money((prior_final or {}).get("adr_median")) if prior_final else "—"
            vs_p = pct_change(adr_med, (prior_pace or {}).get("adr_median")) if prior_pace else "—"
            vs_f = pct_change(adr_med, (prior_final or {}).get("adr_median")) if prior_final else "—"
            lines.append(
                f"{'ADR median':<12} {fmt_money(adr_med):>10} {pace_med:>10} {final_med:>10} {vs_p:>9} {vs_f:>9}"
            )
        adr_max = current.get("adr_max")
        if adr_max is not None:
            pace_max = fmt_money((prior_pace or {}).get("adr_max")) if prior_pace else "—"
            final_max = fmt_money((prior_final or {}).get("adr_max")) if prior_final else "—"
            vs_p = pct_change(adr_max, (prior_pace or {}).get("adr_max")) if prior_pace else "—"
            vs_f = pct_change(adr_max, (prior_final or {}).get("adr_max")) if prior_final else "—"
            lines.append(
                f"{'ADR max':<12} {fmt_money(adr_max):>10} {pace_max:>10} {final_max:>10} {vs_p:>9} {vs_f:>9}"
            )
    else:
        lines.append(f"{'Metric':<12} {'Current':>10}")
        for field, label, kind in rows:
            cur = _format_value(current, field, kind)
            lines.append(f"{label:<12} {cur:>10}")
        adr_med = current.get("adr_median")
        adr_max = current.get("adr_max")
        if adr_med is not None:
            lines.append(f"{'ADR median':<12} {fmt_money(adr_med):>10}")
        if adr_max is not None:
            lines.append(f"{'ADR max':<12} {fmt_money(adr_max):>10}")

    lines.append("```")
    return lines


def _day_type_table(portfolio: dict | None) -> list[str]:
    """Weekday/weekend breakdown table when day_type_metrics is present."""
    if not portfolio:
        return []
    dt_data = portfolio.get("day_type_metrics")
    if not dt_data:
        return []
    lines = ["```", format_section_header("Weekday / Weekend")]
    lines.append(
        f"{'':>8} {'Avail':>6} {'Sold':>6} {'Occ%':>6} "
        f"{'ADR(w)':>8} {'ADR(m)':>8} {'RevPAR':>8}"
    )
    for dt_name in ("Sun-Thu", "Fri-Sat"):
        m = dt_data.get(dt_name)
        if not m:
            continue
        adr_w = fmt_money(m.get("adr_weighted"))
        adr_stats = m.get("adr_stats") or {}
        adr_m = fmt_money(adr_stats.get("median"))
        lines.append(
            f"{dt_name:>8} "
            f"{fmt_number(m.get('available_room_nights')):>6} "
            f"{fmt_number(m.get('room_nights_sold')):>6} "
            f"{fmt_pct(m.get('occupancy_pct')):>6} "
            f"{adr_w:>8} {adr_m:>8} "
            f"{fmt_money(m.get('revpar')):>8}"
        )
    lines.append("```")
    return lines


def _booking_window_summary_table(
    *,
    prior_pace: dict | None,
    prior_final: dict | None,
) -> list[str]:
    rows: list[tuple[str, str, str]] = []
    for label, portfolio in (("LY pace", prior_pace), ("LY final", prior_final)):
        if not portfolio:
            continue
        bw = portfolio.get("booking_window") or {}
        mean = bw.get("mean_days")
        median = bw.get("median_days")
        if mean is None and median is None:
            continue
        rows.append(
            (
                label,
                f"{float(mean):.1f}d" if mean is not None else "—",
                f"{float(median):.1f}d" if median is not None else "—",
            )
        )
    if not rows:
        return []

    lines = ["```", format_section_header("Booking window (last year)")]
    lines.append(f"{'Period':<10} {'Mean':>8} {'Median':>8}")
    for label, mean, median in rows:
        lines.append(f"{label:<10} {mean:>8} {median:>8}")
    lines.append("```")
    return lines


def _revenue_by_window_table(
    portfolio: dict | None,
    *,
    title: str,
    limit: int | None = 4,
    order: str = "revenue",
) -> list[str]:
    if not portfolio:
        return []
    pickup = portfolio.get("pickup") or {}
    revenue_by_band = pickup.get("revenue_by_band") or {}
    if not revenue_by_band:
        return []

    if order == "lead_time":
        bands = [b for b in _BAND_ORDER if b in revenue_by_band]
        bands.extend(b for b in revenue_by_band if b not in bands)
    else:
        bands = sorted(revenue_by_band, key=lambda b: float(revenue_by_band[b] or 0), reverse=True)

    if limit is not None:
        bands = bands[:limit]

    lines = ["```", format_section_header(title)]
    lines.append(f"{'Window':<8} {'Revenue':>10} {'Share':>7}")
    for band in bands:
        revenue = revenue_by_band.get(band)
        share = (pickup.get("revenue_share_pct_by_band") or {}).get(band)
        share_label = f"{round(float(share))}%" if share is not None else "—"
        lines.append(f"{_format_band_label(band):<8} {fmt_money(revenue):>10} {share_label:>7}")
    lines.append("```")
    return lines


def _portfolio_sets(result: ChatSnapshotResult) -> dict[str, dict | None]:
    if result.query.listing_name:
        return {
            "current": result.listing_pace_current,
            "pace_prior": result.listing_pace_prior,
            "prior_final": result.listing_prior_final,
        }
    return {
        "current": (result.pace_current or {}).get("portfolio_snapshot"),
        "pace_prior": (result.pace_prior or {}).get("portfolio_snapshot") if result.pace_prior else None,
        "prior_final": (result.prior_final or {}).get("portfolio_snapshot") if result.prior_final else None,
    }


def _period_label(result: ChatSnapshotResult) -> str:
    query = result.query
    period = format_short_period_label(query.start_date, query.end_date)
    if query.compare_prior_year and query.prior_stay_start_date and query.prior_stay_end_date:
        cal_start = shift_one_year(query.start_date)
        cal_end = shift_one_year(query.end_date)
        if (query.prior_stay_start_date, query.prior_stay_end_date) != (cal_start, cal_end):
            ly_period = format_short_period_label(
                query.prior_stay_start_date,
                query.prior_stay_end_date,
            )
            period = (
                f"{period} {query.start_date.year} vs "
                f"{ly_period} {query.prior_stay_start_date.year} LY"
            )
    return period


def _title_label(result: ChatSnapshotResult) -> str:
    query = result.query
    if query.listing_name:
        return f"{query.listing_name} — {query.property_name}"
    return query.property_name


def _report_header_lines(result: ChatSnapshotResult, *, subtitle: str | None = None) -> list[str]:
    as_of = result.pace_as_of_current
    as_of_label = format_date_label(as_of) if as_of else "today"
    lines = [
        f"*{_title_label(result)} — {_period_label(result)}*",
        f"Snapshot as of {as_of_label}",
    ]
    if subtitle:
        lines.append(subtitle)
    lines.append("")
    return lines


def format_reply(result: ChatSnapshotResult) -> str:
    query = result.query
    period = _period_label(result)
    title = _title_label(result)

    portfolios = _portfolio_sets(result)
    current = portfolios["current"]
    prior_pace = portfolios["pace_prior"] if query.compare_prior_year else None
    prior_final = portfolios["prior_final"] if query.compare_prior_year else None

    # Listing drill-downs can have LY rows with no current pace row yet — synthesize an
    # empty current so the LY comparison table still renders.
    if not current and query.compare_prior_year and (prior_pace or prior_final):
        has_prior = any(
            portfolio and (portfolio.get("bookings_count") or portfolio.get("room_revenue"))
            for portfolio in (prior_pace, prior_final)
        )
        if has_prior:
            current = {
                "room_revenue": 0,
                "bookings_count": 0,
                "adr": 0,
                "occupancy_pct": 0,
                "revpar": 0,
                "average_los": 1.0,
            }

    if not current:
        return f"No bookings found for *{title}* during {period}."

    zero_rev = current.get("room_revenue") in (0, 0.0, None)
    zero_bk = current.get("bookings_count") == 0
    if zero_rev and zero_bk:
        has_prior_data = False
        if query.compare_prior_year:
            for portfolio in (prior_pace, prior_final):
                if not portfolio:
                    continue
                if portfolio.get("bookings_count") or portfolio.get("room_revenue"):
                    has_prior_data = True
                    break
        if not has_prior_data:
            note = f"No bookings found for *{title}* during {period}."
            if query.inventory_mode == "live_listings" and not result.comparable_listings:
                note += (
                    "\n_No units were live in both the selected period and prior-year compare window._"
                )
            return note

    lines = _report_header_lines(result)
    if zero_rev and zero_bk:
        lines.insert(2, "_No bookings on the books yet for this window._")

    lines.extend(
        _performance_table(
            current,
            prior_pace=prior_pace,
            prior_final=prior_final,
            compare_prior_year=query.compare_prior_year,
        )
    )
    lines.append("")

    dt_table = _day_type_table(current)
    if dt_table:
        lines.extend(dt_table)
        lines.append("")

    bullets = _booking_window_summary_table(prior_pace=prior_pace, prior_final=prior_final)
    if bullets:
        lines.extend(bullets)
        lines.append("")

    if query.compare_prior_year and prior_final:
        ly_final_pickup = _revenue_by_window_table(
            prior_final,
            title="Revenue by booking window (LY final)",
            limit=None,
            order="lead_time",
        )
        if ly_final_pickup:
            lines.extend(ly_final_pickup)
            lines.append("")

    if result.comparable_listings:
        lines.append(f"_Based on {len(result.comparable_listings)} comparable units._")

    return "\n".join(line for line in lines if line is not None).strip()


def _short_listing_name(name: str, *, width: int = 28) -> str:
    """Return a display name for a listing, truncated to `width` characters.

    For names formatted as "NNN Section: Unit Name" (e.g. LaFave's
    "100 LaFave South: Temple of Sinawava"), strip the prefix and show
    only the unit name part so the table stays readable.
    """
    import re as _re
    text = (name or "").strip() or "Unknown"
    # Strip leading "NNN Section: " prefix (e.g. "100 LaFave South: ")
    stripped = _re.sub(r"^\d+\s+[^:]+:\s*", "", text).strip()
    if stripped and stripped != text:
        text = stripped
    # Strip leading "Word(s): " prefix when the remainder is non-empty
    # (e.g. "Great Lodge: King Room" -> "King Room")
    elif _re.match(r"^[A-Za-z][^:]+:\s+\S", text):
        candidate = _re.sub(r"^[^:]+:\s+", "", text).strip()
        if candidate:
            text = candidate
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _top_listings_table(
    rows: list[dict],
    *,
    title: str,
    limit: int = 5,
) -> list[str]:
    if not rows:
        return []
    ranked = sorted(rows, key=lambda row: float(row.get("room_revenue") or 0), reverse=True)[:limit]
    lines = ["```", format_section_header(title)]
    lines.append(f"{'Listing':<28} {'Rev':>8} {'Bkgs':>4} {'Nights':>6} {'ADR':>7} {'Occ':>5}")
    for row in ranked:
        listing = _short_listing_name(str(row.get("listing") or row.get("property_id") or ""))
        lines.append(
            f"{listing:<28} "
            f"{fmt_money(row.get('room_revenue')):>8} "
            f"{fmt_number(row.get('bookings_count')):>4} "
            f"{fmt_number(row.get('room_nights_sold')):>6} "
            f"{fmt_money(row.get('adr')):>7} "
            f"{fmt_pct(row.get('occupancy_pct')):>5}"
        )
    lines.append("```")
    return lines


def _channel_mix_table(
    channel_mix: dict[str, Any],
    *,
    title: str,
    limit: int = 8,
) -> list[str]:
    if not channel_mix:
        return []
    ranked = sorted(
        channel_mix.items(),
        key=lambda item: float((item[1] or {}).get("revenue") or 0),
        reverse=True,
    )[:limit]
    lines = ["```", format_section_header(title)]
    has_deep = any(
        (item[1] or {}).get("adr_stats") for item in ranked
    )
    if has_deep:
        lines.append(f"{'Channel':<12} {'Rev':>9} {'Share':>7} {'Bkgs':>5} {'Nights':>6} {'ADR(w)':>8} {'ADR(m)':>8}")
    else:
        lines.append(f"{'Channel':<12} {'Rev':>9} {'Share':>7} {'Bkgs':>5} {'Nights':>6} {'ADR':>7}")
    for channel, stats in ranked:
        stats = stats or {}
        share = stats.get("revenue_share_pct")
        share_label = f"{round(float(share))}%" if share is not None else "—"
        label = channel[:12]
        adr_w = fmt_money(stats.get("adr_weighted") or stats.get("adr"))
        if has_deep:
            adr_stats = stats.get("adr_stats") or {}
            adr_m = fmt_money(adr_stats.get("median"))
            lines.append(
                f"{label:<12} "
                f"{fmt_money(stats.get('revenue') or stats.get('room_revenue')):>9} "
                f"{share_label:>7} "
                f"{fmt_number(stats.get('bookings_count')):>5} "
                f"{fmt_number(stats.get('room_nights')):>6} "
                f"{adr_w:>8} {adr_m:>8}"
            )
        else:
            lines.append(
                f"{label:<12} "
                f"{fmt_money(stats.get('revenue')):>9} "
                f"{share_label:>7} "
                f"{fmt_number(stats.get('bookings_count')):>5} "
                f"{fmt_number(stats.get('room_nights')):>6} "
                f"{fmt_money(stats.get('adr')):>7}"
            )
    lines.append("```")
    return lines


def append_listing_breakdown_tables(
    report: str,
    *,
    current_rows: list[dict],
    ly_final_rows: list[dict] | None = None,
    limit: int = 5,
) -> str:
    sections: list[str] = [report.strip()] if report.strip() else []
    current_table = _top_listings_table(
        current_rows,
        title="Top listings (current pace)",
        limit=limit,
    )
    if current_table:
        sections.append("\n".join(current_table))
    if ly_final_rows:
        ly_table = _top_listings_table(
            ly_final_rows,
            title="Top listings (LY final)",
            limit=limit,
        )
        if ly_table:
            sections.append("\n".join(ly_table))
    return "\n\n".join(sections).strip()


def append_channel_breakdown_tables(
    report: str,
    *,
    current_mix: dict[str, Any],
    ly_pace_mix: dict[str, Any] | None = None,
    ly_final_mix: dict[str, Any] | None = None,
) -> str:
    sections: list[str] = [report.strip()] if report.strip() else []
    current_table = _channel_mix_table(current_mix, title="Channel mix (current pace)")
    if current_table:
        sections.append("\n".join(current_table))
    if ly_pace_mix:
        pace_table = _channel_mix_table(ly_pace_mix, title="Channel mix (LY pace)")
        if pace_table:
            sections.append("\n".join(pace_table))
    if ly_final_mix:
        final_table = _channel_mix_table(ly_final_mix, title="Channel mix (LY final)")
        if final_table:
            sections.append("\n".join(final_table))
    return "\n\n".join(sections).strip()


def format_delta_listings_report(
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
) -> str:
    listing_payload = metrics.get("listing_breakdown") or {}
    header = _report_header_lines(result, subtitle="_Listing breakdown for this stay window._")
    body = append_listing_breakdown_tables(
        "",
        current_rows=list(listing_payload.get("current") or []),
        ly_final_rows=list(listing_payload.get("ly_final") or []),
    )
    return "\n".join(header).strip() + ("\n\n" + body if body else "")


def format_delta_channels_report(
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
) -> str:
    channel_payload = metrics.get("channel_breakdown") or {}
    header = _report_header_lines(result, subtitle="_Channel mix for this stay window._")
    body = append_channel_breakdown_tables(
        "",
        current_mix=dict(channel_payload.get("current") or {}),
        ly_pace_mix=dict(channel_payload.get("ly_pace") or {}),
        ly_final_mix=dict(channel_payload.get("ly_final") or {}),
    )
    return "\n".join(header).strip() + ("\n\n" + body if body else "")


def _listing_row_by_name(rows: list[dict], listing_name: str) -> dict | None:
    from historical_snapshot.chat.parser_rules import _normalize_listing_key

    target = _normalize_listing_key(listing_name)
    for row in rows:
        bucket = _normalize_listing_key(str(row.get("listing") or row.get("property_id") or ""))
        if bucket == target:
            return row
    return None


def _compare_listings_table(
    rows_by_listing: list[tuple[str, dict | None]],
    *,
    title: str,
) -> list[str]:
    lines = ["```", format_section_header(title)]
    lines.append(f"{'Listing':<28} {'Rev':>9} {'Bkgs':>5} {'Nights':>6} {'ADR':>7} {'Occ':>5}")
    for name, row in rows_by_listing:
        label = _short_listing_name(name, width=28)
        if not row:
            lines.append(f"{label:<28} {'—':>9} {'—':>5} {'—':>6} {'—':>7} {'—':>5}")
            continue
        lines.append(
            f"{label:<28} "
            f"{fmt_money(row.get('room_revenue')):>9} "
            f"{fmt_number(row.get('bookings_count')):>5} "
            f"{fmt_number(row.get('room_nights_sold')):>6} "
            f"{fmt_money(row.get('adr')):>7} "
            f"{fmt_pct(row.get('occupancy_pct')):>5}"
        )
    lines.append("```")
    return lines


def _format_month_label(period: str) -> str:
    """"YYYY-MM" -> "Jan 2026". Falls back to the raw label if unparseable."""
    try:
        year, month = period.split("-")
        return date(int(year), int(month), 1).strftime("%b %Y")
    except (ValueError, IndexError):
        return period


def _ramp_series_table(ramp_series: dict[str, list[dict]], names: list[str]) -> list[str]:
    """Monthly ramp since go-live, one Rev/Occ column pair per listing.

    Rows are aligned by calendar month (via each row's "period" key) even
    though the two listings may have different go-live dates - a listing not
    yet live in a given month just shows "-" instead of a misleading $0.
    """
    present = [name for name in names if ramp_series.get(name)]
    if not present:
        return []

    by_period: dict[str, dict[str, dict]] = {}
    for name in present:
        for row in ramp_series[name]:
            by_period.setdefault(row.get("period"), {})[name] = row
    periods = sorted(by_period.keys())
    if not periods:
        return []

    lines = ["```", format_section_header("Monthly ramp since go-live")]
    header = f"{'Month':<10}"
    for name in present:
        label = _short_listing_name(name, width=14)
        header += f" {label + ' Rev':>18} {label + ' Occ':>12}"
    lines.append(header)
    for period in periods:
        row_line = f"{_format_month_label(period):<10}"
        for name in present:
            row = by_period[period].get(name)
            rev = fmt_money(row.get("room_revenue")) if row else "—"
            occ = fmt_pct(row.get("occupancy_pct")) if row else "—"
            row_line += f" {rev:>18} {occ:>12}"
        lines.append(row_line)
    lines.append("```")
    return lines


def format_listing_compare_report(
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
) -> str:
    compare = metrics.get("listing_compare") or {}
    names = [str(name) for name in (compare.get("names") or []) if str(name).strip()]
    focus = str(compare.get("focus") or "ly_final")
    listing_payload = metrics.get("listing_breakdown") or {}
    if not names:
        return _report_header_lines(result, subtitle="_Listing comparison._")[0]

    sections: list[str] = [
        "\n".join(
            _report_header_lines(
                result,
                subtitle="_Listing comparison for this stay window._",
            )
        ).strip()
    ]

    def rows_for(bucket: str) -> list[tuple[str, dict | None]]:
        source = list(listing_payload.get(bucket) or [])
        return [(name, _listing_row_by_name(source, name)) for name in names]

    if focus in {"ly_final", "both"}:
        sections.append(
            "\n".join(
                _compare_listings_table(
                    rows_for("ly_final"),
                    title="LY final",
                )
            )
        )
    if focus in {"current", "both"}:
        sections.append(
            "\n".join(
                _compare_listings_table(
                    rows_for("current"),
                    title="Current pace",
                )
            )
        )
    if focus == "ly_final":
        # Keep a one-line current context without repeating the full portfolio format.
        current_bits = []
        for name, row in rows_for("current"):
            if row and (row.get("bookings_count") or row.get("room_revenue")):
                current_bits.append(
                    f"{_short_listing_name(name, width=40)}: "
                    f"{fmt_money(row.get('room_revenue'))} / "
                    f"{fmt_number(row.get('bookings_count'))} bkgs on books"
                )
            else:
                current_bits.append(f"{_short_listing_name(name, width=40)}: $0 on books")
        if current_bits:
            sections.append("_Current pace:_ " + "; ".join(current_bits))

    ramp_series = metrics.get("ramp_series") or {}
    if ramp_series:
        ramp_lines = _ramp_series_table(ramp_series, names)
        if ramp_lines:
            sections.append("\n".join(ramp_lines))

    return "\n\n".join(section for section in sections if section).strip()


def build_formatted_report(
    result: ChatSnapshotResult,
    metrics: dict[str, Any],
    *,
    reply_mode: str = "full",
) -> str:
    if reply_mode == "interpretation_only":
        return ""
    if reply_mode == "delta_listings":
        return format_delta_listings_report(result, metrics)
    if reply_mode == "delta_channels":
        return format_delta_channels_report(result, metrics)
    if reply_mode == "listing_compare":
        return format_listing_compare_report(result, metrics)
    if reply_mode == "delta_listing":
        return format_reply(result)

    report = format_reply(result)
    if result.query.listing_name:
        return report

    listing_payload = metrics.get("listing_breakdown") or {}
    if listing_payload.get("current"):
        report = append_listing_breakdown_tables(
            report,
            current_rows=list(listing_payload.get("current") or []),
            ly_final_rows=list(listing_payload.get("ly_final") or []),
        )
    return report
