from __future__ import annotations

from datetime import date


def shift_one_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year - 1)


def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"${round(float(value)):,}"


def fmt_pct(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{round(float(value))}%"


def fmt_number(value: float | int | None, *, decimals: int = 0) -> str:
    if value is None:
        return "n/a"
    if decimals == 0:
        return f"{round(float(value)):,}"
    return f"{float(value):,.{decimals}f}"


def format_short_period_label(start: date, end: date) -> str:
    if start == end:
        return start.strftime("%b %-d")
    if start.year == end.year and start.month == end.month:
        return f"{start.strftime('%b %-d')}–{end.strftime('%-d')}"
    if start.year == end.year:
        return f"{start.strftime('%b %-d')}–{end.strftime('%b %-d')}"
    return f"{start.strftime('%b %-d, %Y')}–{end.strftime('%b %-d, %Y')}"


def format_date_label(value: date) -> str:
    return value.strftime("%b %-d, %Y")


def pct_change(current: float | int | None, prior: float | int | None) -> str:
    if current is None or prior is None:
        return "n/a"
    prior_f = float(prior)
    if prior_f == 0:
        return "n/a"
    delta = ((float(current) - prior_f) / prior_f) * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.0f}%"
