"""Tests for opening hand analysis."""

from mtg_deck_engine.models import Card, CardLayout, CardTag, Deck, DeckEntry, Format, Zone
from mtg_deck_engine.probability.opening_hand import (
    HandEvaluation,
    OpenerArchetype,
    evaluate_hand,
    simulate_opening_hands,
)


def _make_card(name: str, is_land: bool = False, cmc: float = 0, tags: list[CardTag] | None = None, **kw) -> Card:
    defaults = {
        "scryfall_id": f"id-{name}",
        "oracle_id": f"oracle-{name}",
        "name": name,
        "layout": CardLayout.NORMAL,
        "cmc": cmc,
        "is_land": is_land,
        "tags": tags or [],
    }
    defaults.update(kw)
    return Card(**defaults)


def _make_entry(name: str, qty: int = 1, zone: Zone = Zone.MAINBOARD, **card_kw) -> DeckEntry:
    card = _make_card(name, **card_kw)
    return DeckEntry(card_name=name, quantity=qty, zone=zone, card=card)


def _make_commander_deck() -> Deck:
    """Build a minimal 100-card commander deck for testing."""
    entries = [
        _make_entry("Commander", qty=1, zone=Zone.COMMANDER, cmc=4, tags=[CardTag.FINISHER]),
        _make_entry("Sol Ring", qty=1, cmc=1, tags=[CardTag.MANA_ROCK, CardTag.RAMP]),
        _make_entry("Counterspell", qty=1, cmc=2, tags=[CardTag.COUNTERSPELL]),
        _make_entry("Swords to Plowshares", qty=1, cmc=1, tags=[CardTag.TARGETED_REMOVAL]),
    ]
    # Fill with lands and spells to reach 99 mainboard
    for i in range(36):
        entries.append(_make_entry(f"Land{i}", qty=1, is_land=True))
    for i in range(60):
        entries.append(_make_entry(f"Spell{i}", qty=1, cmc=float(i % 5 + 1)))
    return Deck(name="Test Commander", format=Format.COMMANDER, entries=entries)


def test_evaluate_hand_balanced():
    """A hand with 3 lands and castable spells should be BALANCED."""
    deck = _make_commander_deck()
    hand = [
        _make_entry("Land0", is_land=True),
        _make_entry("Land1", is_land=True),
        _make_entry("Land2", is_land=True),
        _make_entry("Sol Ring", cmc=1, tags=[CardTag.MANA_ROCK, CardTag.RAMP]),
        _make_entry("Spell0", cmc=1),
        _make_entry("Spell1", cmc=2),
        _make_entry("Spell2", cmc=3),
    ]
    ev = evaluate_hand(hand, deck)
    assert ev.land_count == 3
    assert ev.nonland_count == 4
    assert ev.archetype == OpenerArchetype.BALANCED
    assert ev.keepable is True


def test_evaluate_hand_dead_no_lands():
    """A hand with 0 lands should be DEAD."""
    deck = _make_commander_deck()
    hand = [_make_entry(f"Spell{i}", cmc=float(i + 1)) for i in range(7)]
    ev = evaluate_hand(hand, deck)
    assert ev.land_count == 0
    assert ev.archetype == OpenerArchetype.DEAD
    assert ev.keepable is False


def test_evaluate_hand_dead_flood():
    """A hand with 6+ lands should be DEAD."""
    deck = _make_commander_deck()
    hand = [_make_entry(f"Land{i}", is_land=True) for i in range(6)]
    hand.append(_make_entry("Spell0", cmc=1))
    ev = evaluate_hand(hand, deck)
    assert ev.land_count == 6
    assert ev.archetype == OpenerArchetype.DEAD


def test_evaluate_hand_risky_one_land():
    """A 1-land hand with low-curve spells should be RISKY."""
    deck = _make_commander_deck()
    hand = [
        _make_entry("Land0", is_land=True),
        _make_entry("Spell0", cmc=1),
        _make_entry("Spell1", cmc=1),
        _make_entry("Spell2", cmc=2),
        _make_entry("Spell3", cmc=2),
        _make_entry("Spell4", cmc=2),
        _make_entry("Spell5", cmc=3),
    ]
    ev = evaluate_hand(hand, deck)
    assert ev.land_count == 1
    assert ev.archetype == OpenerArchetype.RISKY


def test_simulate_opening_hands_deterministic():
    """Simulation with seed should produce consistent results."""
    deck = _make_commander_deck()
    r1 = simulate_opening_hands(deck, simulations=500, seed=42)
    r2 = simulate_opening_hands(deck, simulations=500, seed=42)
    assert r1.keep_rate == r2.keep_rate
    assert r1.average_lands == r2.average_lands


def test_simulate_opening_hands_keep_rate_reasonable():
    """A well-built deck should have >60% keep rate."""
    deck = _make_commander_deck()
    report = simulate_opening_hands(deck, simulations=2000, seed=123)
    assert report.keep_rate > 0.5
    assert report.simulations == 2000
    assert 1.0 <= report.average_lands <= 5.0


def test_simulate_opening_hands_archetype_distribution():
    """Archetype distribution should sum to ~1.0."""
    deck = _make_commander_deck()
    report = simulate_opening_hands(deck, simulations=2000, seed=456)
    total = sum(report.archetype_distribution.values())
    assert 0.99 < total < 1.01
