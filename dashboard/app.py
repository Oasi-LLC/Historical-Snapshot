from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

from historical_snapshot.core.bands import parse_bands, sort_band_labels

API_URL = os.environ.get("SNAPSHOT_API_URL", "http://127.0.0.1:8000")
DEFAULT_BANDS = "0-7,8-15,16-30,31-60,61+"

st.set_page_config(page_title="Historical Snapshot", layout="wide")
st.title("Historical Snapshot Dashboard")
st.caption("Holiday and event performance using stay-overlap date filtering with prorated revenue.")

if "api_url" not in st.session_state:
    st.session_state.api_url = API_URL


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


def shift_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        # Handle Feb 29 -> Feb 28
        return d.replace(month=2, day=28, year=d.year - 1)


def delta_value(current: float | int | None, compare: float | int | None) -> float | None:
    if _is_missing(current) or _is_missing(compare):
        return None
    return float(current) - float(compare)


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
            "Room nights": df["room_nights"],
            "Revenue": df["revenue"].apply(fmt_money),
            "Revenue share": df["revenue_share_pct"].apply(fmt_pct),
            "Nights share": df["nights_share_pct"].apply(fmt_pct),
        }
    )


def format_breakdown_for_display(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Name": df["name"],
            "Bookings": df["bookings"],
            "Room nights": df["room_nights"],
            "Revenue": df["revenue"].apply(fmt_money),
            "ADR": df["adr"].apply(fmt_money),
            "Occupancy": df["occupancy_pct"].apply(fmt_pct),
            "RevPAR": df["revpar"].apply(fmt_money),
            "Avg LOS": df["avg_los"].apply(lambda v: fmt_number(v, decimals=2)),
        }
    )


with st.sidebar:
    st.header("Settings")
    st.session_state.api_url = st.text_input("API URL", value=st.session_state.api_url)
    data_root = st.text_input("Data root", value="data")
    try:
        properties = fetch_properties(st.session_state.api_url, data_root)
    except requests.RequestException as exc:
        st.error(f"Cannot reach API: {exc}")
        st.stop()

    if not properties:
        st.warning("No properties found under data root.")
        st.stop()

    property_labels = {f"{p['name']} ({p['id']})": p for p in properties}
    selected_label = st.selectbox("Property", list(property_labels.keys()))
    selected = property_labels[selected_label]

    st.divider()
    st.header("Date range")
    if "start_date" not in st.session_state:
        st.session_state.start_date = date(2025, 7, 4)
    if "end_date" not in st.session_state:
        st.session_state.end_date = date(2025, 7, 5)

    preset = st.selectbox(
        "Quick preset",
        [
            "Custom",
            "July 4 weekend 2025 (Fri–Sat)",
            "July 4 weekend 2024 (Thu–Fri)",
            "Last 30 days",
        ],
    )
    if preset == "July 4 weekend 2025 (Fri–Sat)":
        st.session_state.start_date = date(2025, 7, 4)
        st.session_state.end_date = date(2025, 7, 5)
    elif preset == "July 4 weekend 2024 (Thu–Fri)":
        st.session_state.start_date = date(2024, 7, 4)
        st.session_state.end_date = date(2024, 7, 5)
    elif preset == "Last 30 days":
        st.session_state.end_date = date.today()
        st.session_state.start_date = st.session_state.end_date - timedelta(days=29)

    col_a, col_b = st.columns(2)
    with col_a:
        start_date = st.date_input("Start", key="start_date")
    with col_b:
        end_date = st.date_input("End", key="end_date")

    date_basis = st.selectbox(
        "Date filter",
        options=["stay", "arrival", "reservation"],
        index=0,
        help="Stay = occupied nights in range (prorated revenue). Arrival/reservation = full booking when check-in/book date falls in range.",
    )
    breakdown_by = st.selectbox(
        "Breakdown",
        options=["listing", "grouping"],
        index=0 if selected.get("config", {}).get("defaults", {}).get("breakdown_by", "listing") == "listing" else 1,
    )
    inventory_mode = selected.get("inventory_mode", "manual")
    default_inventory = selected.get("default_inventory_listings") or 30
    if inventory_mode == "active_listings":
        st.caption(
            "Portfolio occupancy uses listings active in each date range (from booking history), "
            "so YoY compare uses the correct unit count per period."
        )
        inventory_listings = default_inventory
    else:
        inventory_listings = st.number_input(
            "Portfolio listing count",
            min_value=1,
            value=int(default_inventory),
            step=1,
        )
    bands = st.text_input("Booking window bands", value=DEFAULT_BANDS)
    st.divider()
    st.header("YoY comparison")
    enable_compare = st.checkbox("Enable compare mode", value=False)
    use_custom_compare = st.checkbox("Use custom compare range", value=False, disabled=not enable_compare)
    compare_start_date = shift_one_year(start_date)
    compare_end_date = shift_one_year(end_date)
    if enable_compare and use_custom_compare:
        c1, c2 = st.columns(2)
        with c1:
            compare_start_date = st.date_input("Compare start", value=compare_start_date)
        with c2:
            compare_end_date = st.date_input("Compare end", value=compare_end_date)
    run = st.button("Run snapshot", type="primary", use_container_width=True)

st.info(
    "**Stay filter (default):** KPIs use only nights and prorated revenue inside your date range. "
    "Revenue = Amount × (nights in range ÷ total nights)."
)

if not run:
    st.stop()

params = {
    "csv_path": selected["csv_path"],
    "start_date": start_date.isoformat(),
    "end_date": end_date.isoformat(),
    "property_id": selected["id"],
    "property_name": selected["name"],
    "date_basis": date_basis,
    "breakdown_by": breakdown_by,
    "bands": bands,
    "inventory_listings": int(inventory_listings),
}
if inventory_mode == "active_listings":
    params["inventory_mode"] = "active_listings"

try:
    payload = fetch_snapshot(st.session_state.api_url, params)
except requests.RequestException as exc:
    st.error(f"Snapshot failed: {exc}")
    st.stop()

compare_payload = None
if enable_compare:
    compare_params = {
        **params,
        "start_date": compare_start_date.isoformat(),
        "end_date": compare_end_date.isoformat(),
    }
    try:
        compare_payload = fetch_snapshot(st.session_state.api_url, compare_params)
    except requests.RequestException as exc:
        st.error(f"Compare snapshot failed: {exc}")
        st.stop()

portfolio = payload["portfolio_snapshot"]
dr = portfolio["date_range"]

st.subheader(f"{payload['property']['name']} — {dr['start']} to {dr['end']}")
if payload.get("inventory_mode") == "active_listings":
    units_used = payload.get("inventory_units_used")
    if units_used is not None:
        st.caption(f"Active listings in range (occupancy denominator): **{units_used}**")

k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
k1.metric("Bookings", portfolio["bookings_count"])
k2.metric("Room nights", portfolio["room_nights_sold"])
k3.metric("Revenue", fmt_money(portfolio["room_revenue"]))
k4.metric("ADR", fmt_money(portfolio["adr"]))
k5.metric("Occupancy", fmt_pct(portfolio["occupancy_pct"]))
k6.metric("RevPAR", fmt_money(portfolio["revpar"]))
k7.metric("Avg LOS", fmt_number(portfolio["average_los"], decimals=2))

if compare_payload is not None:
    st.divider()
    compare_portfolio = compare_payload["portfolio_snapshot"]
    compare_dr = compare_portfolio["date_range"]
    st.subheader(f"YoY comparison vs {compare_dr['start']} to {compare_dr['end']}")
    if compare_payload.get("inventory_mode") == "active_listings":
        compare_units = compare_payload.get("inventory_units_used")
        current_units = payload.get("inventory_units_used")
        if compare_units is not None and current_units is not None:
            st.caption(
                f"Active listings: **{current_units}** (current) vs **{compare_units}** (compare period)"
            )
    yoy_rows = [
        ("Revenue", portfolio["room_revenue"], compare_portfolio["room_revenue"], "money"),
        ("ADR", portfolio["adr"], compare_portfolio["adr"], "money"),
        ("Occupancy %", portfolio["occupancy_pct"], compare_portfolio["occupancy_pct"], "pct"),
        ("RevPAR", portfolio["revpar"], compare_portfolio["revpar"], "money"),
        ("Room nights", portfolio["room_nights_sold"], compare_portfolio["room_nights_sold"], "num"),
        ("Bookings", portfolio["bookings_count"], compare_portfolio["bookings_count"], "num"),
    ]
    yoy_rows_data = []
    for label, cur, prev, kind in yoy_rows:
        dval = delta_value(cur, prev)
        dpct = delta_pct(cur, prev)
        yoy_rows_data.append(
            {
                "Metric": label,
                "Kind": kind,
                "Current_raw": cur,
                "Compare_raw": prev,
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
    yoy_display = pd.DataFrame(
        {
            "Metric": yoy_df["Metric"],
            "Current": yoy_df.apply(
                lambda r: fmt_money(r["Current_raw"])
                if r["Kind"] == "money"
                else (fmt_pct(r["Current_raw"]) if r["Kind"] == "pct" else fmt_number(r["Current_raw"])),
                axis=1,
            ),
            "Compare": yoy_df.apply(
                lambda r: fmt_money(r["Compare_raw"])
                if r["Kind"] == "money"
                else (fmt_pct(r["Compare_raw"]) if r["Kind"] == "pct" else fmt_number(r["Compare_raw"])),
                axis=1,
            ),
            "Delta": yoy_df.apply(
                lambda r: fmt_money(r["Delta_raw"])
                if r["Kind"] == "money"
                else (fmt_pct(r["Delta_raw"]) if r["Kind"] == "pct" else fmt_number(r["Delta_raw"])),
                axis=1,
            ),
            "Delta %": yoy_df["Delta_pct_raw"].apply(fmt_pct),
        }
    )
    def style_yoy_row(row: pd.Series) -> list[str]:
        styles = [""] * len(row.index)
        idx_map = {col: i for i, col in enumerate(row.index)}
        raw_row = yoy_raw.iloc[row.name]
        styles[idx_map["Delta"]] = delta_style(raw_row["Delta_raw"])
        styles[idx_map["Delta %"]] = delta_style(raw_row["Delta_pct_raw"])
        return styles

    yoy_styler = yoy_display.style.apply(style_yoy_row, axis=1)
    st.dataframe(yoy_styler, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Booking window analysis")
st.caption("Summary and pickup split for booking-window behavior.")
st.markdown("**Summary**")
bw = portfolio.get("booking_window") or {}
bw_col1, bw_col2 = st.columns(2)
bw_col1.metric("Mean booking window (days)", fmt_number(bw.get("mean_days"), decimals=0))
bw_col2.metric("Median booking window (days)", fmt_number(bw.get("median_days"), decimals=0))

st.markdown("**Pickup by booking window**")
pickup = portfolio["pickup"]
band_order = sort_band_labels(list(pickup["room_nights_by_band"].keys()), parse_bands(bands))
pickup_df = pd.DataFrame(
    {
        "band": band_order,
        "room_nights": [pickup["room_nights_by_band"].get(b, 0) for b in band_order],
        "revenue": [round(pickup["revenue_by_band"].get(b, 0) or 0) for b in band_order],
        "revenue_share_pct": [pickup.get("revenue_share_pct_by_band", {}).get(b) for b in band_order],
        "nights_share_pct": [pickup.get("room_nights_share_pct_by_band", {}).get(b) for b in band_order],
    }
)

chart_col, table_col = st.columns([2, 1])
with chart_col:
    st.bar_chart(pickup_df.set_index("band")["room_nights"])
with table_col:
    st.dataframe(format_pickup_for_display(pickup_df), use_container_width=True, hide_index=True)

st.divider()
st.subheader("Channel mix")
channel_rows = []
for channel, metrics in (portfolio.get("channel_mix") or {}).items():
    channel_rows.append(
        {
            "Channel": channel,
            "Bookings": metrics.get("bookings_count"),
            "Room nights": metrics.get("room_nights"),
            "Revenue": metrics.get("revenue"),
            "Revenue share %": metrics.get("revenue_share_pct"),
            "ADR": metrics.get("adr"),
        }
    )
if channel_rows:
    channel_df = pd.DataFrame(channel_rows).sort_values("Revenue", ascending=False)
    channel_display = pd.DataFrame(
        {
            "Channel": channel_df["Channel"],
            "Bookings": channel_df["Bookings"],
            "Room nights": channel_df["Room nights"],
            "Revenue": channel_df["Revenue"].apply(fmt_money),
            "Revenue share": channel_df["Revenue share %"].apply(fmt_pct),
            "ADR": channel_df["ADR"].apply(fmt_money),
        }
    )
    st.dataframe(channel_display, use_container_width=True, hide_index=True)
    if compare_payload is not None:
        compare_channel_rows = {
            c: m for c, m in (compare_payload["portfolio_snapshot"].get("channel_mix") or {}).items()
        }
        merged = []
        for row in channel_rows:
            channel = row["Channel"]
            prev = compare_channel_rows.get(channel, {})
            cur_rev = row["Revenue"]
            prev_rev = prev.get("revenue")
            cur_adr = row["ADR"]
            prev_adr = prev.get("adr")
            merged.append(
                {
                    "Channel": channel,
                    "Revenue Δ": fmt_money(delta_value(cur_rev, prev_rev)),
                    "Revenue Δ%": fmt_pct(delta_pct(cur_rev, prev_rev)),
                    "ADR Δ": fmt_money(delta_value(cur_adr, prev_adr)),
                    "ADR Δ%": fmt_pct(delta_pct(cur_adr, prev_adr)),
                    "Revenue_delta_raw": delta_value(cur_rev, prev_rev),
                    "Revenue_delta_pct_raw": delta_pct(cur_rev, prev_rev),
                    "ADR_delta_raw": delta_value(cur_adr, prev_adr),
                    "ADR_delta_pct_raw": delta_pct(cur_adr, prev_adr),
                }
            )
        st.caption("YoY channel deltas")
        merged_df = pd.DataFrame(merged)
        merged_raw = merged_df[
            ["Revenue_delta_raw", "Revenue_delta_pct_raw", "ADR_delta_raw", "ADR_delta_pct_raw"]
        ].copy()
        merged_display = merged_df[["Channel", "Revenue Δ", "Revenue Δ%", "ADR Δ", "ADR Δ%"]].copy()

        def style_channel_row(row: pd.Series) -> list[str]:
            styles = [""] * len(row.index)
            idx_map = {col: i for i, col in enumerate(row.index)}
            raw_row = merged_raw.iloc[row.name]
            styles[idx_map["Revenue Δ"]] = delta_style(raw_row["Revenue_delta_raw"])
            styles[idx_map["Revenue Δ%"]] = delta_style(raw_row["Revenue_delta_pct_raw"])
            styles[idx_map["ADR Δ"]] = delta_style(raw_row["ADR_delta_raw"])
            styles[idx_map["ADR Δ%"]] = delta_style(raw_row["ADR_delta_pct_raw"])
            return styles

        merged_styler = merged_display.style.apply(style_channel_row, axis=1)
        st.dataframe(merged_styler, use_container_width=True, hide_index=True)
else:
    st.caption("No channel mix available for this selection.")

st.divider()
st.subheader("Arrival day-of-week mix (bookings)")
dow_mix = portfolio.get("arrival_day_of_week_mix") or {}
if dow_mix:
    dow_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_df = pd.DataFrame(
        {"Day": dow_order, "Bookings": [dow_mix.get(k, 0) for k in dow_order]}
    )
    st.bar_chart(dow_df.set_index("Day")["Bookings"])
else:
    st.caption("No day-of-week mix available for this selection.")

st.divider()
st.subheader(f"Breakdown by {payload['breakdown_by']}")
rows = []
for item in payload["breakdown_snapshots"]:
    rows.append(
        {
            "name": item["property_id"],
            "bookings": item["bookings_count"],
            "room_nights": item["room_nights_sold"],
            "revenue": item["room_revenue"],
            "adr": item["adr"],
            "occupancy_pct": item["occupancy_pct"],
            "revpar": item["revpar"],
            "avg_los": item["average_los"],
        }
    )
breakdown_raw = pd.DataFrame(rows).sort_values("revenue", ascending=False)
breakdown_display = format_breakdown_for_display(breakdown_raw)
st.dataframe(breakdown_display, use_container_width=True, hide_index=True)

st.download_button(
    "Download breakdown CSV",
    data=breakdown_raw.to_csv(index=False),
    file_name=f"snapshot_{dr['start']}_{dr['end']}.csv",
    mime="text/csv",
)
