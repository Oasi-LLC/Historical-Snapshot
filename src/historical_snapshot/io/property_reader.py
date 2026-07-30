from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from historical_snapshot.config import PropertyConfig, load_pms_profile
from historical_snapshot.io.google_sheets import sheets_cache_path, sync_property_tab
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.models import BookingRecord, ValidationIssue

# In-process cache of parsed bookings, keyed by the resolved source file(s) and
# their (mtime, size) signatures. A single chat turn can trigger several
# snapshot computations against the same property (current period, pacing vs
# last year, prior-year final, etc.), each of which previously re-read and
# re-parsed the same CSV/Sheets-cache file from disk. Caching here preserves
# read_property_bookings' exact return values while avoiding that redundant
# I/O + parsing; entries are invalidated automatically whenever the underlying
# file's mtime/size changes (e.g. after a Google Sheets sync writes a fresh
# cache file). Set BOOKINGS_CACHE_DISABLE=true to bypass entirely (debugging).
_BOOKINGS_CACHE_MAX_ENTRIES = 64
_bookings_cache: "OrderedDict[tuple, tuple[list[BookingRecord], list[ValidationIssue]]]" = (
    OrderedDict()
)


def _bookings_cache_disabled() -> bool:
    return os.environ.get("BOOKINGS_CACHE_DISABLE", "").strip().lower() in {"1", "true", "yes"}


def _file_signature(path: Path) -> tuple[str, float, int]:
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime, stat.st_size)
    except OSError:
        return (str(path), -1.0, -1)


def clear_bookings_cache() -> None:
    """Drop all cached parsed bookings (mainly for tests)."""
    _bookings_cache.clear()


def _cache_get(
    key: tuple,
) -> tuple[list[BookingRecord], list[ValidationIssue]] | None:
    cached = _bookings_cache.get(key)
    if cached is None:
        return None
    # Refresh recency for a simple LRU eviction policy.
    _bookings_cache.move_to_end(key)
    return cached


def _cache_put(
    key: tuple,
    value: tuple[list[BookingRecord], list[ValidationIssue]],
) -> None:
    _bookings_cache[key] = value
    _bookings_cache.move_to_end(key)
    while len(_bookings_cache) > _BOOKINGS_CACHE_MAX_ENTRIES:
        _bookings_cache.popitem(last=False)


def _property_data_dir(property_config: PropertyConfig, csv_path: str | Path) -> Path:
    path = Path(csv_path)
    if path.is_dir():
        return path
    return path.parent


def _passes_source_cutoff(record: BookingRecord, cutoff) -> bool:
    if cutoff.property_name and record.property_name != cutoff.property_name:
        return True
    if (
        cutoff.max_reservation_date is not None
        and record.reservation_date > cutoff.max_reservation_date
    ):
        return False
    if cutoff.max_check_in_date is not None and record.check_in_date > cutoff.max_check_in_date:
        return False
    return True


def _apply_source_cutoffs(
    records: list[BookingRecord],
    source_file: str,
    cutoffs,
) -> list[BookingRecord]:
    file_cutoffs = [cutoff for cutoff in cutoffs if cutoff.file == source_file]
    if not file_cutoffs:
        return records
    return [
        record
        for record in records
        if all(_passes_source_cutoff(record, cutoff) for cutoff in file_cutoffs)
    ]


def resolve_property_data_path(
    property_config: PropertyConfig,
    csv_path: str | Path | None = None,
    *,
    data_root: Path | str = "data",
) -> Path:
    if property_config.data_source and property_config.data_source.is_google_sheets:
        cache_path = sheets_cache_path(property_config, data_root=data_root)
        if cache_path.is_file():
            return cache_path
        try:
            sync_property_tab(property_config, data_root=data_root)
        except Exception as exc:
            if csv_path is not None and Path(csv_path).is_file():
                return Path(csv_path)
            raise ValueError(
                f"No Google Sheets cache for {property_config.folder}. "
                f"Run sync first. ({exc})"
            ) from exc
        return cache_path

    if csv_path is not None:
        path = Path(csv_path)
        if path.is_file():
            return path
        if path.is_dir():
            csv_files = sorted(path.glob("*.csv"))
            if csv_files:
                return csv_files[0]

    data_dir = Path(data_root) / property_config.folder
    if data_dir.is_dir():
        csv_files = sorted(data_dir.glob("*.csv"))
        if csv_files:
            return csv_files[0]

    raise ValueError(f"No data file found for property: {property_config.folder}")


def _read_property_bookings_uncached(
    csv_path: str | Path,
    *,
    property_config: PropertyConfig | None,
    data_root: Path | str = "data",
) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    if property_config is None:
        return read_bookings_csv(csv_path, property_config=None)

    if property_config.data_source and property_config.data_source.is_google_sheets:
        data_path = resolve_property_data_path(
            property_config,
            csv_path,
            data_root=data_root,
        )
        return read_bookings_csv(
            data_path,
            property_config=property_config,
            pms_profile=None,
        )

    if not property_config.data_sources:
        return read_bookings_csv(csv_path, property_config=property_config)

    data_dir = _property_data_dir(property_config, csv_path)
    merged: list[BookingRecord] = []
    issues: list[ValidationIssue] = []

    for source in property_config.data_sources:
        source_path = data_dir / source.file
        pms_profile = load_pms_profile(source.pms)
        records, source_issues = read_bookings_csv(
            source_path,
            property_config=property_config,
            pms_profile=pms_profile,
            apply_postprocess=False,
        )
        records = _apply_source_cutoffs(
            records,
            source.file,
            property_config.source_cutoffs,
        )
        merged.extend(records)
        issues.extend(source_issues)

    from historical_snapshot.io.postprocess import apply_property_postprocess

    merged = apply_property_postprocess(merged, property_config)
    return merged, issues


def _cache_signature_paths(
    csv_path: str | Path,
    *,
    property_config: PropertyConfig | None,
    data_root: Path | str,
) -> list[Path] | None:
    """Best-effort list of files this call will actually read, for cache-keying.

    Returns None if resolution can't be determined cheaply/safely without side
    effects (e.g. would trigger a Google Sheets network sync) - callers should
    skip caching in that case rather than risk a stale/incorrect key.
    """
    if property_config is None:
        return [Path(csv_path)]

    if property_config.data_source and property_config.data_source.is_google_sheets:
        cache_path = sheets_cache_path(property_config, data_root=data_root)
        if not cache_path.is_file():
            return None
        return [cache_path]

    if not property_config.data_sources:
        return [Path(csv_path)]

    data_dir = _property_data_dir(property_config, csv_path)
    return [data_dir / source.file for source in property_config.data_sources]


def read_property_bookings(
    csv_path: str | Path,
    *,
    property_config: PropertyConfig | None,
    data_root: Path | str = "data",
) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    if _bookings_cache_disabled():
        return _read_property_bookings_uncached(
            csv_path, property_config=property_config, data_root=data_root
        )

    signature_paths = _cache_signature_paths(
        csv_path, property_config=property_config, data_root=data_root
    )
    if signature_paths is None:
        return _read_property_bookings_uncached(
            csv_path, property_config=property_config, data_root=data_root
        )

    key = (
        property_config.property_id if property_config else None,
        tuple(_file_signature(path) for path in signature_paths),
    )
    cached = _cache_get(key)
    if cached is not None:
        records, issues = cached
        return list(records), list(issues)

    records, issues = _read_property_bookings_uncached(
        csv_path, property_config=property_config, data_root=data_root
    )
    _cache_put(key, (records, issues))
    return list(records), list(issues)
