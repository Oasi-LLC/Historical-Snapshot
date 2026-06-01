from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"
PMS_DIR = CONFIG_ROOT / "pms"
PROPERTIES_DIR = CONFIG_ROOT / "properties"

InventoryMode = str  # "manual" | "active_listings"


@dataclass(frozen=True)
class PmsProfile:
    id: str
    column_aliases: dict[str, list[str]]
    defaults: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PmsProfile:
        return cls(
            id=data["id"],
            column_aliases={key: list(values) for key, values in data["column_aliases"].items()},
            defaults=dict(data.get("defaults", {})),
        )


@dataclass(frozen=True)
class PropertyConfig:
    folder: str
    pms: str
    property_id: str
    property_name: str
    inventory_mode: InventoryMode = "manual"
    default_inventory_listings: int | None = None
    exclude_payment_status: frozenset[str] = frozenset()
    defaults: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PropertyConfig:
        excluded = {
            value.strip().lower()
            for value in data.get("exclude_payment_status", [])
            if str(value).strip()
        }
        return cls(
            folder=data["folder"],
            pms=data["pms"],
            property_id=data["property_id"],
            property_name=data["property_name"],
            inventory_mode=data.get("inventory_mode", "manual"),
            default_inventory_listings=data.get("default_inventory_listings"),
            exclude_payment_status=frozenset(excluded),
            defaults=dict(data.get("defaults", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "pms": self.pms,
            "property_id": self.property_id,
            "property_name": self.property_name,
            "inventory_mode": self.inventory_mode,
            "default_inventory_listings": self.default_inventory_listings,
            "exclude_payment_status": sorted(self.exclude_payment_status),
            "defaults": self.defaults,
        }


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
            exclude_payment_status=property_config.exclude_payment_status,
            defaults=property_config.defaults,
        )
    return property_config, pms_profile
