"""Tests for key card access calculator."""

from densa_deck.models import Card, CardLayout, CardTag, Deck, DeckEntry, Format, Zone
from densa_deck.probability.key_cards import (
    analyze_card_access,
    analyze_package_access,
    analyze_role_access,
)


def _make_card(name: str, is_land: bool = False, cmc: float = 0, tags: list[CardTag] | None = None) -> Card:
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        cmc=cmc,
        is_land=is_land,
        tags=tags or [],
    )


def _make_entry(name: str, qty: int = 1, zone: Zone = Zone.MAINBOARD, **card_kw) -> DeckEntry:
    card = _make_card(name, **card_kw)
    return DeckEntry(card_name=name, quantity=qty, zone=zone, card=card)


def _make_deck() -> Deck:
    entries = []
    # 4 copies of key card
    entries.append(_make_entry("Lightning Bolt", qty=4, cmc=1, tags=[CardTag.TARGETED_REMOVAL]))
    # 1 copy singleton
    entries.append(_make_entry("Sol Ring", qty=1, cmc=1, tags=[CardTag.RAMP, CardTag.MANA_ROCK]))
    # Combo pieces
    entries.append(_make_entry("Combo A", qty=1, cmc=3))
    entries.append(_make_entry("Combo B", qty=1, cmc=4))
    # Card draw
    entries.append(_make_entry("Draw Spell", qty=4, cmc=2, tags=[CardTag.CARD_DRAW]))
    # Filler
    for i in range(20):
        entries.append(_make_entry(f"Land{i}", qty=1, is_land=True))
    for i in range(30):
        entries.append(_make_entry(f"Filler{i}", qty=1, cmc=2))
    return Deck(name="Test", format=Format.MODERN, entries=entries)


def test_card_access_4_copies():
    """4 copies should have high access probability early."""
    deck = _make_deck()
    results = analyze_card_access(deck, card_names=["Lightning Bolt"])
    assert len(results) == 1
    r = results[0]
    assert r.copies_in_deck == 4
    assert r.by_turn[1] > 0.30  # ~39.7% for 4 of 60 in 7 cards
    assert r.by_turn[5] > r.by_turn[1]  # Probability increases over turns


def test_card_access_singleton():
    """1 copy should have low but increasing access."""
    deck = _make_deck()
    results = analyze_card_access(deck, card_names=["Sol Ring"])
    assert len(results) == 1
    r = results[0]
    assert r.copies_in_deck == 1
    assert r.by_turn[1] < 0.15  # ~11.7% for 1 of 60 in 7 cards
    assert r.by_turn[10] > r.by_turn[1]


def test_card_access_nonexistent():
    """Searching for a card not in deck returns empty."""
    deck = _make_deck()
    results = analyze_card_access(deck, card_names=["Nonexistent Card"])
    assert len(results) == 0


def test_card_access_monotonic():
    """Probabilities should only increase with more turns."""
    deck = _make_deck()
    results = analyze_card_access(deck, card_names=["Lightning Bolt"])
    r = results[0]
    prev = 0
    for t in range(1, 11):
        curr = r.by_turn[t]
        assert curr >= prev
        prev = curr


def test_role_access():
    """Role access should find removal and draw."""
    deck = _make_deck()
    results = analyze_role_access(deck, roles=[CardTag.TARGETED_REMOVAL, CardTag.CARD_DRAW])
    assert len(results) == 2
    removal = next(r for r in results if r.role == "targeted_removal")
    assert removal.total_in_deck == 4
    draw = next(r for r in results if r.role == "card_draw")
    assert draw.total_in_deck == 4


def test_role_access_probabilities_increase():
    """Role access probabilities should increase each turn."""
    deck = _make_deck()
    results = analyze_role_access(deck, roles=[CardTag.RAMP])
    assert len(results) == 1
    r = results[0]
    prev = 0
    for t in range(1, 11):
        curr = r.by_turn[t]
        assert curr >= prev
        prev = curr


def test_package_access_two_piece_combo():
    """Two-piece combo with 1 copy each should be low probability early."""
    deck = _make_deck()
    results = analyze_package_access(
        deck,
        packages={"Test Combo": ["Combo A", "Combo B"]},
        simulations=5000,
        seed=42,
    )
    assert len(results) == 1
    r = results[0]
    assert r.by_turn[1] < 0.05  # Very unlikely T1
    assert r.by_turn[10] > r.by_turn[1]  # Increases over time


def test_package_access_deterministic():
    """Package access with seed should be deterministic."""
    deck = _make_deck()
    r1 = analyze_package_access(
        deck, packages={"C": ["Combo A", "Combo B"]}, simulations=1000, seed=99,
    )
    r2 = analyze_package_access(
        deck, packages={"C": ["Combo A", "Combo B"]}, simulations=1000, seed=99,
    )
    assert r1[0].by_turn == r2[0].by_turn
