from __future__ import annotations

from pathlib import Path

from historical_snapshot.config import PropertyConfig, load_pms_profile
from historical_snapshot.io.google_sheets import sheets_cache_path, sync_property_tab
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.models import BookingRecord, ValidationIssue


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


def read_property_bookings(
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
