from __future__ import annotations

from historical_snapshot.chat.parser_rules import _resolve_listing
from historical_snapshot.config import load_property_config


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

