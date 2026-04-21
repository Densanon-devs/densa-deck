"""Tests for mana development probability calculator."""

from densa_deck.models import Card, CardLayout, CardTag, Deck, DeckEntry, Format, Zone
from densa_deck.probability.mana_development import analyze_mana_development


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


def _make_commander_deck(land_count: int = 36, ramp_count: int = 10) -> Deck:
    entries = [
        _make_entry("Commander", qty=1, zone=Zone.COMMANDER, cmc=4),
    ]
    for i in range(land_count):
        entries.append(_make_entry(f"Land{i}", qty=1, is_land=True))
    for i in range(ramp_count):
        entries.append(_make_entry(f"Ramp{i}", qty=1, cmc=2, tags=[CardTag.RAMP]))
    filler = 99 - land_count - ramp_count
    for i in range(filler):
        entries.append(_make_entry(f"Spell{i}", qty=1, cmc=3))
    return Deck(name="Test", format=Format.COMMANDER, entries=entries)


def test_basic_counts():
    """Report should correctly count lands and ramp."""
    deck = _make_commander_deck(land_count=36, ramp_count=10)
    report = analyze_mana_development(deck)
    assert report.land_count == 36
    assert report.ramp_count == 10
    assert report.deck_size == 100  # 1 cmdr + 99 mainboard


def test_two_lands_by_t2_reasonable():
    """With 36 lands in 100 cards, 2 lands by T2 should be likely."""
    deck = _make_commander_deck(land_count=36)
    report = analyze_mana_development(deck)
    assert report.two_lands_by_t2 > 0.70


def test_mana_screw_rate_low_with_enough_lands():
    """36 lands should have low screw rate."""
    deck = _make_commander_deck(land_count=36)
    report = analyze_mana_development(deck)
    assert report.mana_screw_rate < 0.15


def test_mana_screw_rate_high_with_few_lands():
    """20 lands should have high screw rate."""
    deck = _make_commander_deck(land_count=20, ramp_count=5)
    report = analyze_mana_development(deck)
    assert report.mana_screw_rate > 0.20


def test_four_mana_by_t4_with_ramp():
    """Ramp should improve four-mana-by-T4 probability."""
    deck_no_ramp = _make_commander_deck(land_count=36, ramp_count=0)
    deck_with_ramp = _make_commander_deck(land_count=36, ramp_count=12)
    r_no = analyze_mana_development(deck_no_ramp)
    r_yes = analyze_mana_development(deck_with_ramp)
    assert r_yes.four_mana_by_t4 >= r_no.four_mana_by_t4


def test_expected_lands_increase_over_turns():
    """Expected lands should monotonically increase."""
    deck = _make_commander_deck(land_count=36)
    report = analyze_mana_development(deck)
    prev = 0
    for turn in range(1, 8):
        curr = report.expected_lands_by_turn[turn]
        assert curr >= prev
        prev = curr


def test_commander_on_curve():
    """Commander on curve should be calculated when commander exists."""
    deck = _make_commander_deck(land_count=36, ramp_count=10)
    report = analyze_mana_development(deck)
    assert report.commander_on_curve > 0
    assert report.commander_cmc == 4.0


def test_sixty_card_deck():
    """Should work for 60-card decks too."""
    entries = []
    for i in range(24):
        entries.append(_make_entry(f"Land{i}", qty=1, is_land=True))
    for i in range(36):
        entries.append(_make_entry(f"Spell{i}", qty=1, cmc=2))
    deck = Deck(name="Modern", format=Format.MODERN, entries=entries)
    report = analyze_mana_development(deck)
    assert report.land_count == 24
    assert report.deck_size == 60
    assert report.two_lands_by_t2 > 0.80
