from __future__ import annotations

from pathlib import Path

from historical_snapshot.config import PropertyConfig, load_pms_profile
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.models import BookingRecord, ValidationIssue


def _property_data_dir(property_config: PropertyConfig, csv_path: str | Path) -> Path:
    path = Path(csv_path)
    if path.is_dir():
        return path
    return path.parent


def read_property_bookings(
    csv_path: str | Path,
    *,
    property_config: PropertyConfig | None,
) -> tuple[list[BookingRecord], list[ValidationIssue]]:
    if property_config is None or not property_config.data_sources:
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
        merged.extend(records)
        issues.extend(source_issues)

    from historical_snapshot.io.postprocess import apply_property_postprocess

    merged = apply_property_postprocess(merged, property_config)
    return merged, issues
