from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from historical_snapshot.config import PropertyConfig
from historical_snapshot.models import BookingRecord


def _split_decimal(total: Decimal, parts: int) -> list[Decimal]:
    if parts <= 1:
        return [total]
    share = (total / Decimal(parts)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amounts = [share] * parts
    remainder = total - sum(amounts)
    if remainder != Decimal("0"):
        amounts[-1] += remainder
    return amounts


def _parse_base_listings(listing_name: str, base_listings: frozenset[str]) -> list[str]:
    parts = [part.strip() for part in listing_name.split(",")]
    return [part for part in parts if part in base_listings]


def _clone_record(record: BookingRecord, **changes: object) -> BookingRecord:
    return BookingRecord(
        property_id=changes.get("property_id", record.property_id),
        property_name=changes.get("property_name", record.property_name),
        listing_name=changes.get("listing_name", record.listing_name),
        channel=changes.get("channel", record.channel),
        grouping=changes.get("grouping", record.grouping),
        reservation_date=changes.get("reservation_date", record.reservation_date),
        booking_date=changes.get("booking_date", record.booking_date),
        check_in_date=changes.get("check_in_date", record.check_in_date),
        check_out_date=changes.get("check_out_date", record.check_out_date),
        room_revenue=changes.get("room_revenue", record.room_revenue),
        room_nights=changes.get("room_nights", record.room_nights),
        status=changes.get("status", record.status),
        available_room_nights=changes.get(
            "available_room_nights", record.available_room_nights
        ),
    )


def _normalized_channel(channel: str) -> str:
    return channel.strip() or "Other"


def _group_for_listing(listing_name: str, listing_groups: dict[str, str]) -> str:
    if listing_name in listing_groups:
        return listing_groups[listing_name]
    if listing_name.startswith("Greenhouse"):
        return "Greenhouse"
    if listing_name.startswith("Spyglass"):
        return "Spyglass"
    return ""


def _apply_listing_groups(
    records: list[BookingRecord],
    listing_groups: dict[str, str],
) -> list[BookingRecord]:
    if not listing_groups:
        return records
    grouped: list[BookingRecord] = []
    for record in records:
        grouping = _group_for_listing(record.listing_name, listing_groups)
        if grouping:
            grouped.append(_clone_record(record, grouping=grouping))
        else:
            grouped.append(record)
    return grouped


def _canonical_listing(listing_name: str, listing_aliases: dict[str, str]) -> str:
    return listing_aliases.get(listing_name, listing_name)


def _dedupe_stay_records(records: list[BookingRecord]) -> list[BookingRecord]:
    best: dict[tuple[str, object, object], BookingRecord] = {}
    for record in records:
        key = (record.listing_name, record.check_in_date, record.check_out_date)
        current = best.get(key)
        if current is None or record.room_revenue > current.room_revenue:
            best[key] = record
    return list(best.values())


def apply_property_postprocess(
    records: list[BookingRecord],
    property_config: PropertyConfig | None,
) -> list[BookingRecord]:
    if property_config is None:
        return records

    excluded = {status.strip().lower() for status in property_config.excluded_statuses}
    base_listings = frozenset(property_config.listing_inventory.keys())
    allowed_listings = frozenset(property_config.allowed_listings)

    filtered: list[BookingRecord] = []
    for record in records:
        if excluded and record.status in excluded:
            continue
        if property_config.exclude_zero_revenue and record.room_revenue <= Decimal("0"):
            continue
        listing_name = _canonical_listing(record.listing_name, property_config.listing_aliases)
        channel = property_config.default_channel or _normalized_channel(record.channel)
        filtered.append(_clone_record(record, listing_name=listing_name, channel=channel))

    if allowed_listings:
        filtered = [record for record in filtered if record.listing_name in allowed_listings]

    if property_config.dedupe_stays:
        filtered = _dedupe_stay_records(filtered)

    if property_config.split_multi_listings and base_listings:
        split: list[BookingRecord] = []
        for record in filtered:
            channel = _normalized_channel(record.channel)
            listings = _parse_base_listings(record.listing_name, base_listings)
            if len(listings) <= 1:
                listing_name = listings[0] if listings else record.listing_name
                split.append(_clone_record(record, listing_name=listing_name, channel=channel))
                continue

            revenue_shares = _split_decimal(record.room_revenue, len(listings))
            for listing_name, revenue in zip(listings, revenue_shares):
                split.append(
                    _clone_record(
                        record,
                        listing_name=listing_name,
                        channel=channel,
                        room_revenue=revenue,
                    )
                )
        filtered = split
    elif property_config.listing_groups or property_config.exclude_zero_revenue or excluded:
        filtered = [
            _clone_record(record, channel=_normalized_channel(record.channel))
            for record in filtered
        ]

    return _apply_listing_groups(filtered, property_config.listing_groups)
