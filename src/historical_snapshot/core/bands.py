from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    label: str
    min_days: int
    max_days: int | None

    def includes(self, days: int) -> bool:
        if days < self.min_days:
            return False
        if self.max_days is None:
            return True
        return days <= self.max_days


DEFAULT_BANDS = "0,1-3,4-7,8-15,16-30,31-60,61+"


def parse_bands(raw: str | None) -> list[Band]:
    if not raw:
        raw = DEFAULT_BANDS

    bands: list[Band] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if token.endswith("+"):
            min_days = int(token[:-1])
            bands.append(Band(label=token, min_days=min_days, max_days=None))
        elif "-" not in token:
            day = int(token)
            bands.append(Band(label=token, min_days=day, max_days=day))
        else:
            start_raw, end_raw = token.split("-", maxsplit=1)
            start, end = int(start_raw), int(end_raw)
            if end < start:
                raise ValueError(f"Invalid band range: {token}")
            bands.append(Band(label=token, min_days=start, max_days=end))

    if not bands:
        raise ValueError("At least one booking window band is required.")
    return bands


def band_for_days(days: int, bands: list[Band]) -> str:
    if not bands:
        return "unbanded"
    for band in bands:
        if band.includes(days):
            return band.label
    return bands[-1].label


def sort_band_labels(labels: list[str], bands: list[Band]) -> list[str]:
    order = {band.label: index for index, band in enumerate(bands)}
    return sorted(labels, key=lambda label: order.get(label, len(bands)))
