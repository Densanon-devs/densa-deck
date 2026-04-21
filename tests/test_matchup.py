"""Tests for matchup simulation and gauntlet."""

import random

from densa_deck.matchup.archetypes import (
    ARCHETYPES,
    ArchetypeName,
    get_archetype,
    get_default_gauntlet,
)
from densa_deck.matchup.gauntlet import run_gauntlet
from densa_deck.matchup.simulator import simulate_matchup
from densa_deck.models import Card, CardLayout, CardTag, Deck, DeckEntry, Format, Zone


def _make_card(name, is_land=False, cmc=0.0, tags=None, power=None, toughness=None, **kw):
    return Card(
        scryfall_id=f"id-{name}",
        oracle_id=f"oracle-{name}",
        name=name,
        layout=CardLayout.NORMAL,
        cmc=cmc,
        is_land=is_land,
        is_creature=power is not None,
        power=power,
        toughness=toughness,
        tags=tags or [],
        **kw,
    )


def _make_entry(name, qty=1, zone=Zone.MAINBOARD, **card_kw):
    card = _make_card(name, **card_kw)
    return DeckEntry(card_name=name, quantity=qty, zone=zone, card=card)


def _make_test_deck():
    """100-card commander deck with reasonable composition."""
    entries = [
        _make_entry("Commander", zone=Zone.COMMANDER, cmc=4, power="4", toughness="4",
                     tags=[CardTag.FINISHER]),
    ]
    for i in range(36):
        entries.append(_make_entry(f"Land{i}", is_land=True))
    for i in range(10):
        entries.append(_make_entry(f"Rock{i}", cmc=2, tags=[CardTag.MANA_ROCK, CardTag.RAMP],
                                   is_artifact=True))
    for i in range(20):
        p = str((i % 4) + 2)
        entries.append(_make_entry(f"Creature{i}", cmc=float(int(p)), power=p, toughness=p,
                                   tags=[CardTag.THREAT]))
    for i in range(5):
        entries.append(_make_entry(f"Removal{i}", cmc=2, tags=[CardTag.TARGETED_REMOVAL],
                                   is_instant=True))
    for i in range(28):
        entries.append(_make_entry(f"Spell{i}", cmc=3, tags=[CardTag.CARD_DRAW]))
    return Deck(name="Test Matchup", format=Format.COMMANDER, entries=entries)


# --- Archetype tests ---


class TestArchetypes:
    def test_all_archetypes_defined(self):
        assert len(ARCHETYPES) >= 10

    def test_get_archetype(self):
        a = get_archetype("aggro")
        assert a is not None
        assert a.name == ArchetypeName.AGGRO

    def test_get_archetype_missing(self):
        assert get_archetype("nonexistent") is None

    def test_default_gauntlet(self):
        gauntlet = get_default_gauntlet()
        assert len(gauntlet) >= 10
        names = [a.name for a in gauntlet]
        assert ArchetypeName.AGGRO in names
        assert ArchetypeName.CONTROL in names
        assert ArchetypeName.COMBO in names

    def test_archetype_profiles_valid(self):
        for arch in ARCHETYPES.values():
            assert arch.clock_turns > 0
            assert arch.damage_per_turn >= 0
            assert 0.0 <= arch.targeted_removal_chance <= 1.0
            assert 0.0 <= arch.counterspell_chance <= 1.0
            assert 0.0 <= arch.wipe_chance <= 1.0
            assert arch.meta_weight > 0


# --- Simulator tests ---


class TestSimulator:
    def test_basic_matchup(self):
        deck = _make_test_deck()
        aggro = get_archetype("aggro")
        result = simulate_matchup(deck, aggro, simulations=100, max_turns=10, seed=42)
        assert result.simulations == 100
        assert result.wins + result.losses == 100
        assert 0 <= result.win_rate <= 1.0
        assert result.avg_turns > 0

    def test_deterministic(self):
        deck = _make_test_deck()
        control = get_archetype("control")
        r1 = simulate_matchup(deck, control, simulations=50, seed=77)
        r2 = simulate_matchup(deck, control, simulations=50, seed=77)
        assert r1.win_rate == r2.win_rate
        assert r1.avg_turns == r2.avg_turns

    def test_vs_aggro_faster_than_control(self):
        """Games vs aggro should end faster than vs control."""
        deck = _make_test_deck()
        aggro = get_archetype("aggro")
        control = get_archetype("control")
        r_aggro = simulate_matchup(deck, aggro, simulations=200, seed=42)
        r_control = simulate_matchup(deck, control, simulations=200, seed=42)
        assert r_aggro.avg_turns <= r_control.avg_turns

    def test_disruption_tracked(self):
        """Control matchup should show more removal/counters than aggro."""
        deck = _make_test_deck()
        aggro = get_archetype("aggro")
        control = get_archetype("control")
        r_aggro = simulate_matchup(deck, aggro, simulations=200, seed=42)
        r_control = simulate_matchup(deck, control, simulations=200, seed=42)
        total_disruption_aggro = r_aggro.avg_permanents_removed + r_aggro.avg_spells_countered
        total_disruption_control = r_control.avg_permanents_removed + r_control.avg_spells_countered
        assert total_disruption_control > total_disruption_aggro

    def test_win_condition_breakdown(self):
        deck = _make_test_deck()
        aggro = get_archetype("aggro")
        result = simulate_matchup(deck, aggro, simulations=200, seed=42)
        total = result.wins_by_damage + result.losses_by_clock + result.losses_by_timeout
        # Should account for all games (some wins may be by timeout)
        assert total <= result.simulations


# --- Gauntlet tests ---


class TestGauntlet:
    def test_basic_gauntlet(self):
        deck = _make_test_deck()
        # Run against just 3 archetypes for speed
        archetypes = [get_archetype(n) for n in ["aggro", "midrange", "control"]]
        report = run_gauntlet(deck, archetypes=archetypes, simulations=100, seed=42)
        assert report.total_games == 300  # 3 * 100
        assert len(report.matchups) == 3
        assert 0 <= report.overall_win_rate <= 1.0
        assert 0 <= report.weighted_win_rate <= 1.0

    def test_gauntlet_best_worst(self):
        deck = _make_test_deck()
        archetypes = [get_archetype(n) for n in ["aggro", "control", "combo"]]
        report = run_gauntlet(deck, archetypes=archetypes, simulations=100, seed=42)
        assert report.best_matchup != ""
        assert report.worst_matchup != ""
        assert report.best_win_rate >= report.worst_win_rate

    def test_gauntlet_scores(self):
        deck = _make_test_deck()
        archetypes = [get_archetype(n) for n in ["aggro", "midrange", "control"]]
        report = run_gauntlet(deck, archetypes=archetypes, simulations=100, seed=42)
        assert 0 <= report.speed_score <= 100
        assert 0 <= report.resilience_score <= 100
        assert 0 <= report.consistency_score <= 100

    def test_gauntlet_deterministic(self):
        deck = _make_test_deck()
        archetypes = [get_archetype("aggro"), get_archetype("midrange")]
        r1 = run_gauntlet(deck, archetypes=archetypes, simulations=50, seed=99)
        r2 = run_gauntlet(deck, archetypes=archetypes, simulations=50, seed=99)
        assert r1.overall_win_rate == r2.overall_win_rate
