"""Tests for the second-wave combo integrations:

- power_level reweights when detected_combo_count is non-zero
- detect_archetype overrides to "combo" when 2+ combos present
- mulligan_phase preserves combo pieces during bottoming
- build_deck_sheet surfaces a [COMBOS] block when combo_lines is non-empty
- diff_combos returns gained / lost / still_present buckets
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.analysis.power_level import estimate_power_level
from densa_deck.analyst.coach import build_deck_sheet
from densa_deck.analyst.prompts import executive_summary_prompt
from densa_deck.combos import Combo, ComboStore, diff_combos
from densa_deck.formats.profiles import detect_archetype
from densa_deck.goldfish.mulligan import _bottom_cards
from densa_deck.goldfish.state import GameState
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


def _mk(name, **kw):
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}",
        name=name, layout=CardLayout.NORMAL,
        cmc=kw.get("cmc", 0), mana_cost=kw.get("mc", ""),
        type_line=kw.get("tl", "Artifact"),
        colors=kw.get("cols", []), color_identity=kw.get("ci", []),
        is_land=kw.get("is_land", False),
        is_creature=kw.get("is_creature", False),
        is_artifact=kw.get("is_artifact", False),
        is_instant=kw.get("is_instant", False),
        legalities={"commander": Legality.LEGAL},
    )


def _build_deck(cards: list[Card], format_=Format.COMMANDER) -> Deck:
    entries = [
        DeckEntry(card_name=c.name, quantity=1, zone=Zone.MAINBOARD, card=c)
        for c in cards
    ]
    forest = _mk("Forest", tl="Basic Land", ci=[Color.GREEN], is_land=True)
    entries.append(DeckEntry(card_name="Forest", quantity=35,
                             zone=Zone.MAINBOARD, card=forest))
    return Deck(name="Test", format=format_, entries=entries)


# ---------------------------------------------------------------- power level


class TestComboAwarePowerLevel:
    def test_no_combos_means_unchanged_score(self):
        """Backward compat: omitting the new kwargs gives the same result
        as the pre-combo-aware version."""
        deck = _build_deck([_mk("X", cmc=2, is_creature=True),
                            _mk("Y", cmc=3, is_creature=True)])
        a = estimate_power_level(deck)
        b = estimate_power_level(deck, detected_combo_count=0,
                                 near_miss_combo_count=0)
        assert a.overall == b.overall
        assert a.combo_potential == b.combo_potential
        assert a.win_condition_quality == b.win_condition_quality

    def test_detected_combos_lift_combo_potential(self):
        """Detecting 3+ combo lines saturates combo_potential."""
        deck = _build_deck([_mk("X", cmc=2)])
        baseline = estimate_power_level(deck).combo_potential
        boosted = estimate_power_level(deck, detected_combo_count=3).combo_potential
        assert boosted > baseline
        assert boosted >= 4.0

    def test_detected_combos_lift_win_condition(self):
        """Decks with combo lines should not score 1.5/10 win-condition just
        because Thoracle isn't FINISHER-tagged."""
        deck = _build_deck([_mk("X", cmc=2)])
        baseline = estimate_power_level(deck).win_condition_quality
        boosted = estimate_power_level(deck, detected_combo_count=2).win_condition_quality
        assert boosted > baseline
        assert boosted >= 6.0  # 3.0 + 1.5*2

    def test_near_miss_combos_smaller_lift(self):
        """Near-miss combos contribute, but less than detected ones."""
        deck = _build_deck([_mk("X", cmc=2)])
        detected = estimate_power_level(deck, detected_combo_count=3).combo_potential
        near = estimate_power_level(deck, near_miss_combo_count=4).combo_potential
        assert detected > near


# ---------------------------------------------------------------- archetype


class TestComboAwareArchetype:
    def test_two_combos_overrides_to_combo_archetype(self):
        """A creature-heavy deck with 2+ combos detected reads as COMBO,
        not MIDRANGE."""
        deck = _build_deck([
            _mk("X", cmc=2, is_creature=True, tl="Creature"),
            _mk("Y", cmc=3, is_creature=True, tl="Creature"),
            _mk("Z", cmc=4, is_creature=True, tl="Creature"),
        ])
        # No combos: should NOT be COMBO archetype (only creatures)
        baseline = detect_archetype(deck)
        from densa_deck.formats.profiles import DeckArchetype
        # With 2+ combos: explicit override
        with_combos = detect_archetype(deck, detected_combo_count=2)
        assert with_combos == DeckArchetype.COMBO

    def test_one_combo_doesnt_override(self):
        """Single combo (e.g. Sol Ring + X infinite) shouldn't override —
        most casual decks have 1 incidental combo."""
        from densa_deck.formats.profiles import DeckArchetype
        deck = _build_deck([
            _mk("X", cmc=2, is_creature=True, tl="Creature"),
        ])
        result = detect_archetype(deck, detected_combo_count=1)
        assert result != DeckArchetype.COMBO


# ---------------------------------------------------------------- mulligan


class TestComboAwareMulligan:
    def test_bottom_cards_preserves_combo_pieces(self):
        """Combo pieces should rank highest during bottoming, beating even lands."""
        state = GameState()
        # Hand of 7: 2 combo pieces + 3 forests + 2 random creatures
        forest = _mk("Forest", tl="Basic Land", ci=[Color.GREEN], is_land=True)
        combo_a = _mk("Combo-A", cmc=2, mc="{2}", is_artifact=True)
        combo_b = _mk("Combo-B", cmc=2, mc="{2}", is_artifact=True)
        c1 = _mk("Filler-1", cmc=4, mc="{4}", tl="Creature", is_creature=True)
        c2 = _mk("Filler-2", cmc=5, mc="{5}", tl="Creature", is_creature=True)
        for c in (combo_a, combo_b, forest, forest, forest, c1, c2):
            state.hand.append(DeckEntry(card_name=c.name, quantity=1,
                                        zone=Zone.MAINBOARD, card=c))
        combos = {"combo-a", "combo-b"}
        # Bottom 3 cards
        _bottom_cards(state, 3, combo_card_names=combos)
        # Combo pieces must remain in hand
        names_left = {e.card.name for e in state.hand if e.card}
        assert "Combo-A" in names_left
        assert "Combo-B" in names_left


# ---------------------------------------------------------------- coach sheet


class TestComboCoachSheet:
    def test_no_combo_lines_no_block(self):
        sheet = build_deck_sheet(
            deck_name="Test", archetype="combo",
            color_identity=["U"], power_overall=8.0, power_tier="competitive",
            land_count=36, ramp_count=10, draw_count=10, interaction_count=8,
            avg_mana_value=2.5, deck_cards=["A", "B"],
        )
        assert "[COMBOS]" not in sheet
        assert "[CARDS]" in sheet

    def test_combo_lines_emitted(self):
        sheet = build_deck_sheet(
            deck_name="Test", archetype="combo",
            color_identity=["U", "B"], power_overall=9.0, power_tier="competitive",
            land_count=33, ramp_count=12, draw_count=10, interaction_count=12,
            avg_mana_value=2.0, deck_cards=["Thassa's Oracle"],
            combo_lines=["Thassa's Oracle + Demonic Consultation -> Win"],
        )
        assert "[COMBOS]" in sheet
        assert "Thassa's Oracle + Demonic Consultation" in sheet


# ---------------------------------------------------------------- summary prompt


class TestComboAwareSummaryPrompt:
    def test_combo_lines_appear_in_input_block(self):
        prompt = executive_summary_prompt(
            deck_name="Test", archetype="combo",
            power_overall=9.0, power_tier="competitive",
            power_reasons_up=[], power_reasons_down=[],
            land_count=33, ramp_count=12, draw_count=10,
            interaction_count=10, avg_mana_value=2.0,
            color_identity=["U", "B"], format_name="commander",
            recommendations=[],
            combo_lines=["Thoracle + Consultation"],
        )
        assert "Detected combo lines" in prompt
        assert "Thoracle + Consultation" in prompt
        # Combo instruction sentence appears in the system instruction
        assert "wins via combo" in prompt

    def test_no_combo_lines_no_combo_block(self):
        prompt = executive_summary_prompt(
            deck_name="Test", archetype="midrange",
            power_overall=6.0, power_tier="optimized",
            power_reasons_up=[], power_reasons_down=[],
            land_count=37, ramp_count=10, draw_count=8,
            interaction_count=8, avg_mana_value=3.0,
            color_identity=["G"], format_name="commander",
            recommendations=[],
        )
        assert "Detected combo lines" not in prompt
        assert "wins via combo" not in prompt


# ---------------------------------------------------------------- diff


class TestDiffCombos:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = ComboStore(db_path=Path(tmp) / "combos.db")
            s.upsert_combos([
                Combo(combo_id="c1", cards=["A", "B"], popularity=100),
                Combo(combo_id="c2", cards=["C", "D"], popularity=50),
                Combo(combo_id="c3", cards=["E", "F"], popularity=25),
            ])
            yield s
            s.close()

    def test_gained_loaded_buckets(self, store):
        # Before: A + B (c1 complete), C alone (c2 not yet)
        # After: C + D (c2 complete), A alone (c1 broken)
        result = diff_combos(
            store=store,
            before_card_names=["A", "B", "C"],
            after_card_names=["A", "C", "D"],
        )
        gained_ids = {m.combo.combo_id for m in result["gained"]}
        lost_ids = {m.combo.combo_id for m in result["lost"]}
        assert "c2" in gained_ids
        assert "c1" in lost_ids

    def test_still_present_carries_over(self, store):
        result = diff_combos(
            store=store,
            before_card_names=["A", "B"],
            after_card_names=["A", "B", "X"],  # added an irrelevant card
        )
        still_ids = {m.combo.combo_id for m in result["still_present"]}
        assert "c1" in still_ids
        assert result["gained"] == []
        assert result["lost"] == []

    def test_no_overlap_returns_empty(self, store):
        result = diff_combos(
            store=store,
            before_card_names=["X"],
            after_card_names=["Y"],
        )
        assert result["gained"] == []
        assert result["lost"] == []
        assert result["still_present"] == []
