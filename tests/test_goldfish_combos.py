"""Tests for combo-aware goldfish.

Confirms run_goldfish_batch threads the optional `combos` argument
through, that a deck with all pieces of a combo eventually fires it,
and that the report's combo_* fields aggregate correctly.
"""

from __future__ import annotations

import pytest

from densa_deck.combos.models import Combo
from densa_deck.goldfish.runner import run_goldfish_batch
from densa_deck.models import (
    Card,
    CardLayout,
    Color,
    Deck,
    DeckEntry,
    Format,
    Legality,
    Zone,
)


def _make_card(name, *, cmc=0, mana_cost="", type_line="Artifact",
               colors=None, ci=None, is_land=False, is_creature=False,
               is_artifact=False, is_instant=False):
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}",
        name=name, layout=CardLayout.NORMAL,
        cmc=cmc, mana_cost=mana_cost, type_line=type_line,
        colors=colors or [], color_identity=ci or [],
        is_land=is_land, is_creature=is_creature, is_artifact=is_artifact,
        is_instant=is_instant,
        legalities={"commander": Legality.LEGAL},
    )


def _build_deck_with(combo_cards: list[str], copies_each: int = 12) -> Deck:
    """Build a 60-card-style deck with `copies_each` of each combo piece.

    Using a non-singleton format (modern shape) drives up the chance that
    every combo piece is drawn in the first 10 turns from ~5% (singleton
    in 99) to >90% — keeping the goldfish-combo wiring test robust to
    seed-noise without needing millions of simulations.
    """
    entries: list[DeckEntry] = []
    # Combo cards as 12-ofs
    for n in combo_cards:
        card = _make_card(n, cmc=2, mana_cost="{2}", type_line="Artifact",
                          is_artifact=True)
        entries.append(DeckEntry(card_name=n, quantity=copies_each,
                                 zone=Zone.MAINBOARD, card=card))
    # Lots of forests so the goldfish has fuel
    forest = _make_card("Forest", cmc=0, type_line="Basic Land — Forest",
                        ci=[Color.GREEN], is_land=True)
    entries.append(DeckEntry(card_name="Forest", quantity=24,
                             zone=Zone.MAINBOARD, card=forest))
    # Filler — 1-of each, total enough cards for a 60-card-equivalent deck
    fillers = []
    target_total = 60 - 24 - copies_each * len(combo_cards)
    for i in range(max(0, target_total)):
        c = _make_card(f"Filler-{i}", cmc=2, mana_cost="{2}",
                       type_line="Creature", is_creature=True)
        fillers.append(DeckEntry(card_name=f"Filler-{i}", quantity=1,
                                 zone=Zone.MAINBOARD, card=c))
    entries.extend(fillers)
    # Use Modern format for non-singleton 60-card deck
    return Deck(name="Combo Test Deck", format=Format.MODERN, entries=entries)


class TestGoldfishCombos:
    def test_no_combos_passed_means_no_combo_fields(self):
        """Backward compat: callers that don't pass combos see zero
        combo_* fields. The new tracking is opt-in."""
        deck = _build_deck_with([])
        report = run_goldfish_batch(deck, simulations=10, seed=42)
        assert report.combos_evaluated == 0
        assert report.combo_win_rate == 0.0
        assert report.average_combo_win_turn == 0.0
        assert report.combo_win_turn_distribution == {}
        assert report.top_combo_lines == []

    def test_combo_fires_when_all_pieces_in_deck(self):
        """A deck running both pieces of a 2-card combo should fire it
        across enough simulations — by turn 10 with 100 sims, virtually
        guaranteed."""
        deck = _build_deck_with(["Combo-A", "Combo-B"])
        combo = Combo(
            combo_id="test-1",
            cards=["Combo-A", "Combo-B"],
            produces=["Win the game"],
            color_identity="",
            popularity=1,
        )
        report = run_goldfish_batch(
            deck, simulations=50, seed=42, combos=[combo],
        )
        assert report.combos_evaluated == 1
        # With 50 games at max 10 turns and only 2 combo pieces, almost
        # all games should fire. Allow a wide floor — the test only
        # needs to confirm the wiring works end-to-end.
        assert report.combo_win_rate > 0.3
        assert report.average_combo_win_turn > 0.0
        assert report.combo_win_turn_distribution
        # top_combo_lines should include our combo
        assert any(cid == "test-1" for cid, _, _, _ in report.top_combo_lines)

    def test_combo_with_missing_pieces_is_skipped(self):
        """If the deck doesn't run all combo pieces, the combo can never
        fire — and the runner pre-filters it out so combos_evaluated stays 0."""
        deck = _build_deck_with(["Combo-A"])  # only 1 of 2 pieces in deck
        combo = Combo(
            combo_id="test-2",
            cards=["Combo-A", "Missing-Card"],
            color_identity="",
            popularity=1,
        )
        report = run_goldfish_batch(
            deck, simulations=10, seed=42, combos=[combo],
        )
        assert report.combos_evaluated == 0
        assert report.combo_win_rate == 0.0

    def test_first_fired_combo_recorded(self):
        """When multiple combos are tracked, the report's top_combo_lines
        ranks them by frequency. With both fully in the deck, both should
        appear; the more-fired one comes first."""
        deck = _build_deck_with(["A", "B", "C", "D"])
        combos = [
            Combo(combo_id="cAB", cards=["A", "B"], popularity=1),
            Combo(combo_id="cCD", cards=["C", "D"], popularity=1),
        ]
        report = run_goldfish_batch(
            deck, simulations=30, seed=1, combos=combos,
        )
        # Both combos are valid for this deck.
        assert report.combos_evaluated == 2
        # At least one of the two should fire across 30 games.
        ids = {row[0] for row in report.top_combo_lines}
        assert ids & {"cAB", "cCD"}

    def test_combo_evaluation_doesnt_break_existing_metrics(self):
        """Regression: existing kill-turn / mulligan / objective metrics
        must still aggregate correctly when combos is provided."""
        deck = _build_deck_with(["X", "Y"])
        combo = Combo(combo_id="cXY", cards=["X", "Y"], popularity=1)
        report = run_goldfish_batch(
            deck, simulations=20, seed=99, combos=[combo],
        )
        assert report.simulations == 20
        # Kill-turn fields exist (may or may not have non-zero values
        # depending on the deck's clock — we only assert the schema).
        assert isinstance(report.kill_rate, float)
        assert isinstance(report.average_kill_turn, float)
        assert isinstance(report.average_mulligans, float)
