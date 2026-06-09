"""Pricing module tests — deck value rollup, over-budget flagging,
TCGPlayer URL shape, and the JSON shape exposed by value_to_dict.
"""

from __future__ import annotations

import os

import pytest

from densa_deck.analysis.pricing import (
    CardPriceLine,
    DeckValue,
    compute_deck_value,
    tcgplayer_search_url,
    value_to_dict,
)
from densa_deck.models import Card, CardLayout, Deck, DeckEntry, Zone


def _card(name: str, price: float | None) -> Card:
    """Minimal Card with just enough to feed the pricer."""
    return Card(
        scryfall_id="00000000-0000-0000-0000-000000000000",
        oracle_id="00000000-0000-0000-0000-000000000001",
        name=name,
        layout=CardLayout.NORMAL,
        price_usd=price,
    )


def _deck(*entries: DeckEntry) -> Deck:
    return Deck(name="Test", entries=list(entries))


# ----------------------------------------------------- compute_deck_value


def test_total_sums_priced_cards():
    deck = _deck(
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
        DeckEntry(card_name="Mana Vault", quantity=1, zone=Zone.MAINBOARD, card=_card("Mana Vault", 17.0)),
    )
    v = compute_deck_value(deck)
    assert v.total_known_usd == pytest.approx(19.5)
    assert v.unpriced_count == 0


def test_unpriced_cards_counted_not_zeroed():
    """Cards Scryfall has no price for must be reported, never silently $0."""
    deck = _deck(
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
        DeckEntry(card_name="Unreleased", quantity=2, zone=Zone.MAINBOARD, card=_card("Unreleased", None)),
    )
    v = compute_deck_value(deck)
    assert v.total_known_usd == pytest.approx(2.5)
    assert v.unpriced_count == 2  # quantity, not entry count


def test_quantity_multiplies_line_total():
    deck = _deck(
        DeckEntry(card_name="Plains", quantity=10, zone=Zone.MAINBOARD, card=_card("Plains", 0.10)),
    )
    v = compute_deck_value(deck)
    assert v.total_known_usd == pytest.approx(1.0)
    assert v.lines[0].line_total == pytest.approx(1.0)


def test_sideboard_and_maybeboard_excluded_by_default():
    """Sideboard/maybeboard shouldn't inflate the headline number."""
    deck = _deck(
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
        DeckEntry(card_name="Mana Crypt", quantity=1, zone=Zone.SIDEBOARD, card=_card("Mana Crypt", 200.0)),
        DeckEntry(card_name="Force of Will", quantity=1, zone=Zone.MAYBEBOARD, card=_card("Force of Will", 80.0)),
    )
    v = compute_deck_value(deck)
    assert v.total_known_usd == pytest.approx(2.5)


def test_commander_included_by_default():
    deck = _deck(
        DeckEntry(card_name="Atraxa", quantity=1, zone=Zone.COMMANDER, card=_card("Atraxa", 15.0)),
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
    )
    v = compute_deck_value(deck)
    assert v.total_known_usd == pytest.approx(17.5)


def test_priciest_sorted_descending():
    deck = _deck(
        DeckEntry(card_name="A", quantity=1, zone=Zone.MAINBOARD, card=_card("A", 1.0)),
        DeckEntry(card_name="B", quantity=1, zone=Zone.MAINBOARD, card=_card("B", 50.0)),
        DeckEntry(card_name="C", quantity=1, zone=Zone.MAINBOARD, card=_card("C", 10.0)),
    )
    v = compute_deck_value(deck, top_n=2)
    assert [l.name for l in v.priciest] == ["B", "C"]


def test_priciest_uses_line_total_not_unit_price():
    """4x cheap-bulk should beat 1x mid-tier when line_total is larger."""
    deck = _deck(
        DeckEntry(card_name="Bulk", quantity=10, zone=Zone.MAINBOARD, card=_card("Bulk", 5.0)),  # $50
        DeckEntry(card_name="Single", quantity=1, zone=Zone.MAINBOARD, card=_card("Single", 30.0)),
    )
    v = compute_deck_value(deck, top_n=1)
    assert v.priciest[0].name == "Bulk"


def test_over_budget_flags_per_card():
    deck = _deck(
        DeckEntry(card_name="Cheap", quantity=1, zone=Zone.MAINBOARD, card=_card("Cheap", 2.0)),
        DeckEntry(card_name="Mid", quantity=1, zone=Zone.MAINBOARD, card=_card("Mid", 12.0)),
        DeckEntry(card_name="Spicy", quantity=1, zone=Zone.MAINBOARD, card=_card("Spicy", 80.0)),
    )
    v = compute_deck_value(deck, budget_per_card_usd=10.0)
    names = [l.name for l in v.over_budget]
    assert "Spicy" in names
    assert "Mid" in names
    assert "Cheap" not in names


def test_over_budget_empty_when_no_budget():
    deck = _deck(
        DeckEntry(card_name="Spicy", quantity=1, zone=Zone.MAINBOARD, card=_card("Spicy", 80.0)),
    )
    v = compute_deck_value(deck)
    assert v.over_budget == []


def test_unresolved_entry_counted_as_unpriced():
    """A parsed entry that never resolved against the card DB still counts
    its quantity into `unpriced_count` — the user needs to know the total
    is incomplete."""
    deck = _deck(
        DeckEntry(card_name="Mystery", quantity=3, zone=Zone.MAINBOARD, card=None),
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
    )
    v = compute_deck_value(deck)
    assert v.total_known_usd == pytest.approx(2.5)
    assert v.unpriced_count == 3


def test_zones_included_reported():
    deck = _deck(
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
    )
    v = compute_deck_value(deck)
    assert "mainboard" in v.zones_included
    assert "commander" in v.zones_included


# --------------------------------------------------- tcgplayer_search_url


def test_tcgplayer_url_basic_shape():
    url = tcgplayer_search_url("Sol Ring")
    assert url.startswith("https://www.tcgplayer.com/search/magic/product")
    assert "Sol+Ring" in url
    assert "productLineName=magic" in url


def test_tcgplayer_url_handles_special_chars():
    """Names with apostrophes / commas / slashes (split cards) must encode."""
    url = tcgplayer_search_url("Jace, the Mind Sculptor")
    assert "%2C" in url  # the comma
    url = tcgplayer_search_url("Fire // Ice")
    assert "%2F" in url  # the slash


def test_tcgplayer_url_empty_name_returns_base():
    """Guard against UI passing an empty string."""
    url = tcgplayer_search_url("")
    assert url == "https://www.tcgplayer.com/search/magic/product"


def test_tcgplayer_url_includes_partner_when_env_set(monkeypatch):
    monkeypatch.setenv("DENSA_TCGPLAYER_PARTNER", "densanon")
    url = tcgplayer_search_url("Sol Ring")
    assert "partner=densanon" in url


def test_tcgplayer_url_omits_partner_when_env_blank(monkeypatch):
    monkeypatch.setenv("DENSA_TCGPLAYER_PARTNER", "   ")
    url = tcgplayer_search_url("Sol Ring")
    assert "partner=" not in url


# ----------------------------------------------------------- value_to_dict


def test_value_to_dict_json_shape():
    deck = _deck(
        DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD, card=_card("Sol Ring", 2.5)),
        DeckEntry(card_name="Mana Vault", quantity=1, zone=Zone.MAINBOARD, card=_card("Mana Vault", 17.0)),
    )
    v = compute_deck_value(deck, budget_per_card_usd=10.0)
    d = value_to_dict(v)
    assert d["total_known_usd"] == pytest.approx(19.5)
    assert d["unpriced_count"] == 0
    assert isinstance(d["priciest"], list)
    assert d["priciest"][0]["name"] == "Mana Vault"
    assert d["priciest"][0]["tcgplayer_url"].startswith("https://www.tcgplayer.com/")
    # Mana Vault > $10 — should be flagged over budget.
    over_names = [c["name"] for c in d["over_budget"]]
    assert "Mana Vault" in over_names
    assert "Sol Ring" not in over_names


def test_empty_deck_returns_zero_value():
    """No entries → zero total, zero unpriced, empty lists. Never raise."""
    deck = _deck()
    v = compute_deck_value(deck)
    assert v.total_known_usd == 0.0
    assert v.unpriced_count == 0
    assert v.priciest == []
    assert v.lines == []
    assert v.over_budget == []
