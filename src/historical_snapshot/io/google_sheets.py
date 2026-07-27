from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from historical_snapshot.config import PropertyConfig, load_pms_profile, list_property_configs
from historical_snapshot.io.reader import read_bookings_csv
from historical_snapshot.models import BookingRecord

SPREADSHEET_ID_ENV = "GOOGLE_SHEETS_SPREADSHEET_ID"
CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
CANONICAL_HEADERS = (
    "booking_date",
    "reservation_date",
    "check_in_date",
    "check_out_date",
    "room_revenue",
    "room_nights",
    "listing_name",
    "channel",
    "status",
    "grouping",
)
RESORT_FEE_PER_NIGHT = Decimal("35")
LAFAVE_GROUPINGS: tuple[tuple[str, str], ...] = (
    ("Temple of Sinawava", "Suite BIG (1BR/1BA)"),
    ("Sentinel", "Suite BIG (1BR/1BA)"),
    ("Sundial", "Suite BIG (1BR/1BA)"),
    ("Watchman", "Suite SMALL (1BR/1BA)"),
    ("Zion", "Suite SMALL (1BR/1BA)"),
    ("Emerald Pools", "Villa Game (2BR/2BA)"),
    ("Subway", "Villa Game (2BR/2BA)"),
    ("Meridian Tower", "Villa Game (2BR/2BA)"),
    ("Lava Point", "Villa Game (2BR/2BA)"),
    ("Checkerboard Mesa", "Premium Villa (2BR/2BA)"),
    ("Echo Canyon", "Premium Villa (2BR/2BA)"),
    ("Mountain of the Sun", "Premium Villa (2BR/2BA)"),
    ("Mystery Falls", "Premium Villa (2BR/2BA)"),
    ("Big Springs", "Premium Villa (2BR/2BA)"),
    ("East Temple", "Premium Villa (2BR/2BA)"),
    ("Phantom Valley", "Premium Villa (2BR/2BA)"),
    ("Pine Creek", "Premium Villa (2BR/2BA)"),
    ("Hidden Canyon", "Deluxe Villa (2BR/1BA)"),
    ("Kolob Arch", "Deluxe Villa (2BR/1BA)"),
    ("Northgate Peaks", "Deluxe Villa (2BR/1BA)"),
    ("Orderville Canyon", "Deluxe Villa (2BR/1BA)"),
    ("Kayenta", "Deluxe Villa (2BR/1BA)"),
    ("Mount Kinesava", "Deluxe Villa (2BR/1BA)"),
    ("Weeping Rock", "Deluxe Villa (2BR/1BA)"),
    ("Angels Landing", "Premier Villa (3BR/3BA)"),
    ("Narrows", "Premier Villa (3BR/3BA)"),
    ("Cathedral Mountain", "Premier Villa (3BR/3BA)"),
    ("Virgin River", "Premier Villa (3BR/3BA)"),
    ("Johnson Mountain", "J.MT. Villa (3BR/2BA)"),
    ("Gallery House", "House (6BR/4BA)"),
)


@dataclass(frozen=True)
class PropertySyncResult:
    folder: str
    property_id: str
    tab: str
    cache_path: Path
    row_count: int
    synced_at: str


@dataclass(frozen=True)
class SyncResult:
    properties: tuple[PropertySyncResult, ...]
    errors: tuple[tuple[str, str], ...]


def sheets_cache_path(property_config: PropertyConfig, *, data_root: Path | str = "data") -> Path:
    root = Path(data_root)
    return root / ".cache" / "sheets" / f"{property_config.folder.lower()}.csv"


def sheets_meta_path(property_config: PropertyConfig, *, data_root: Path | str = "data") -> Path:
    root = Path(data_root)
    return root / ".cache" / "sheets" / f"{property_config.folder.lower()}.meta.json"


def get_spreadsheet_id() -> str:
    spreadsheet_id = os.environ.get(SPREADSHEET_ID_ENV, "").strip()
    if not spreadsheet_id:
        raise ValueError(
            f"Missing {SPREADSHEET_ID_ENV} environment variable for Google Sheets sync."
        )
    return spreadsheet_id


def _require_gspread():
    try:
        import gspread
    except ImportError as exc:
        raise ImportError(
            "Google Sheets sync requires gspread and google-auth. "
            "Install with: pip install gspread google-auth"
        ) from exc
    return gspread


def _open_spreadsheet():
    gspread = _require_gspread()
    from google.oauth2.service_account import Credentials

    credentials_path = os.environ.get(CREDENTIALS_ENV, "").strip()
    if not credentials_path:
        raise ValueError(
            f"Missing {CREDENTIALS_ENV} environment variable pointing to service account JSON."
        )
    creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(get_spreadsheet_id())


def _open_worksheet(tab_name: str):
    spreadsheet = _open_spreadsheet()
    try:
        return spreadsheet.worksheet(tab_name)
    except Exception as exc:
        raise ValueError(f"Worksheet tab not found: {tab_name}") from exc


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _reservation_key(value: str | None) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _sheet_date_to_iso(value: str | None) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    for pattern in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
    ):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            continue
    try:
        serial = Decimal(raw)
    except InvalidOperation:
        return raw
    base = datetime(1899, 12, 30)
    whole_days = int(serial)
    return (base + timedelta(days=whole_days)).date().isoformat()


def _decimal_string(value: str | None) -> str:
    raw = _clean(value).replace("$", "").replace(",", "")
    if not raw:
        return ""
    try:
        return format(Decimal(raw), "f")
    except InvalidOperation:
        return raw


def _integer_string(value: str | None) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        return str(int(Decimal(raw)))
    except InvalidOperation:
        return raw


def _decimal_or_zero(value: str | None) -> Decimal:
    raw = _decimal_string(value)
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def _nights_decimal(value: str | None) -> Decimal:
    raw = _integer_string(value)
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def _fbg_wmb_room_revenue(row: dict[str, str], *, waive_resort_fee_for_airbnb: bool) -> str:
    accommodation = _decimal_or_zero(row.get("Accommodation Total"))
    nights = _nights_decimal(row.get("Nights"))
    source = _clean(row.get("Source"))
    resort_fee = RESORT_FEE_PER_NIGHT * nights
    if waive_resort_fee_for_airbnb and source.lower() == "airbnb":
        resort_fee = Decimal("0")
    return format(accommodation + resort_fee, "f")


def _normalize_flohom_channel(channel: str) -> str:
    raw = channel.strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered == "airbnbofficial":
        return "Airbnb"
    if lowered == "vrbo":
        return "VRBO"
    if lowered in {"direct", "bookingengine", "google"}:
        return "Direct"
    return "Other"


def _normalize_atx_channel(channel: str) -> str:
    raw = channel.strip()
    if raw.lower() == "airbnbofficial":
        return "Airbnb"
    return raw


def fetch_tab_rows(tab_name: str) -> list[dict[str, str]]:
    worksheet = _open_worksheet(tab_name)
    values = worksheet.get_all_values()
    if not values:
        return []
    headers = [header.strip() for header in values[0]]
    if not any(headers):
        raise ValueError(f"Worksheet '{tab_name}' has no header row.")

    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        if not any(cell.strip() for cell in raw_row):
            continue
        row = {
            headers[index]: (raw_row[index] if index < len(raw_row) else "")
            for index in range(len(headers))
            if headers[index]
        }
        rows.append(row)
    return rows


def _rows_by_key(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        normalized = _reservation_key(row.get(key))
        if normalized:
            lookup[normalized] = row
    return lookup


def _canonical_row(
    *,
    booking_date: str,
    reservation_date: str,
    check_in_date: str,
    check_out_date: str,
    room_revenue: str,
    room_nights: str,
    listing_name: str = "",
    channel: str = "",
    status: str = "",
    grouping: str = "",
) -> dict[str, str]:
    return {
        "booking_date": booking_date,
        "reservation_date": reservation_date or booking_date,
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "room_revenue": room_revenue,
        "room_nights": room_nights,
        "listing_name": listing_name,
        "channel": channel,
        "status": status,
        "grouping": grouping,
    }


def _normalize_fbg_or_wmb_row(
    row: dict[str, str],
    *,
    waive_resort_fee_for_airbnb: bool,
) -> dict[str, str]:
    return _canonical_row(
        booking_date=_sheet_date_to_iso(row.get("Reservation Date")),
        reservation_date=_sheet_date_to_iso(row.get("Reservation Date")),
        check_in_date=_sheet_date_to_iso(row.get("Check in Date")),
        check_out_date=_sheet_date_to_iso(row.get("Check out Date")),
        room_revenue=_fbg_wmb_room_revenue(
            row,
            waive_resort_fee_for_airbnb=waive_resort_fee_for_airbnb,
        ),
        room_nights=_integer_string(row.get("Nights")),
        listing_name=_clean(row.get("Room Type")),
        channel=_clean(row.get("Source")),
        status=_clean(row.get("Status")),
    )


def _normalize_fbg(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        _normalize_fbg_or_wmb_row(row, waive_resort_fee_for_airbnb=True)
        for row in rows
    ]


def _normalize_wmb(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        _normalize_fbg_or_wmb_row(row, waive_resort_fee_for_airbnb=False)
        for row in rows
    ]


def _normalize_hostaway_rows(
    rows: list[dict[str, str]],
    *,
    normalize_channel,
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        normalized.append(
            _canonical_row(
                booking_date=_sheet_date_to_iso(row.get("Reservation date")),
                reservation_date=_sheet_date_to_iso(row.get("Reservation date")),
                check_in_date=_sheet_date_to_iso(row.get("Check-in date")),
                check_out_date=_sheet_date_to_iso(row.get("Check-out date")),
                room_revenue=_decimal_string(row.get("rentalRevenue")),
                room_nights=_integer_string(row.get("Nights")),
                listing_name=_clean(row.get("Listing")),
                channel=normalize_channel(_clean(row.get("Channel"))),
                status=_clean(row.get("Reservation status")),
                grouping=_clean(row.get("Grouping")),
            )
            | {"payment_status": _clean(row.get("Payment status"))}
        )
    return normalized


def _lafave_grouping(listing_name: str) -> str:
    for needle, grouping in LAFAVE_GROUPINGS:
        if needle.lower() in listing_name.lower():
            return grouping
    return ""


def _lafave_channel(
    row: dict[str, str],
    *,
    ota_lookup: dict[str, dict[str, str]],
    reservation_id: str,
) -> str:
    by_phone = _clean(row.get("By Phone")).upper() == "X"
    booked_online = _clean(row.get("Booked Online")).upper() == "X"
    if by_phone or booked_online:
        return "Direct"
    if _clean(row.get("3rd Party")):
        ota_row = ota_lookup.get(reservation_id)
        if ota_row:
            return _clean(ota_row.get("Channel Name")) or "Other"
    return "Other"


def _normalize_lafave(rows_by_tab: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    base_rows = rows_by_tab.get("Lafave_data", [])
    helper_rows = rows_by_tab.get("Lafave_data2", [])
    ota_rows = rows_by_tab.get("Lafave_OTA_data", [])

    listing_lookup = _rows_by_key(helper_rows, "Res#")
    ota_lookup = _rows_by_key(ota_rows, "Reservation")
    normalized: list[dict[str, str]] = []

    for row in base_rows:
        reservation_id = _reservation_key(row.get("Res#"))
        helper_row = listing_lookup.get(reservation_id, {})
        listing_name = _clean(helper_row.get("Unit"))
        booking_date = _sheet_date_to_iso(row.get("Date & Time"))
        normalized.append(
            _canonical_row(
                booking_date=booking_date,
                reservation_date=booking_date,
                check_in_date=_sheet_date_to_iso(row.get("Arrival")),
                check_out_date=_sheet_date_to_iso(row.get("Departure")),
                room_revenue=_decimal_string(row.get("Amount")),
                room_nights=_integer_string(row.get("# Nights")),
                listing_name=listing_name,
                channel=_lafave_channel(row, ota_lookup=ota_lookup, reservation_id=reservation_id),
                status="confirmed",
                grouping=_lafave_grouping(listing_name),
            )
        )
    return normalized


def _tabs_for_property(property_config: PropertyConfig) -> tuple[str, ...]:
    folder = property_config.folder.lower()
    if folder == "lafave":
        return ("Lafave_data", "Lafave_data2", "Lafave_OTA_data")
    return (property_config.sheets_tab_name(),)


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


def _record_to_cache_row(record: BookingRecord) -> dict[str, str]:
    return _canonical_row(
        booking_date=record.booking_date.isoformat(),
        reservation_date=record.reservation_date.isoformat(),
        check_in_date=record.check_in_date.isoformat(),
        check_out_date=record.check_out_date.isoformat(),
        room_revenue=format(record.room_revenue, "f"),
        room_nights=str(record.room_nights),
        listing_name=record.listing_name,
        channel=record.channel,
        status=record.status,
        grouping=record.grouping,
    )


def _load_legacy_cache_rows(
    property_config: PropertyConfig,
    *,
    data_root: Path | str = "data",
) -> list[dict[str, str]]:
    if property_config.data_source is None or not property_config.data_source.legacy_sources:
        return []

    data_dir = Path(data_root) / property_config.folder
    merged: list[dict[str, str]] = []
    for source in property_config.data_source.legacy_sources:
        source_path = data_dir / source.file
        if not source_path.is_file():
            raise ValueError(f"Legacy source file not found: {source_path}")
        pms_profile = load_pms_profile(source.pms)
        records, _ = read_bookings_csv(
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
        merged.extend(_record_to_cache_row(record) for record in records)
    return merged


def normalize_property_rows(
    property_config: PropertyConfig,
    rows_by_tab: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    folder = property_config.folder.lower()
    if folder == "lafave":
        return _normalize_lafave(rows_by_tab)
    if folder == "onera":
        return _normalize_fbg(rows_by_tab[property_config.sheets_tab_name()])
    if folder == "wmb":
        return _normalize_wmb(rows_by_tab[property_config.sheets_tab_name()])
    if folder == "flohom":
        return _normalize_hostaway_rows(
            rows_by_tab[property_config.sheets_tab_name()],
            normalize_channel=_normalize_flohom_channel,
        )
    if folder == "atx":
        return _normalize_hostaway_rows(
            rows_by_tab[property_config.sheets_tab_name()],
            normalize_channel=_normalize_atx_channel,
        )
    raise ValueError(f"Unsupported Google Sheets property mapping: {property_config.folder}")


def write_cache_csv(path: Path, rows: list[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CANONICAL_HEADERS) + ["payment_status"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return len(rows)


def write_sync_metadata(
    meta_path: Path,
    *,
    tab: str,
    row_count: int,
    synced_at: str,
    legacy_row_count: int | None = None,
    sheets_row_count: int | None = None,
) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "tab": tab,
        "row_count": row_count,
        "synced_at": synced_at,
    }
    if legacy_row_count is not None:
        payload["legacy_row_count"] = legacy_row_count
    if sheets_row_count is not None:
        payload["sheets_row_count"] = sheets_row_count
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_sync_metadata(meta_path: Path) -> dict | None:
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def sync_property_tab(
    property_config: PropertyConfig,
    *,
    data_root: Path | str = "data",
) -> PropertySyncResult:
    tab_names = _tabs_for_property(property_config)
    rows_by_tab = {tab_name: fetch_tab_rows(tab_name) for tab_name in tab_names}
    sheets_rows = normalize_property_rows(property_config, rows_by_tab)
    legacy_rows = _load_legacy_cache_rows(property_config, data_root=data_root)
    normalized_rows = legacy_rows + sheets_rows
    cache_path = sheets_cache_path(property_config, data_root=data_root)
    row_count = write_cache_csv(cache_path, normalized_rows)
    synced_at = datetime.now(timezone.utc).isoformat()
    write_sync_metadata(
        sheets_meta_path(property_config, data_root=data_root),
        tab=property_config.sheets_tab_name(),
        row_count=row_count,
        synced_at=synced_at,
        legacy_row_count=len(legacy_rows),
        sheets_row_count=len(sheets_rows),
    )
    return PropertySyncResult(
        folder=property_config.folder,
        property_id=property_config.property_id,
        tab=property_config.sheets_tab_name(),
        cache_path=cache_path,
        row_count=row_count,
        synced_at=synced_at,
    )


def sync_all_properties(
    *,
    data_root: Path | str = "data",
    property_folder: str | None = None,
) -> SyncResult:
    configs = list_property_configs()
    if property_folder:
        normalized = property_folder.strip().lower()
        configs = [
            config
            for config in configs
            if config.folder.lower() == normalized or config.property_id.lower() == normalized
        ]
        if not configs:
            raise ValueError(f"No property config found for: {property_folder}")

    results: list[PropertySyncResult] = []
    errors: list[tuple[str, str]] = []
    for config in configs:
        if config.data_source is None or not config.data_source.is_google_sheets:
            continue
        try:
            results.append(sync_property_tab(config, data_root=data_root))
        except Exception as exc:
            errors.append((config.folder, str(exc)))
    return SyncResult(properties=tuple(results), errors=tuple(errors))
