from __future__ import annotations

import re
from datetime import datetime


def strip_markdown_emphasis(text: str) -> str:
    """Remove Markdown/Slack emphasis markers from model text."""
    cleaned = text.replace("**", "").replace("*", "")
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\s+\*\s*$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def format_section_header(title: str) -> str:
    return strip_markdown_emphasis(title)


def _format_iso_range(start: str | None, end: str | None) -> str | None:
    if not start or not end:
        return None
    try:
        s = datetime.strptime(start, "%Y-%m-%d").date()
        e = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        return f"{start} to {end}"
    if s == e:
        return s.strftime("%b %-d, %Y")
    if s.year == e.year and s.month == e.month:
        return f"{s.strftime('%b %-d')}–{e.strftime('%-d, %Y')}"
    if s.year == e.year:
        return f"{s.strftime('%b %-d')}–{e.strftime('%b %-d, %Y')}"
    return f"{s.strftime('%b %-d, %Y')}–{e.strftime('%b %-d, %Y')}"


def format_clarification(message: str, proposed: dict | None = None) -> str:
    body = strip_markdown_emphasis(message)
    if not body:
        body = "Which property and dates should I look up?"

    lines = ["Clarification", ""]

    if proposed:
        property_name = proposed.get("property")
        stay = _format_iso_range(proposed.get("start_date"), proposed.get("end_date"))
        ly = _format_iso_range(proposed.get("prior_start_date"), proposed.get("prior_end_date"))
        if property_name:
            lines.append(f"Property: {property_name}")
        if stay:
            lines.append(f"Stay window: {stay}")
        if ly:
            lines.append(f"LY compare: {ly}")
        if property_name or stay or ly:
            lines.append("")

    lines.append(body)
    if not body.endswith("?"):
        lines.append("")
        lines.append("Reply yes to proceed, or specify different dates.")
    return "\n".join(lines).strip()


FULL_MODE_FIELDS: tuple[str, ...] = (
    "stay_state",
    "primary_comparison",
    "driver_decomposition",
    "gap_to_final",
    "action",
)


def render_interpretation_struct(data: dict, *, reply_mode: str = "full") -> str | None:
    """Render a parsed JSON interpretation object into numbered Slack text.

    Full mode expects one string per doctrine step (any of FULL_MODE_FIELDS); reduced modes
    expect a flat `{"bullets": [...]}` list. Either shape produces discrete, complete bullets
    by construction — no regex line-splitting, no character truncation.
    """
    if not isinstance(data, dict):
        return None

    if any(key in data for key in FULL_MODE_FIELDS):
        lines: list[str] = []
        for key in FULL_MODE_FIELDS:
            value = data.get(key)
            if value in (None, "", "null"):
                continue
            text = strip_markdown_emphasis(str(value)).strip()
            if text:
                lines.append(text)
        if not lines:
            return None
        numbered = [f"{idx}. {item}" for idx, item in enumerate(lines, start=1)]
        return "Interpretation\n\n" + "\n".join(numbered)

    bullets = data.get("bullets")
    if not isinstance(bullets, list) or not bullets:
        return None
    cleaned = [strip_markdown_emphasis(str(item)).strip() for item in bullets]
    cleaned = [item for item in cleaned if item]
    if not cleaned:
        return None
    numbered = [f"{idx}. {item}" for idx, item in enumerate(cleaned[:6], start=1)]
    return "Interpretation\n\n" + "\n".join(numbered)


def format_interpretation(body: str, *, max_items: int = 6) -> str | None:
    cleaned = strip_markdown_emphasis(body)
    cleaned = re.sub(r"^---+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)

    match = re.search(r"^Interpretation\s*(.*)$", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if match:
        cleaned = match.group(1).strip()
    elif "Snapshot as of" in cleaned or re.search(r"^\s*Metric\b", cleaned, re.MULTILINE):
        return None

    cleaned = re.sub(r"(?im)^based on\s+\d+\s+comparable units\.?\s*$", "", cleaned).strip()
    if not cleaned:
        return None

    items: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line or line.lower() == "interpretation":
            continue
        line = re.sub(r"^\d+[.)]\s*", "", line)
        line = line.lstrip("•-* ").strip()
        if not line:
            continue
        # Normalize "Label - detail" only when a spaced dash separates label from body.
        if re.match(r"^[^-–—]+ [-–—] ", line):
            line = re.sub(r"^([^-–—]+?)\s*[-–—]\s+", r"\1 — ", line, count=1)
        items.append(line)

    if not items:
        return None

    numbered = [f"{idx}. {item}" for idx, item in enumerate(items[:max_items], start=1)]
    return "Interpretation\n\n" + "\n".join(numbered)