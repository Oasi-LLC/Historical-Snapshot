from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"
PMS_DIR = CONFIG_ROOT / "pms"
PROPERTIES_DIR = CONFIG_ROOT / "properties"

InventoryMode = str  # "manual" | "active_listings" | "live_listings"

_LIVE_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


def parse_config_date(value: str) -> date:
    for pattern in _LIVE_DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {value}")


@dataclass(frozen=True)
class LegacyLocalSource:
    file: str
    pms: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LegacyLocalSource:
        return cls(file=data["file"], pms=data["pms"])


@dataclass(frozen=True)
class PropertyDataSource:
    type: str
    tab: str | None = None
    legacy_sources: tuple[LegacyLocalSource, ...] = ()

    @property
    def is_google_sheets(self) -> bool:
        return self.type == "google_sheets"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PropertyDataSource | None:
        if not data:
            return None
        return cls(
            type=data["type"],
            tab=data.get("tab"),
            legacy_sources=tuple(
                LegacyLocalSource.from_dict(item) for item in data.get("legacy_sources", ())
            ),
        )


@dataclass(frozen=True)
class DataSource:
    file: str
    pms: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataSource:
        return cls(file=data["file"], pms=data["pms"])


@dataclass(frozen=True)
class SourceCutoff:
    file: str
    property_name: str | None = None
    max_reservation_date: date | None = None
    max_check_in_date: date | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceCutoff:
        max_reservation = data.get("max_reservation_date")
        max_check_in = data.get("max_check_in_date")
        return cls(
            file=data["file"],
            property_name=data.get("property_name"),
            max_reservation_date=(
                parse_config_date(max_reservation) if max_reservation else None
            ),
            max_check_in_date=parse_config_date(max_check_in) if max_check_in else None,
        )


@dataclass(frozen=True)
class PmsProfile:
    id: str
    column_aliases: dict[str, list[str]]
    defaults: dict[str, str] = field(default_factory=dict)
    date_formats: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PmsProfile:
        return cls(
            id=data["id"],
            column_aliases={key: list(values) for key, values in data["column_aliases"].items()},
            defaults=dict(data.get("defaults", {})),
            date_formats=tuple(data.get("date_formats", ())),
        )


@dataclass(frozen=True)
class PropertyConfig:
    folder: str
    pms: str
    property_id: str
    property_name: str
    inventory_mode: InventoryMode = "manual"
    default_inventory_listings: int | None = None
    listing_inventory: dict[str, int] = field(default_factory=dict)
    listing_groups: dict[str, str] = field(default_factory=dict)
    grouping_inventory: dict[str, int] = field(default_factory=dict)
    listing_aliases: dict[str, str] = field(default_factory=dict)
    listing_combo_splits: dict[str, list[str]] = field(default_factory=dict)
    listing_live_dates: dict[str, date] = field(default_factory=dict)
    allowed_listings: tuple[str, ...] = ()
    data_sources: tuple[DataSource, ...] = ()
    source_cutoffs: tuple[SourceCutoff, ...] = ()
    allowed_reservation_statuses: tuple[str, ...] = ()
    allowed_payment_statuses: tuple[str, ...] = ()
    default_channel: str = ""
    dedupe_stays: bool = False
    exclude_zero_revenue: bool = False
    excluded_statuses: tuple[str, ...] = ()
    split_multi_listings: bool = False
    date_formats: tuple[str, ...] = ()
    defaults: dict[str, str] = field(default_factory=dict)
    data_source: PropertyDataSource | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropertyConfig:
        excluded = data.get("excluded_statuses", ())
        return cls(
            folder=data["folder"],
            pms=data["pms"],
            property_id=data["property_id"],
            property_name=data["property_name"],
            inventory_mode=data.get("inventory_mode", "manual"),
            default_inventory_listings=data.get("default_inventory_listings"),
            listing_inventory={
                str(key): int(value) for key, value in data.get("listing_inventory", {}).items()
            },
            listing_groups={
                str(key): str(value) for key, value in data.get("listing_groups", {}).items()
            },
            grouping_inventory={
                str(key): int(value) for key, value in data.get("grouping_inventory", {}).items()
            },
            listing_aliases={
                str(key): str(value) for key, value in data.get("listing_aliases", {}).items()
            },
            listing_combo_splits={
                str(key): [str(item) for item in values]
                for key, values in data.get("listing_combo_splits", {}).items()
            },
            listing_live_dates={
                str(key): parse_config_date(value)
                for key, value in data.get("listing_live_dates", {}).items()
            },
            allowed_listings=tuple(str(name) for name in data.get("allowed_listings", ())),
            data_sources=tuple(
                DataSource.from_dict(item) for item in data.get("data_sources", ())
            ),
            source_cutoffs=tuple(
                SourceCutoff.from_dict(item) for item in data.get("source_cutoffs", ())
            ),
            allowed_reservation_statuses=tuple(
                str(status).strip().lower()
                for status in data.get("allowed_reservation_statuses", ())
            ),
            allowed_payment_statuses=tuple(
                str(status).strip().lower()
                for status in data.get("allowed_payment_statuses", ())
            ),
            default_channel=str(data.get("default_channel", "")),
            dedupe_stays=bool(data.get("dedupe_stays", False)),
            exclude_zero_revenue=bool(data.get("exclude_zero_revenue", False)),
            excluded_statuses=tuple(str(status) for status in excluded),
            split_multi_listings=bool(data.get("split_multi_listings", False)),
            date_formats=tuple(data.get("date_formats", ())),
            defaults=dict(data.get("defaults", {})),
            data_source=PropertyDataSource.from_dict(data.get("data_source")),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "folder": self.folder,
            "pms": self.pms,
            "property_id": self.property_id,
            "property_name": self.property_name,
            "inventory_mode": self.inventory_mode,
            "default_inventory_listings": self.default_inventory_listings,
            "defaults": self.defaults,
        }
        if self.listing_inventory:
            payload["listing_inventory"] = dict(self.listing_inventory)
        if self.listing_groups:
            payload["listing_groups"] = dict(self.listing_groups)
        if self.grouping_inventory:
            payload["grouping_inventory"] = dict(self.grouping_inventory)
        if self.listing_aliases:
            payload["listing_aliases"] = dict(self.listing_aliases)
        if self.listing_combo_splits:
            payload["listing_combo_splits"] = {
                key: list(values) for key, values in self.listing_combo_splits.items()
            }
        if self.listing_live_dates:
            payload["listing_live_dates"] = {
                key: value.isoformat() for key, value in self.listing_live_dates.items()
            }
        if self.allowed_listings:
            payload["allowed_listings"] = list(self.allowed_listings)
        if self.data_sources:
            payload["data_sources"] = [
                {"file": source.file, "pms": source.pms} for source in self.data_sources
            ]
        if self.source_cutoffs:
            payload["source_cutoffs"] = [
                {
                    "file": cutoff.file,
                    **(
                        {"property_name": cutoff.property_name}
                        if cutoff.property_name
                        else {}
                    ),
                    **(
                        {"max_reservation_date": cutoff.max_reservation_date.isoformat()}
                        if cutoff.max_reservation_date
                        else {}
                    ),
                    **(
                        {"max_check_in_date": cutoff.max_check_in_date.isoformat()}
                        if cutoff.max_check_in_date
                        else {}
                    ),
                }
                for cutoff in self.source_cutoffs
            ]
        if self.allowed_reservation_statuses:
            payload["allowed_reservation_statuses"] = list(self.allowed_reservation_statuses)
        if self.allowed_payment_statuses:
            payload["allowed_payment_statuses"] = list(self.allowed_payment_statuses)
        if self.default_channel:
            payload["default_channel"] = self.default_channel
        if self.dedupe_stays:
            payload["dedupe_stays"] = True
        if self.exclude_zero_revenue:
            payload["exclude_zero_revenue"] = True
        if self.excluded_statuses:
            payload["excluded_statuses"] = list(self.excluded_statuses)
        if self.split_multi_listings:
            payload["split_multi_listings"] = True
        if self.date_formats:
            payload["date_formats"] = list(self.date_formats)
        if self.data_source:
            payload["data_source"] = {
                "type": self.data_source.type,
                **({"tab": self.data_source.tab} if self.data_source.tab else {}),
                **(
                    {
                        "legacy_sources": [
                            {"file": source.file, "pms": source.pms}
                            for source in self.data_source.legacy_sources
                        ]
                    }
                    if self.data_source.legacy_sources
                    else {}
                ),
            }
        return payload

    def sheets_tab_name(self) -> str:
        if self.data_source and self.data_source.tab:
            return self.data_source.tab
        return self.folder


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_pms_profile(pms_id: str) -> PmsProfile | None:
    path = PMS_DIR / f"{pms_id}.json"
    if not path.is_file():
        return None
    return PmsProfile.from_dict(_read_json(path))


def load_property_config(folder_name: str) -> PropertyConfig | None:
    path = PROPERTIES_DIR / f"{folder_name.lower()}.json"
    if not path.is_file():
        return None
    return PropertyConfig.from_dict(_read_json(path))


def load_property_config_by_id(property_id: str) -> PropertyConfig | None:
    normalized = property_id.strip().lower()
    for path in sorted(PROPERTIES_DIR.glob("*.json")):
        config = PropertyConfig.from_dict(_read_json(path))
        if config.property_id.lower() == normalized or config.folder.lower() == normalized:
            return config
    return None


def list_property_configs() -> list[PropertyConfig]:
    configs: list[PropertyConfig] = []
    for path in sorted(PROPERTIES_DIR.glob("*.json")):
        configs.append(PropertyConfig.from_dict(_read_json(path)))
    return configs


def resolve_property_config(
    csv_path: str | Path,
    *,
    property_id: str | None = None,
    property_name: str | None = None,
) -> tuple[PropertyConfig | None, PmsProfile | None]:
    folder = Path(csv_path).parent.name
    property_config = load_property_config(folder)
    if property_config is None:
        return None, None

    pms_profile = load_pms_profile(property_config.pms)
    if property_id or property_name:
        property_config = PropertyConfig(
            folder=property_config.folder,
            pms=property_config.pms,
            property_id=property_id or property_config.property_id,
            property_name=property_name or property_config.property_name,
            inventory_mode=property_config.inventory_mode,
            default_inventory_listings=property_config.default_inventory_listings,
            listing_inventory=property_config.listing_inventory,
            listing_groups=property_config.listing_groups,
            grouping_inventory=property_config.grouping_inventory,
            listing_aliases=property_config.listing_aliases,
            listing_combo_splits=property_config.listing_combo_splits,
            listing_live_dates=property_config.listing_live_dates,
            allowed_listings=property_config.allowed_listings,
            data_sources=property_config.data_sources,
            source_cutoffs=property_config.source_cutoffs,
            allowed_reservation_statuses=property_config.allowed_reservation_statuses,
            allowed_payment_statuses=property_config.allowed_payment_statuses,
            default_channel=property_config.default_channel,
            dedupe_stays=property_config.dedupe_stays,
            exclude_zero_revenue=property_config.exclude_zero_revenue,
            excluded_statuses=property_config.excluded_statuses,
            split_multi_listings=property_config.split_multi_listings,
            date_formats=property_config.date_formats,
            defaults=property_config.defaults,
            data_source=property_config.data_source,
        )
    return property_config, pms_profile
