from __future__ import annotations

ReplyMode = str  # full | delta_listings | delta_channels | delta_listing | listing_compare | interpretation_only


def compose_thread_reply(
    *,
    report: str,
    interpretation: str | None,
    reply_mode: ReplyMode,
) -> str:
    if reply_mode == "interpretation_only":
        if interpretation:
            return interpretation.strip()
        return (report or "I couldn't generate an interpretation for that.").strip()
    if interpretation and report:
        return f"{report.strip()}\n\n{interpretation.strip()}".strip()
    return (report or interpretation or "").strip()
