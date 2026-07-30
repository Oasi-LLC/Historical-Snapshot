from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from historical_snapshot.config import list_property_configs
from historical_snapshot.io.google_sheets import (
    CREDENTIALS_ENV,
    SPREADSHEET_ID_ENV,
    SyncResult,
    read_sync_metadata,
    sheets_cache_path,
    sheets_meta_path,
    sync_all_properties,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_SYNC_TIMEZONE = "Europe/Lisbon"


def sync_timezone(name: str | None = None) -> ZoneInfo:
    tz_name = (name or os.environ.get("SYNC_TIMEZONE") or DEFAULT_SYNC_TIMEZONE).strip()
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"Invalid SYNC_TIMEZONE: {tz_name}") from exc


def _parse_synced_at(synced_at_iso: str) -> datetime:
    raw = synced_at_iso.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def synced_on_date(
    synced_at_iso: str,
    *,
    day: date,
    tz: ZoneInfo,
) -> bool:
    synced_at = _parse_synced_at(synced_at_iso)
    return synced_at.astimezone(tz).date() == day


def _google_sheets_property_configs():
    return [
        config
        for config in list_property_configs()
        if config.data_source is not None and config.data_source.is_google_sheets
    ]


def needs_daily_sync(
    data_root: Path | str = "data",
    *,
    tz: ZoneInfo | None = None,
    today: date | None = None,
) -> tuple[bool, str]:
    zone = tz or sync_timezone()
    day = today or datetime.now(zone).date()
    configs = _google_sheets_property_configs()
    if not configs:
        return False, "no Google Sheets properties configured"

    stale: list[str] = []
    for config in configs:
        folder = config.folder.lower()
        cache_path = sheets_cache_path(config, data_root=data_root)
        meta_path = sheets_meta_path(config, data_root=data_root)
        if not cache_path.is_file():
            stale.append(f"{folder}: missing cache")
            continue
        meta = read_sync_metadata(meta_path)
        if meta is None:
            stale.append(f"{folder}: missing sync metadata")
            continue
        synced_at = meta.get("synced_at")
        if not synced_at or not isinstance(synced_at, str):
            stale.append(f"{folder}: invalid synced_at")
            continue
        try:
            if not synced_on_date(synced_at, day=day, tz=zone):
                stale.append(f"{folder}: last synced {synced_at}")
        except ValueError:
            stale.append(f"{folder}: unparseable synced_at")

    if stale:
        return True, "; ".join(stale)

    return False, f"all {len(configs)} properties synced today ({zone.key})"


def _sheets_env_configured() -> bool:
    return bool(
        os.environ.get(SPREADSHEET_ID_ENV, "").strip()
        and os.environ.get(CREDENTIALS_ENV, "").strip()
    )


def ensure_daily_sync(
    data_root: Path | str = "data",
    *,
    tz: ZoneInfo | None = None,
    force: bool = False,
    today: date | None = None,
) -> SyncResult | None:
    zone = tz or sync_timezone()

    if not _sheets_env_configured():
        LOGGER.warning(
            "Skipping startup sync: set %s and %s",
            SPREADSHEET_ID_ENV,
            CREDENTIALS_ENV,
        )
        return None

    should_sync, reason = needs_daily_sync(data_root=data_root, tz=zone, today=today)
    if not force and not should_sync:
        LOGGER.info("Skipping startup sync — %s", reason)
        return None

    if should_sync:
        LOGGER.info("Running startup sync (%s)", reason)
    else:
        LOGGER.info("Running forced startup sync")

    try:
        result = sync_all_properties(data_root=data_root)
    except ImportError as exc:
        LOGGER.warning("Skipping startup sync: %s", exc)
        return None
    except ValueError as exc:
        LOGGER.warning("Skipping startup sync: %s", exc)
        return None

    if result.errors:
        for folder, error in result.errors:
            LOGGER.error("Startup sync failed for %s: %s", folder, error)
    else:
        synced = ", ".join(item.folder for item in result.properties)
        LOGGER.info("Startup sync complete: %s", synced or "no properties")

    return result
