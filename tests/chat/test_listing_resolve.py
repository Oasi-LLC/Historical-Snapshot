from __future__ import annotations

from datetime import date
from decimal import Decimal

from historical_snapshot.chat.parser_rules import _resolve_listing
from historical_snapshot.config import load_property_config
from historical_snapshot.io.postprocess import apply_property_postprocess
from historical_snapshot.models import BookingRecord


def test_wmb_ada_aliases_resolve_to_accessible():
    config = load_property_config("wmb")
    assert _resolve_listing("Greenhouse ADA", config) == "Greenhouse (Accessible)"
    assert _resolve_listing("Spyglass ADA", config) == "Spyglass (Accessible)"


def test_wmb_ada_bookings_are_not_dropped():
    config = load_property_config("wmb")
    record = BookingRecord(
        property_id="WMB",
        property_name="Onera Wimberley",
        listing_name="Greenhouse ADA",
        channel="Expedia (Channel Collect Booking)",
        grouping="",
        reservation_date=date(2026, 7, 21),
        booking_date=date(2026, 7, 21),
        check_in_date=date(2026, 11, 30),
        check_out_date=date(2026, 12, 3),
        room_revenue=Decimal("836.04"),
        room_nights=3,
        status="confirmed",
    )
    kept = apply_property_postprocess([record], config)
    assert len(kept) == 1
    assert kept[0].listing_name == "Greenhouse (Accessible)"
    assert kept[0].grouping == "Greenhouse"


def test_great_lodge_king_room_resolves_without_colon():
    config = load_property_config("onera")
    assert _resolve_listing("great lodge king room", config) == "Great Lodge: King Room"
    assert _resolve_listing("Great Lodge: King Room", config) == "Great Lodge: King Room"


def test_great_lodge_alone_still_resolves_to_parent():
    config = load_property_config("onera")
    assert _resolve_listing("how is great lodge doing", config) == "Great Lodge"


def test_longest_listing_phrase_wins_over_parent_prefix():
    config = load_property_config("onera")
    from historical_snapshot.chat.parser_rules import _resolve_all_listings

    names = _resolve_all_listings(
        "which was better last year: post oak or great lodge king room?",
        config,
    )
    # Both listings; parent "Great Lodge" must not replace King Room.
    assert names == ["Post Oak", "Great Lodge: King Room"]
    assert "Great Lodge" not in names

