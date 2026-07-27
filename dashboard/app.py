from __future__ import annotations

import sys
from pathlib import Path

# Allow running via `streamlit run` without `pip install -e .`
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    _src_str = str(_SRC)
    if _src_str in sys.path:
        sys.path.remove(_src_str)
    sys.path.insert(0, _src_str)
    for _name in list(sys.modules):
        if _name == "historical_snapshot" or _name.startswith("historical_snapshot."):
            del sys.modules[_name]

import html
import os
from datetime import date

import pandas as pd
import requests
import streamlit as st

import historical_snapshot.core.bands as _bands_mod
from historical_snapshot.core.bands import parse_bands, sort_band_labels
from historical_snapshot.config import parse_config_date
from historical_snapshot.core.metrics import count_live_listings_in_scope

DEFAULT_BANDS = getattr(
    _bands_mod,
    "DEFAULT_BANDS",
    "0,1-3,4-7,8-15,16-30,31-60,61+",
)

API_URL = os.environ.get("SNAPSHOT_API_URL", "http://127.0.0.1:8000")

st.set_page_config(page_title="Historical Snapshot", layout="wide")
st.title("Historical Snapshot Dashboard")
st.caption("Holiday and event performance using stay-overlap date filtering with prorated revenue.")

if "api_url" not in st.session_state:
    st.session_state.api_url = API_URL
if "data_root" not in st.session_state:
    st.session_state.data_root = "data"


@st.cache_data(ttl=60)
def fetch_properties(api_url: str, data_root: str) -> list[dict]:
    response = requests.get(f"{api_url}/properties", params={"data_root": data_root}, timeout=30)
    response.raise_for_status()
    return response.json()["properties"]


@st.cache_data(ttl=30)
def fetch_snapshot(api_url: str, params: dict) -> dict:
    response = requests.get(f"{api_url}/snapshot", params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def sync_google_sheets(api_url: str, data_root: str, property_folder: str | None = None) -> dict:
    params: dict[str, str] = {"data_root": data_root}
    if property_folder:
        params["property_folder"] = property_folder
    response = requests.post(f"{api_url}/sync", params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def fmt_money(value: float | None) -> str:
    if _is_missing(value):
        return "n/a"
    return f"${round(float(value)):,}"


def fmt_pct(value: float | None) -> str:
    if _is_missing(value):
        return "n/a"
    return f"{round(float(value))}%"


def fmt_number(value: float | None, decimals: int = 0) -> str:
    if _is_missing(value):
        return "n/a"
    if decimals == 0:
        return f"{round(value):,}"
    return f"{value:,.{decimals}f}"


def delta_style(value: float | int | None) -> str:
    if _is_missing(value):
        return ""
    v = float(value)
    if v > 0:
        return "background-color: #14532d; color: #dcfce7;"
    if v < 0:
        return "background-color: #7f1d1d; color: #fee2e2;"
    return "background-color: #1f2937; color: #e5e7eb;"


def style_delta_columns(
    row: pd.Series,
    *,
    delta_raw_col: str,
    delta_pct_raw_col: str,
    delta_col: str = "Delta",
    delta_pct_col: str = "Delta %",
) -> list[str]:
    styles = [""] * len(row.index)
    idx_map = {col: i for i, col in enumerate(row.index)}
    if delta_col in idx_map:
        styles[idx_map[delta_col]] = delta_style(row.get(delta_raw_col))
    if delta_pct_col in idx_map:
        styles[idx_map[delta_pct_col]] = delta_style(row.get(delta_pct_raw_col))
    return styles


def dashboard_inventory_count(
    selected: dict,
    *,
    start_date: date,
    end_date: date,
) -> tuple[int, str | None]:
    """Return portfolio listing count and optional help text for the sidebar."""
    inventory_mode = selected.get("inventory_mode", "manual")
    config = selected.get("config") or {}
    if inventory_mode == "live_listings" and config.get("listing_live_dates"):
        live_dates = {
            name: parse_config_date(raw)
            for name, raw in config["listing_live_dates"].items()
        }
        listing_inventory = {
            name: int(units) for name, units in config.get("listing_inventory", {}).items()
        }
        count = count_live_listings_in_scope(
            live_dates,
            start_date,
            end_date,
            listing_inventory=listing_inventory or None,
        )
        return count, "Units in scope for this date range based on Oasi go-live dates."
    return int(selected.get("default_inventory_listings") or 30), None


def shift_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        # Handle Feb 29 -> Feb 28
        return d.replace(month=2, day=28, year=d.year - 1)


def period_year_label(start: date, end: date) -> str:
    if start.year == end.year:
        return str(start.year)
    return f"{start.year}–{end.year}"


def format_date_with_day(value: date) -> str:
    """Human-readable date with weekday, e.g. Jul 4, 2026 (Saturday)."""
    return f"{value.strftime('%b')} {value.day}, {value.year} ({value.strftime('%A')})"


def format_stay_period(start: date, end: date) -> str:
    if start == end:
        return format_date_with_day(start)
    return f"{format_date_with_day(start)} → {format_date_with_day(end)}"


def pace_as_of_dates() -> tuple[date, date]:
    """Parallel calendar cutoffs for on-the-books YoY pace."""
    today = date.today()
    return today, shift_one_year(today)


def totals_period_label(stay_start: date, stay_end: date) -> str:
    return (
        f"Total for stay {stay_start.isoformat()} to {stay_end.isoformat()} "
        f"({period_year_label(stay_start, stay_end)})"
    )


PERIOD_OPTIONS = ("Previous year", "Selected year")


def portfolio_for_period(
    period: str,
    *,
    portfolio: dict,
    prior_portfolio: dict,
    start_date: date,
    end_date: date,
    prior_start_date: date,
    prior_end_date: date,
) -> tuple[dict, str]:
    if period == "Previous year":
        return prior_portfolio, totals_period_label(prior_start_date, prior_end_date)
    return portfolio, totals_period_label(start_date, end_date)


def build_yoy_table(
    portfolio: dict,
    compare_portfolio: dict,
    *,
    current_label: str,
    compare_label: str,
    compare_total_label: str,
    compare_total_portfolio: dict | None = None,
) -> pd.io.formats.style.Styler:
    bw = portfolio.get("booking_window") or {}
    compare_bw = compare_portfolio.get("booking_window") or {}
    compare_total_bw = (compare_total_portfolio or {}).get("booking_window") or {}
    yoy_rows = [
        ("Revenue", portfolio["room_revenue"], compare_portfolio["room_revenue"], compare_total_portfolio["room_revenue"] if compare_total_portfolio else None, "money"),
        ("ADR", portfolio["adr"], compare_portfolio["adr"], compare_total_portfolio["adr"] if compare_total_portfolio else None, "money"),
        ("Occupancy %", portfolio["occupancy_pct"], compare_portfolio["occupancy_pct"], compare_total_portfolio["occupancy_pct"] if compare_total_portfolio else None, "pct"),
        ("RevPAR", portfolio["revpar"], compare_portfolio["revpar"], compare_total_portfolio["revpar"] if compare_total_portfolio else None, "money"),
        ("Room nights", portfolio["room_nights_sold"], compare_portfolio["room_nights_sold"], compare_total_portfolio["room_nights_sold"] if compare_total_portfolio else None, "num"),
        ("Bookings", portfolio["bookings_count"], compare_portfolio["bookings_count"], compare_total_portfolio["bookings_count"] if compare_total_portfolio else None, "num"),
        ("Avg LOS", portfolio["average_los"], compare_portfolio["average_los"], compare_total_portfolio["average_los"] if compare_total_portfolio else None, "decimal"),
        ("Avg booking window", bw.get("mean_days"), compare_bw.get("mean_days"), compare_total_bw.get("mean_days") if compare_total_portfolio else None, "days"),
    ]
    yoy_rows_data = []
    for label, cur, prev, prev_total, kind in yoy_rows:
        dval = delta_value(cur, prev)
        dpct = delta_pct(cur, prev)
        yoy_rows_data.append(
            {
                "Metric": label,
                "Kind": kind,
                "Current_raw": cur,
                "Compare_raw": prev,
                "Compare_total_raw": prev_total,
                "Delta_raw": dval,
                "Delta_pct_raw": dpct,
            }
        )
    yoy_df = pd.DataFrame(yoy_rows_data)
    yoy_raw = pd.DataFrame(
        {
            "Metric": yoy_df["Metric"],
            "Delta_raw": yoy_df["Delta_raw"],
            "Delta_pct_raw": yoy_df["Delta_pct_raw"],
        }
    )

    def format_metric_value(row: pd.Series, col: str) -> str:
        raw = row[f"{col}_raw"]
        kind = row["Kind"]
        if _is_missing(raw):
            return "n/a"
        if kind == "money":
            return fmt_money(raw)
        if kind == "pct":
            return fmt_pct(raw)
        if kind == "decimal":
            return fmt_number(raw, decimals=2)
        if kind == "days":
            return fmt_number(raw, decimals=0)
        return fmt_number(raw)

    display_cols = {
        "Metric": yoy_df["Metric"],
        current_label: yoy_df.apply(lambda r: format_metric_value(r, "Current"), axis=1),
        compare_label: yoy_df.apply(lambda r: format_metric_value(r, "Compare"), axis=1),
    }
    if compare_total_portfolio is not None:
        display_cols[compare_total_label] = yoy_df.apply(
            lambda r: format_metric_value(r, "Compare_total"), axis=1
        )
    display_cols["Delta"] = yoy_df.apply(lambda r: format_metric_value(r, "Delta"), axis=1)
    display_cols["Delta %"] = yoy_df["Delta_pct_raw"].apply(fmt_pct)
    yoy_display = pd.DataFrame(display_cols)

    def style_yoy_row(row: pd.Series) -> list[str]:
        styles = [""] * len(row.index)
        idx_map = {col: i for i, col in enumerate(row.index)}
        raw_row = yoy_raw.iloc[row.name]
        styles[idx_map["Delta"]] = delta_style(raw_row["Delta_raw"])
        styles[idx_map["Delta %"]] = delta_style(raw_row["Delta_pct_raw"])
        return styles

    return yoy_display.style.apply(style_yoy_row, axis=1)


def late_pickup_share_pct(portfolio: dict, bands: str, *, late_min_days: int = 31) -> float | None:
    pickup = portfolio.get("pickup") or {}
    bookings_by_band = pickup.get("bookings_by_band") or {}
    parsed = parse_bands(bands)
    late_labels = {band.label for band in parsed if band.min_days >= late_min_days}
    total_bookings = sum(bookings_by_band.values())
    if total_bookings <= 0:
        return None
    late_bookings = sum(bookings_by_band.get(label, 0) for label in late_labels)
    return 100 * late_bookings / total_bookings


def build_yoy_insight_text(
    *,
    pace_current: dict,
    pace_compare: dict,
    prior_total: dict,
    bands: str,
    current_year_label: str,
    compare_year_label: str,
) -> str:
    rev_pace_cur = pace_current.get("room_revenue")
    rev_pace_pri = pace_compare.get("room_revenue")
    rev_total_pri = prior_total.get("room_revenue")
    rev_dpct = delta_pct(rev_pace_cur, rev_pace_pri)

    sentences: list[str] = []
    if rev_dpct is not None:
        if rev_dpct > 0:
            sentences.append(
                f"Ahead on revenue pace ({fmt_pct(rev_dpct)} vs {compare_year_label} at the same point last year)."
            )
        elif rev_dpct < 0:
            sentences.append(
                f"Behind on revenue pace ({fmt_pct(abs(rev_dpct))} below {compare_year_label} "
                f"at the same point last year)."
            )
        else:
            sentences.append(
                f"Revenue pace is even with {compare_year_label} at the same point last year."
            )

    if (
        not _is_missing(rev_pace_cur)
        and not _is_missing(rev_total_pri)
        and float(rev_total_pri) > float(rev_pace_cur or 0)
    ):
        gap = float(rev_total_pri) - float(rev_pace_cur or 0)
        if rev_dpct is not None and rev_dpct < 0:
            sentences.append(
                f"{compare_year_label} finished at {fmt_money(rev_total_pri)} — "
                f"{fmt_money(gap)} above {current_year_label} on-the-books pace today."
            )

    late_share = late_pickup_share_pct(prior_total, bands)
    late_band_labels = [
        band.label for band in parse_bands(bands) if band.min_days >= 31
    ]
    late_band_text = ", ".join(late_band_labels) if late_band_labels else "31+"
    if late_share is not None:
        band_days_text = f"{late_band_text} days before arrival"
        if late_share >= 25:
            sentences.append(
                f"Last year, {round(late_share)}% of bookings were made "
                f"{band_days_text} — demand tended to book early (far ahead of stay)."
            )
        elif late_share < 15:
            sentences.append(
                f"Last year, only {round(late_share)}% of bookings were made "
                f"{band_days_text} — most demand booked closer to arrival (relatively late)."
            )

    return " ".join(sentences) if sentences else "Not enough data to generate a pace summary."


def portfolio_booking_window_mean(portfolio: dict) -> float | None:
    mean_days = (portfolio.get("booking_window") or {}).get("mean_days")
    if _is_missing(mean_days):
        return None
    return float(mean_days)


def render_yoy_insight(text: str) -> None:
    st.markdown("**Insights**")
    st.markdown(
        (
            '<div style="padding: 0.75rem 1rem; margin-bottom: 1rem; '
            'background-color: #292524; border-left: 4px solid #d97706; '
            'border-radius: 4px; font-size: 1rem; line-height: 1.6; color: #fafaf9;">'
            f"{html.escape(text)}"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def render_yoy_pace_summary(
    *,
    pace_current: dict,
    pace_compare: dict,
    prior_total: dict,
    current_year_label: str,
    compare_year_label: str,
) -> None:
    rev_pace_cur = pace_current.get("room_revenue")
    rev_pace_pri = pace_compare.get("room_revenue")
    rev_total_pri = prior_total.get("room_revenue")
    bw_pace_cur = portfolio_booking_window_mean(pace_current)
    bw_pace_pri = portfolio_booking_window_mean(pace_compare)
    bw_total_pri = portfolio_booking_window_mean(prior_total)

    rev_pace_delta = delta_value(rev_pace_cur, rev_pace_pri)
    rev_gap_to_final = delta_value(rev_total_pri, rev_pace_cur)
    bw_pace_delta = delta_value(bw_pace_cur, bw_pace_pri)
    bw_gap_to_final = delta_value(bw_total_pri, bw_pace_cur)

    st.markdown("**Pace snapshot**")
    rev_col1, rev_col2, rev_col3, rev_col4 = st.columns(4)
    rev_col1.metric(
        f"{current_year_label} revenue (pace)",
        fmt_money(rev_pace_cur),
        delta=metric_delta_money(rev_pace_delta),
        delta_color="normal",
        help="On-the-books revenue for the selected stay period as of today. Delta vs same date last year.",
    )
    rev_col2.metric(
        f"{compare_year_label} revenue (pace)",
        fmt_money(rev_pace_pri),
        help="On-the-books revenue for the same stay period last year at the parallel as-of date.",
    )
    rev_col3.metric(
        f"{compare_year_label} final total",
        fmt_money(rev_total_pri),
        help="Final realized revenue for the same stay period last year.",
    )
    rev_col4.metric(
        "Gap to last year final",
        fmt_money(rev_gap_to_final),
        help="How much more revenue last year's final total has vs current on-the-books pace.",
    )

    bw_col1, bw_col2, bw_col3, bw_col4 = st.columns(4)
    bw_col1.metric(
        f"{current_year_label} avg booking window",
        fmt_number(bw_pace_cur, decimals=0),
        delta=metric_delta_int(bw_pace_delta),
        delta_color="normal",
        help="Mean booking window (days) at current pace. Delta vs same date last year.",
    )
    bw_col2.metric(
        f"{compare_year_label} avg booking window",
        fmt_number(bw_pace_pri, decimals=0),
        help="Mean booking window (days) at the parallel as-of date last year.",
    )
    bw_col3.metric(
        f"{compare_year_label} B/W total",
        fmt_number(bw_total_pri, decimals=0),
        help="Final mean booking window (days) for the same stay period last year.",
    )
    bw_col4.metric(
        "B/W gap to last year final",
        fmt_number(bw_gap_to_final, decimals=0),
        help="Difference between last year's final mean booking window and current pace.",
    )


def delta_value(current: float | int | None, compare: float | int | None) -> float | None:
    if _is_missing(current) or _is_missing(compare):
        return None
    return float(current) - float(compare)


def metric_delta_int(value: float | int | None) -> int | None:
    if _is_missing(value):
        return None
    return int(round(float(value)))


def metric_delta_money(value: float | int | None) -> str | None:
    if _is_missing(value):
        return None
    amount = round(float(value))
    if amount < 0:
        return f"-${abs(amount):,}"
    if amount > 0:
        return f"+${amount:,}"
    return "$0"


def delta_pct(current: float | int | None, compare: float | int | None) -> float | None:
    if _is_missing(current) or _is_missing(compare):
        return None
    if float(compare) == 0:
        return None
    return ((float(current) - float(compare)) / float(compare)) * 100


def format_pickup_for_display(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Band": df["band"],
            "Bookings": df["bookings"],
            "Revenue": df["revenue"].apply(fmt_money),
            "Revenue share": df["revenue_share_pct"].apply(fmt_pct),
            "Bookings share": df["bookings_share_pct"].apply(fmt_pct),
        }
    )


def snapshots_by_name(snapshots: list[dict]) -> dict[str, dict]:
    return {item["property_id"]: item for item in snapshots}


BREAKDOWN_OPTIONAL_METRICS = (
    "Bookings",
    "Room nights",
    "ADR",
    "Occupancy",
    "RevPAR",
    "Avg LOS",
)


def _zero_num(value: float | int | None) -> float:
    if _is_missing(value):
        return 0.0
    return float(value)


def _snapshot_metric(item: dict, field: str) -> float | int | None:
    if not item:
        return None
    if field == "avg_booking_window":
        return (item.get("booking_window") or {}).get("mean_days")
    mapping = {
        "bookings": "bookings_count",
        "room_nights": "room_nights_sold",
        "revenue": "room_revenue",
        "adr": "adr",
        "occupancy": "occupancy_pct",
        "revpar": "revpar",
        "avg_los": "average_los",
    }
    return item.get(mapping[field])


BREAKDOWN_YOY_COLUMNS = [
    "name",
    "current_revenue",
    "prior_revenue",
    "revenue_delta",
    "current_booking_window",
    "prior_booking_window",
    "booking_window_delta",
    "current_bookings",
    "prior_bookings",
    "current_room_nights",
    "prior_room_nights",
    "current_adr",
    "prior_adr",
    "current_occupancy",
    "prior_occupancy",
    "current_revpar",
    "prior_revpar",
    "current_avg_los",
    "prior_avg_los",
    "is_total",
]


def build_breakdown_yoy_dataframe(
    current_snapshots: list[dict],
    prior_snapshots: list[dict],
    *,
    current_year_label: str,
    prior_year_label: str,
) -> pd.DataFrame:
    current_lookup = snapshots_by_name(current_snapshots)
    prior_lookup = snapshots_by_name(prior_snapshots)
    all_names = sorted(
        set(current_lookup) | set(prior_lookup),
        key=lambda name: (
            -float(_snapshot_metric(current_lookup.get(name, {}), "revenue") or 0),
            -float(_snapshot_metric(prior_lookup.get(name, {}), "revenue") or 0),
            name.lower(),
        ),
    )

    rows = []
    for name in all_names:
        current_item = current_lookup.get(name, {})
        prior_item = prior_lookup.get(name, {})
        current_revenue = _zero_num(_snapshot_metric(current_item, "revenue"))
        prior_revenue = _zero_num(_snapshot_metric(prior_item, "revenue"))
        current_bw = _zero_num(_snapshot_metric(current_item, "avg_booking_window"))
        prior_bw = _zero_num(_snapshot_metric(prior_item, "avg_booking_window"))
        row = {
            "name": name,
            "current_revenue": current_revenue,
            "prior_revenue": prior_revenue,
            "revenue_delta": current_revenue - prior_revenue,
            "current_booking_window": current_bw,
            "prior_booking_window": prior_bw,
            "booking_window_delta": current_bw - prior_bw,
        }
        for field in ("bookings", "room_nights", "adr", "occupancy", "revpar", "avg_los"):
            row[f"current_{field}"] = _zero_num(_snapshot_metric(current_item, field))
            row[f"prior_{field}"] = _zero_num(_snapshot_metric(prior_item, field))
        row["is_total"] = False
        rows.append(row)

    df = pd.DataFrame(rows, columns=BREAKDOWN_YOY_COLUMNS)
    if not df.empty:
        df = append_breakdown_totals_row(df)
    df.attrs["current_year_label"] = current_year_label
    df.attrs["prior_year_label"] = prior_year_label
    return df


def append_breakdown_totals_row(df: pd.DataFrame) -> pd.DataFrame:
    current_revenue_total = df["current_revenue"].sum()
    prior_revenue_total = df["prior_revenue"].sum()
    current_bw_total = df["current_booking_window"].mean()
    prior_bw_total = df["prior_booking_window"].mean()
    totals: dict[str, object] = {
        "name": "Total",
        "is_total": True,
        "current_revenue": current_revenue_total,
        "prior_revenue": prior_revenue_total,
        "revenue_delta": current_revenue_total - prior_revenue_total,
        "current_booking_window": current_bw_total,
        "prior_booking_window": prior_bw_total,
        "booking_window_delta": current_bw_total - prior_bw_total,
    }
    for field in ("bookings", "room_nights"):
        totals[f"current_{field}"] = df[f"current_{field}"].sum()
        totals[f"prior_{field}"] = df[f"prior_{field}"].sum()
    for field in ("adr", "occupancy", "revpar", "avg_los"):
        totals[f"current_{field}"] = df[f"current_{field}"].mean()
        totals[f"prior_{field}"] = df[f"prior_{field}"].mean()

    detail_rows = df.copy()
    if "is_total" not in detail_rows.columns:
        detail_rows["is_total"] = False
    return pd.concat([detail_rows, pd.DataFrame([totals])], ignore_index=True)


def format_breakdown_yoy_display(
    df: pd.DataFrame,
    *,
    extra_metrics: list[str],
) -> pd.DataFrame:
    cur = df.attrs.get("current_year_label", "Current")
    pri = df.attrs.get("prior_year_label", "Prior")
    if df.empty:
        return pd.DataFrame(
            columns=[
                "Name",
                f"{cur} revenue",
                f"{pri} revenue",
                "Revenue Δ",
                f"{cur} B/W",
                f"{pri} B/W",
                "B/W Δ",
            ]
        )
    columns: dict[str, pd.Series] = {
        "Name": df["name"],
        f"{cur} revenue": df["current_revenue"].apply(fmt_money),
        f"{pri} revenue": df["prior_revenue"].apply(fmt_money),
        "Revenue Δ": df["revenue_delta"].apply(fmt_money),
        f"{cur} B/W": df["current_booking_window"].apply(lambda v: fmt_number(v, decimals=0)),
        f"{pri} B/W": df["prior_booking_window"].apply(lambda v: fmt_number(v, decimals=0)),
        "B/W Δ": df["booking_window_delta"].apply(lambda v: fmt_number(v, decimals=0)),
    }

    metric_formatters = {
        "Bookings": (lambda v: fmt_number(v), lambda v: fmt_number(v)),
        "Room nights": (lambda v: fmt_number(v), lambda v: fmt_number(v)),
        "ADR": (fmt_money, fmt_money),
        "Occupancy": (fmt_pct, fmt_pct),
        "RevPAR": (fmt_money, fmt_money),
        "Avg LOS": (lambda v: fmt_number(v, decimals=2), lambda v: fmt_number(v, decimals=2)),
    }
    field_keys = {
        "Bookings": "bookings",
        "Room nights": "room_nights",
        "ADR": "adr",
        "Occupancy": "occupancy",
        "RevPAR": "revpar",
        "Avg LOS": "avg_los",
    }
    for metric in extra_metrics:
        field = field_keys[metric]
        cur_fmt, pri_fmt = metric_formatters[metric]
        columns[f"{cur} {metric}"] = [
            cur_fmt(v) for v in df[f"current_{field}"]
        ]
        columns[f"{pri} {metric}"] = [
            pri_fmt(v) for v in df[f"prior_{field}"]
        ]

    return pd.DataFrame(columns)


def style_breakdown_yoy_display(df: pd.DataFrame, display: pd.DataFrame) -> pd.io.formats.style.Styler:
    def style_row(row: pd.Series) -> list[str]:
        styles = [""] * len(row.index)
        raw_row = df.iloc[row.name]
        idx_map = {col: i for i, col in enumerate(row.index)}
        if raw_row.get("is_total"):
            styles = ["font-weight: bold;"] * len(row.index)
            if "Revenue Δ" in idx_map:
                styles[idx_map["Revenue Δ"]] = (
                    f"font-weight: bold; {delta_style(raw_row.get('revenue_delta'))}"
                )
            if "B/W Δ" in idx_map:
                styles[idx_map["B/W Δ"]] = (
                    f"font-weight: bold; {delta_style(raw_row.get('booking_window_delta'))}"
                )
            return styles
        if "Revenue Δ" in idx_map:
            styles[idx_map["Revenue Δ"]] = delta_style(raw_row.get("revenue_delta"))
        if "B/W Δ" in idx_map:
            styles[idx_map["B/W Δ"]] = delta_style(raw_row.get("booking_window_delta"))
        return styles

    return display.style.apply(style_row, axis=1)


def fetch_pace_snapshots(
    api_url: str,
    base_params: dict,
    *,
    compare_start_date: date,
    compare_end_date: date,
) -> tuple[dict, dict, dict, dict, date, date]:
    pace_as_of_current, pace_as_of_prior = pace_as_of_dates()
    pace_current_params = {
        **base_params,
        "as_of_date": pace_as_of_current.isoformat(),
    }
    pace_compare_params = {
        **base_params,
        "start_date": compare_start_date.isoformat(),
        "end_date": compare_end_date.isoformat(),
        "as_of_date": pace_as_of_prior.isoformat(),
    }
    pace_grouping_current_params = {**pace_current_params, "breakdown_by": "grouping"}
    pace_grouping_compare_params = {**pace_compare_params, "breakdown_by": "grouping"}
    pace_current_payload = fetch_snapshot(api_url, pace_current_params)
    pace_compare_payload = fetch_snapshot(api_url, pace_compare_params)
    pace_grouping_current_payload = fetch_snapshot(api_url, pace_grouping_current_params)
    pace_grouping_compare_payload = fetch_snapshot(api_url, pace_grouping_compare_params)
    return (
        pace_current_payload,
        pace_compare_payload,
        pace_grouping_current_payload,
        pace_grouping_compare_payload,
        pace_as_of_current,
        pace_as_of_prior,
    )


def store_pace_snapshot_state(
    *,
    pace_current_payload: dict,
    pace_compare_payload: dict,
    pace_grouping_current_payload: dict,
    pace_grouping_compare_payload: dict,
    pace_as_of_current: date,
    pace_as_of_prior: date,
) -> None:
    st.session_state.snapshot_pace_current_payload = pace_current_payload
    st.session_state.snapshot_pace_compare_payload = pace_compare_payload
    st.session_state.snapshot_pace_as_of_current = pace_as_of_current
    st.session_state.snapshot_pace_as_of_prior = pace_as_of_prior
    st.session_state.snapshot_breakdown_listing = {
        "current": pace_current_payload["breakdown_snapshots"],
        "prior": pace_compare_payload["breakdown_snapshots"],
    }
    st.session_state.snapshot_breakdown_grouping = {
        "current": pace_grouping_current_payload["breakdown_snapshots"],
        "prior": pace_grouping_compare_payload["breakdown_snapshots"],
    }


with st.sidebar:
    st.header("Settings")
    with st.expander("Advanced", expanded=False):
        st.session_state.api_url = st.text_input("API URL", value=st.session_state.api_url)
        st.session_state.data_root = st.text_input("Data root", value=st.session_state.data_root)
    try:
        properties = fetch_properties(st.session_state.api_url, st.session_state.data_root)
    except requests.RequestException as exc:
        st.error(f"Cannot reach API: {exc}")
        st.stop()

    if not properties:
        st.warning("No properties found under data root.")
        st.stop()

    property_labels = {f"{p['name']} ({p['id']})": p for p in properties}
    property_options = list(property_labels.keys())
    default_property_index = next(
        (
            index
            for index, label in enumerate(property_options)
            if property_labels[label]["id"].upper() == "LAFAVE"
        ),
        0,
    )
    selected_label = st.selectbox("Property", property_options, index=default_property_index)
    selected = property_labels[selected_label]

    data_source = selected.get("data_source", "csv")
    if data_source == "google_sheets":
        last_synced = selected.get("last_synced_at")
        if last_synced:
            st.caption(f"Last synced: {last_synced}")
        else:
            st.caption("Not synced yet — refresh from Google Sheets before running.")
        property_folder = selected.get("config", {}).get("folder") or selected.get("folder")
        if st.button("Refresh from Google Sheets", use_container_width=True):
            try:
                sync_result = sync_google_sheets(
                    st.session_state.api_url,
                    st.session_state.data_root,
                    property_folder=property_folder,
                )
                fetch_properties.clear()
                if sync_result.get("errors"):
                    st.error(sync_result["errors"][0]["error"])
                else:
                    st.success("Google Sheets sync complete.")
                    st.rerun()
            except requests.RequestException as exc:
                st.error(f"Sync failed: {exc}")

    st.divider()
    st.header("Date range")
    if "start_date" not in st.session_state:
        st.session_state.start_date = date(2026, 7, 4)
    if "end_date" not in st.session_state:
        st.session_state.end_date = date(2026, 7, 5)

    col_a, col_b = st.columns(2)
    with col_a:
        start_date = st.date_input("Start", key="start_date")
    with col_b:
        end_date = st.date_input("End", key="end_date")

    st.caption(format_stay_period(start_date, end_date))

    inventory_count, inventory_help = dashboard_inventory_count(
        selected,
        start_date=start_date,
        end_date=end_date,
    )
    inventory_mode = selected.get("inventory_mode", "manual")
    inventory_key = (
        f"inventory_{selected['id']}_{start_date}_{end_date}"
        if inventory_mode == "live_listings"
        else f"inventory_{selected['id']}"
    )
    if inventory_mode == "live_listings":
        inventory_listings = st.number_input(
            "Portfolio listing count",
            min_value=0,
            value=int(inventory_count),
            step=1,
            disabled=True,
            help=inventory_help,
            key=inventory_key,
        )
    else:
        inventory_listings = st.number_input(
            "Portfolio listing count",
            min_value=1,
            value=int(inventory_count),
            step=1,
            key=inventory_key,
        )
    bands = st.text_input("Booking window bands", value=DEFAULT_BANDS)
    st.divider()
    st.header("YoY table")
    use_custom_compare = st.checkbox("Use custom compare range for YoY table", value=False)
    prior_start_date = shift_one_year(start_date)
    prior_end_date = shift_one_year(end_date)
    compare_start_date = prior_start_date
    compare_end_date = prior_end_date
    if use_custom_compare:
        c1, c2 = st.columns(2)
        with c1:
            compare_start_date = st.date_input("Compare start", value=prior_start_date)
        with c2:
            compare_end_date = st.date_input("Compare end", value=prior_end_date)
        st.caption(f"Compare stay: {format_stay_period(compare_start_date, compare_end_date)}")
    run = st.button("Run snapshot", type="primary", use_container_width=True)

pace_as_of_current, pace_as_of_prior = pace_as_of_dates()
pace_as_of_stale = (
    "snapshot_payload" in st.session_state
    and st.session_state.get("snapshot_pace_as_of_current") != pace_as_of_current
)

if run:
    property_folder = selected.get("config", {}).get("folder") or selected.get("folder") or selected["id"]
    base_params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "property_id": selected["id"],
        "property_name": selected["name"],
        "property_folder": property_folder,
        "data_root": st.session_state.data_root,
        "date_basis": "stay",
        "breakdown_by": "listing",
        "bands": bands,
        "inventory_listings": int(inventory_listings),
    }
    if selected.get("csv_path"):
        base_params["csv_path"] = selected["csv_path"]
    if inventory_mode == "live_listings":
        base_params["yoy_compare_start_date"] = compare_start_date.isoformat()
        base_params["yoy_compare_end_date"] = compare_end_date.isoformat()

    try:
        payload = fetch_snapshot(st.session_state.api_url, base_params)
    except requests.RequestException as exc:
        st.error(f"Snapshot failed: {exc}")
        st.stop()

    prior_params = {
        **base_params,
        "start_date": prior_start_date.isoformat(),
        "end_date": prior_end_date.isoformat(),
    }
    if inventory_mode == "live_listings":
        prior_params["yoy_compare_start_date"] = start_date.isoformat()
        prior_params["yoy_compare_end_date"] = end_date.isoformat()
    try:
        prior_payload = fetch_snapshot(st.session_state.api_url, prior_params)
    except requests.RequestException as exc:
        st.error(f"Prior-year snapshot failed: {exc}")
        st.stop()

    try:
        (
            pace_current_payload,
            pace_compare_payload,
            pace_grouping_current_payload,
            pace_grouping_compare_payload,
            pace_as_of_current,
            pace_as_of_prior,
        ) = fetch_pace_snapshots(
            st.session_state.api_url,
            base_params,
            compare_start_date=compare_start_date,
            compare_end_date=compare_end_date,
        )
    except requests.RequestException as exc:
        st.error(f"Pace comparison snapshot failed: {exc}")
        st.stop()

    st.session_state.snapshot_payload = payload
    st.session_state.snapshot_prior_payload = prior_payload
    st.session_state.snapshot_base_params = base_params
    st.session_state.snapshot_start_date = start_date
    st.session_state.snapshot_end_date = end_date
    st.session_state.snapshot_prior_start_date = prior_start_date
    st.session_state.snapshot_prior_end_date = prior_end_date
    st.session_state.snapshot_compare_start_date = compare_start_date
    st.session_state.snapshot_compare_end_date = compare_end_date
    store_pace_snapshot_state(
        pace_current_payload=pace_current_payload,
        pace_compare_payload=pace_compare_payload,
        pace_grouping_current_payload=pace_grouping_current_payload,
        pace_grouping_compare_payload=pace_grouping_compare_payload,
        pace_as_of_current=pace_as_of_current,
        pace_as_of_prior=pace_as_of_prior,
    )

elif pace_as_of_stale:
    try:
        (
            pace_current_payload,
            pace_compare_payload,
            pace_grouping_current_payload,
            pace_grouping_compare_payload,
            pace_as_of_current,
            pace_as_of_prior,
        ) = fetch_pace_snapshots(
            st.session_state.api_url,
            st.session_state.snapshot_base_params,
            compare_start_date=st.session_state.snapshot_compare_start_date,
            compare_end_date=st.session_state.snapshot_compare_end_date,
        )
    except requests.RequestException as exc:
        st.warning(f"Pace data refresh failed: {exc}")
    else:
        store_pace_snapshot_state(
            pace_current_payload=pace_current_payload,
            pace_compare_payload=pace_compare_payload,
            pace_grouping_current_payload=pace_grouping_current_payload,
            pace_grouping_compare_payload=pace_grouping_compare_payload,
            pace_as_of_current=pace_as_of_current,
            pace_as_of_prior=pace_as_of_prior,
        )

if "snapshot_payload" not in st.session_state:
    st.stop()

payload = st.session_state.snapshot_payload
prior_payload = st.session_state.snapshot_prior_payload
pace_current_payload = st.session_state.snapshot_pace_current_payload
pace_compare_payload = st.session_state.snapshot_pace_compare_payload
pace_as_of_current, pace_as_of_prior = pace_as_of_dates()
start_date = st.session_state.snapshot_start_date
end_date = st.session_state.snapshot_end_date
prior_start_date = st.session_state.snapshot_prior_start_date
prior_end_date = st.session_state.snapshot_prior_end_date
compare_start_date = st.session_state.snapshot_compare_start_date
compare_end_date = st.session_state.snapshot_compare_end_date

portfolio = payload["portfolio_snapshot"]
prior_portfolio = prior_payload["portfolio_snapshot"]
pace_current_portfolio = pace_current_payload["portfolio_snapshot"]
pace_compare_portfolio = pace_compare_payload["portfolio_snapshot"]
dr = portfolio["date_range"]
prior_dr = prior_portfolio["date_range"]
pace_compare_dr = pace_compare_portfolio["date_range"]

current_year_label = period_year_label(start_date, end_date)
compare_year_label = period_year_label(compare_start_date, compare_end_date)
st.subheader(f"YoY comparison — {current_year_label} vs {compare_year_label}")
st.caption(
    f"**Selected stay:** {format_stay_period(start_date, end_date)} · "
    f"**Compare stay:** {format_stay_period(compare_start_date, compare_end_date)}"
)
comparable_listings = payload.get("comparable_listings_used") or []
comparable_note = ""
if comparable_listings:
    comparable_note = (
        f" Comparable units only ({len(comparable_listings)}): "
        f"{', '.join(comparable_listings)}."
    )
st.caption(
    f"Pace as of **{pace_as_of_current.isoformat()}** ({dr['start']} to {dr['end']}) vs "
    f"**{pace_as_of_prior.isoformat()}** ({pace_compare_dr['start']} to {pace_compare_dr['end']}). "
    f"**{compare_year_label} total** is the final realized outcome for the same stay period last year."
    f"{comparable_note}"
)
render_yoy_pace_summary(
    pace_current=pace_current_portfolio,
    pace_compare=pace_compare_portfolio,
    prior_total=prior_portfolio,
    current_year_label=current_year_label,
    compare_year_label=compare_year_label,
)
insight_text = build_yoy_insight_text(
    pace_current=pace_current_portfolio,
    pace_compare=pace_compare_portfolio,
    prior_total=prior_portfolio,
    bands=bands,
    current_year_label=current_year_label,
    compare_year_label=compare_year_label,
)
render_yoy_insight(insight_text)

st.markdown("**Full metric comparison**")
yoy_styler = build_yoy_table(
    pace_current_portfolio,
    pace_compare_portfolio,
    current_label=f"{current_year_label} (pace)",
    compare_label=f"{compare_year_label} (pace)",
    compare_total_label=f"{compare_year_label} total",
    compare_total_portfolio=prior_portfolio,
)
st.dataframe(yoy_styler, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Booking window analysis")
bw_period = st.radio(
    "Period",
    options=PERIOD_OPTIONS,
    index=0,
    horizontal=True,
    key="bw_period",
    help="Prior-year booking windows are usually more informative for a future stay period.",
)
bw_portfolio, bw_period_label = portfolio_for_period(
    bw_period,
    portfolio=portfolio,
    prior_portfolio=prior_portfolio,
    start_date=start_date,
    end_date=end_date,
    prior_start_date=prior_start_date,
    prior_end_date=prior_end_date,
)
st.caption(f"Summary and pickup split for booking-window behavior · {bw_period_label}")
st.markdown("**Summary**")
bw = bw_portfolio.get("booking_window") or {}
bw_col1, bw_col2 = st.columns(2)
bw_col1.metric("Mean booking window (days)", fmt_number(bw.get("mean_days"), decimals=0))
bw_col2.metric("Median booking window (days)", fmt_number(bw.get("median_days"), decimals=0))

st.markdown("**Pickup by booking window**")
pickup = bw_portfolio["pickup"]
band_order = sort_band_labels(list(pickup["bookings_by_band"].keys()), parse_bands(bands))
pickup_df = pd.DataFrame(
    {
        "band": band_order,
        "bookings": [pickup["bookings_by_band"].get(b, 0) for b in band_order],
        "revenue": [round(pickup["revenue_by_band"].get(b, 0) or 0) for b in band_order],
        "revenue_share_pct": [pickup.get("revenue_share_pct_by_band", {}).get(b) for b in band_order],
        "bookings_share_pct": [pickup.get("bookings_share_pct_by_band", {}).get(b) for b in band_order],
    }
)

chart_col, table_col = st.columns([2, 1])
with chart_col:
    st.bar_chart(pickup_df.set_index("band")["bookings"])
with table_col:
    st.dataframe(format_pickup_for_display(pickup_df), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Breakdown")
breakdown_view = st.radio(
    "View by",
    options=["listing", "grouping"],
    index=0,
    horizontal=True,
    key="breakdown_view",
)
extra_breakdown_cols = st.multiselect(
    "Additional columns",
    options=list(BREAKDOWN_OPTIONAL_METRICS),
    default=[],
    key="breakdown_extra_cols",
    help="Optional metrics for each year. Core revenue and booking-window pace columns are always shown.",
)

if "snapshot_breakdown_listing" not in st.session_state:
    st.session_state.snapshot_breakdown_listing = {
        "current": pace_current_payload["breakdown_snapshots"],
        "prior": pace_compare_payload["breakdown_snapshots"],
    }
    st.session_state.snapshot_breakdown_grouping = st.session_state.snapshot_breakdown_listing

breakdown_data = (
    st.session_state.snapshot_breakdown_listing
    if breakdown_view == "listing"
    else st.session_state.snapshot_breakdown_grouping
)
st.caption(
    f"Pace comparison by {breakdown_view}: on the books as of {pace_as_of_current.isoformat()} "
    f"({dr['start']} to {dr['end']}) vs as of {pace_as_of_prior.isoformat()} "
    f"({pace_compare_dr['start']} to {pace_compare_dr['end']})."
)
breakdown_raw = build_breakdown_yoy_dataframe(
    breakdown_data["current"],
    breakdown_data["prior"],
    current_year_label=current_year_label,
    prior_year_label=compare_year_label,
)
breakdown_display = format_breakdown_yoy_display(
    breakdown_raw, extra_metrics=extra_breakdown_cols
)
breakdown_styler = style_breakdown_yoy_display(breakdown_raw, breakdown_display)
st.dataframe(breakdown_styler, use_container_width=True, hide_index=True)

st.download_button(
    "Download breakdown CSV",
    data=breakdown_raw.to_csv(index=False),
    file_name=f"snapshot_{breakdown_view}_{dr['start']}_{dr['end']}.csv",
    mime="text/csv",
)

st.divider()
st.subheader("Arrival day-of-week mix (bookings)")
dow_period = st.radio(
    "Period",
    options=PERIOD_OPTIONS,
    index=0,
    horizontal=True,
    key="dow_period",
    help="Prior-year arrival patterns are usually more informative for a future stay period.",
)
dow_portfolio, dow_period_label = portfolio_for_period(
    dow_period,
    portfolio=portfolio,
    prior_portfolio=prior_portfolio,
    start_date=start_date,
    end_date=end_date,
    prior_start_date=prior_start_date,
    prior_end_date=prior_end_date,
)
st.caption(dow_period_label)
dow_mix = dow_portfolio.get("arrival_day_of_week_mix") or {}
if dow_mix:
    dow_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_df = pd.DataFrame(
        {"Day": dow_order, "Bookings": [dow_mix.get(k, 0) for k in dow_order]}
    )
    st.bar_chart(dow_df.set_index("Day")["Bookings"])
else:
    st.caption("No day-of-week mix available for this selection.")
