from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"
PMS_DIR = CONFIG_ROOT / "pms"
PROPERTIES_DIR = CONFIG_ROOT / "properties"

InventoryMode = str  # "manual"


@dataclass(frozen=True)
class DataSource:
    file: str
    pms: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataSource:
        return cls(file=data["file"], pms=data["pms"])


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
    allowed_listings: tuple[str, ...] = ()
    data_sources: tuple[DataSource, ...] = ()
    allowed_reservation_statuses: tuple[str, ...] = ()
    allowed_payment_statuses: tuple[str, ...] = ()
    default_channel: str = ""
    dedupe_stays: bool = False
    exclude_zero_revenue: bool = False
    excluded_statuses: tuple[str, ...] = ()
    split_multi_listings: bool = False
    date_formats: tuple[str, ...] = ()
    defaults: dict[str, str] = field(default_factory=dict)

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
            allowed_listings=tuple(str(name) for name in data.get("allowed_listings", ())),
            data_sources=tuple(
                DataSource.from_dict(item) for item in data.get("data_sources", ())
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
        if self.allowed_listings:
            payload["allowed_listings"] = list(self.allowed_listings)
        if self.data_sources:
            payload["data_sources"] = [
                {"file": source.file, "pms": source.pms} for source in self.data_sources
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
        return payload


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
            allowed_listings=property_config.allowed_listings,
            data_sources=property_config.data_sources,
            allowed_reservation_statuses=property_config.allowed_reservation_statuses,
            allowed_payment_statuses=property_config.allowed_payment_statuses,
            default_channel=property_config.default_channel,
            dedupe_stays=property_config.dedupe_stays,
            exclude_zero_revenue=property_config.exclude_zero_revenue,
            excluded_statuses=property_config.excluded_statuses,
            split_multi_listings=property_config.split_multi_listings,
            date_formats=property_config.date_formats,
            defaults=property_config.defaults,
        )
    return property_config, pms_profile
