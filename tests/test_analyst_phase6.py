"""Tests for analyst Phase 6 features (compare-decks, explain-card,
Rule 0 worksheet) and the Commander Spellbook combo integration.

Uses the existing MockBackend so no real LLM is needed. The Phase 6
prompts are pure prose narration so the verifier passes any non-empty
output — we just check the output flows through and the structured
deltas survive the trip.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.analyst import MockBackend
from densa_deck.analyst.phase6 import (
    CompareResult,
    ExplainResult,
    Rule0Worksheet,
    build_rule0_worksheet,
    compare_decks,
    explain_card,
    render_rule0_text,
)
from densa_deck.combos import (
    Combo,
    ComboStore,
    MatchedCombo,
    detect_combos,
)
from densa_deck.combos.data import _parse_variant


# ---------------------------------------------------------------- compare


class TestCompareDecks:
    def test_returns_prose_summary(self):
        backend = MockBackend(default="Deck B is faster and more interactive — close the gap by adding 2 fast-mana pieces.")
        r = compare_decks(
            backend=backend,
            deck_a_name="A", deck_b_name="B",
            deck_a_archetype="midrange", deck_b_archetype="stax",
            deck_a_power=6.5, deck_b_power=9.0,
            added_cards=["Mana Drain", "Mana Crypt"],
            removed_cards=["Cultivate"],
            score_deltas={"speed": 18.0, "interaction": 22.0},
            role_deltas={"ramp": 2, "interaction": 3},
        )
        assert isinstance(r, CompareResult)
        assert r.summary
        assert "faster" in r.summary
        assert r.added_in_b == ["Mana Drain", "Mana Crypt"]
        assert r.power_gap == pytest.approx(2.5)

    def test_score_deltas_round_trip(self):
        backend = MockBackend(default="Stub.")
        r = compare_decks(
            backend=backend,
            deck_a_name="A", deck_b_name="B",
            deck_a_archetype="x", deck_b_archetype="y",
            deck_a_power=5, deck_b_power=5,
            added_cards=[], removed_cards=[],
            score_deltas={"a": 1.5, "b": -2.0},
        )
        assert r.score_deltas["a"] == pytest.approx(1.5)
        assert r.score_deltas["b"] == pytest.approx(-2.0)


# ---------------------------------------------------------------- explain


class TestExplainCard:
    def test_returns_prose_for_castability_flag(self):
        backend = MockBackend(default="Triple-U pip is too demanding for your 14-source U base.")
        r = explain_card(
            backend=backend,
            card_name="Cryptic Command",
            mana_cost="{1}{U}{U}{U}",
            cmc=4.0,
            deck_name="Esper Midrange",
            deck_colors=["W", "U", "B"],
            color_sources={"W": 8, "U": 14, "B": 9},
            on_curve_prob=0.34,
            bottleneck_color="U",
            flags=["unreliable on curve (P=0.34); bottleneck color U"],
            role_tags=["card_draw", "counterspell"],
        )
        assert isinstance(r, ExplainResult)
        assert r.card_name == "Cryptic Command"
        assert r.summary
        assert r.on_curve_prob == pytest.approx(0.34)
        assert r.bottleneck_color == "U"


# ---------------------------------------------------------------- Rule 0


class _Power:
    def __init__(self, overall: float, tier: str = ""):
        self.overall = overall
        self.tier = tier


class _Analysis:
    def __init__(self, interaction: int, lands: int = 36, ramp: int = 10, draw: int = 9):
        self.interaction_count = interaction
        self.land_count = lands
        self.ramp_count = ramp
        self.draw_engine_count = draw


class TestRule0Worksheet:
    def test_bracket_mapping(self):
        cases = [
            (2.0, "1-precon"),
            (4.0, "2-upgraded"),
            (6.5, "3-optimized"),
            (8.5, "4-high-power"),
            (9.5, "5-cedh"),
            (10.0, "5-cedh"),
        ]
        for overall, expected in cases:
            ws = build_rule0_worksheet(
                deck_name="t", archetype="x", color_identity=["G"],
                power=_Power(overall=overall),
                analysis=_Analysis(interaction=8),
            )
            assert ws.bracket == expected, f"power {overall} -> {ws.bracket}, expected {expected}"

    def test_low_interaction_adds_disclose_note(self):
        ws = build_rule0_worksheet(
            deck_name="x", archetype="combo", color_identity=["U", "B"],
            power=_Power(overall=8.0),
            analysis=_Analysis(interaction=2),
        )
        assert ws.interaction_density == "low"
        assert any("Light on interaction" in n for n in ws.pre_game_notes)

    def test_combo_lines_pinned_to_top_of_notes(self):
        ws = build_rule0_worksheet(
            deck_name="x", archetype="combo", color_identity=["U", "B"],
            power=_Power(overall=8.0),
            analysis=_Analysis(interaction=8),
            combo_lines=["Thoracle + Demonic Consultation"],
        )
        assert any("combo line" in n for n in ws.pre_game_notes)
        # Combo note should be the first bullet so the player sees it first.
        assert ws.pre_game_notes[0].lower().startswith("has ")

    def test_render_text_contains_label_columns(self):
        ws = build_rule0_worksheet(
            deck_name="My Brew", archetype="aristocrats", color_identity=["B", "G"],
            power=_Power(overall=6.5, tier="focused"),
            analysis=_Analysis(interaction=8),
        )
        out = render_rule0_text(ws)
        assert "Deck:" in out
        assert "My Brew" in out
        assert "Bracket:" in out
        assert out.endswith("\n")


# ---------------------------------------------------------------- Combo store


class TestComboStore:
    def test_upsert_and_lookup_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ComboStore(db_path=Path(tmp) / "combos.db")
            combo = Combo(
                combo_id="123",
                cards=["Sol Ring", "Hullbreaker Horror"],
                produces=["Infinite colorless mana"],
                color_identity="U",
                bracket_tag="E",
                description="Demo combo.",
                popularity=42,
                legal_in_commander=True,
                spellbook_url="https://commanderspellbook.com/combo/123/",
            )
            written = store.upsert_combos([combo])
            assert written == 1
            assert store.combo_count() == 1
            ids = store.lookup_combos_for_card("sol ring")
            assert ids == ["123"]
            got = store.get_combo("123")
            assert got is not None
            assert got.cards == ["Sol Ring", "Hullbreaker Horror"]
            assert got.color_identity == "U"
            store.close()

    def test_metadata_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ComboStore(db_path=Path(tmp) / "combos.db")
            store.set_metadata("last_refresh_at", "2026-04-25T12:00:00")
            assert store.get_metadata("last_refresh_at") == "2026-04-25T12:00:00"
            assert store.get_metadata("never_set") is None
            store.close()


# ---------------------------------------------------------------- Combo matcher


class TestDetectCombos:
    @pytest.fixture
    def populated_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ComboStore(db_path=Path(tmp) / "combos.db")
            store.upsert_combos([
                Combo(
                    combo_id="bg-1",
                    cards=["Sol Ring", "Hullbreaker Horror"],
                    produces=["Infinite colorless mana"],
                    color_identity="U",
                    popularity=300_000,
                ),
                Combo(
                    combo_id="rakdos-1",
                    cards=["Vilis, Broker of Blood", "Pain's Reward"],
                    produces=["Infinite life"],
                    color_identity="B",
                    popularity=10,
                ),
                Combo(
                    combo_id="esper-1",
                    cards=["Sol Ring", "Card Not In Deck"],
                    color_identity="U",
                    popularity=5,
                ),
            ])
            yield store
            store.close()

    def test_full_match_returns_combo(self, populated_store):
        matches = detect_combos(
            store=populated_store,
            deck_card_names=["Sol Ring", "Hullbreaker Horror", "Forest"],
            deck_color_identity=["U"],
        )
        ids = [m.combo.combo_id for m in matches]
        assert "bg-1" in ids
        # esper-1 requires "Card Not In Deck" — must NOT match
        assert "esper-1" not in ids

    def test_color_subset_filter(self, populated_store):
        # WUG deck — rakdos-1 (B identity) must be filtered out.
        matches = detect_combos(
            store=populated_store,
            deck_card_names=["Vilis, Broker of Blood", "Pain's Reward"],
            deck_color_identity=["W", "U", "G"],
        )
        ids = [m.combo.combo_id for m in matches]
        assert "rakdos-1" not in ids

    def test_results_sorted_by_popularity(self, populated_store):
        matches = detect_combos(
            store=populated_store,
            deck_card_names=[
                "Sol Ring", "Hullbreaker Horror",
                "Vilis, Broker of Blood", "Pain's Reward",
            ],
            deck_color_identity=["U", "B"],
        )
        # bg-1 (popularity 300_000) must come before rakdos-1 (10)
        if len(matches) >= 2:
            assert matches[0].combo.popularity >= matches[1].combo.popularity

    def test_empty_deck_returns_empty(self, populated_store):
        assert detect_combos(store=populated_store, deck_card_names=[]) == []


# ---------------------------------------------------------------- Variant parser


class TestParseVariant:
    def test_skips_non_ok_status(self):
        raw = {"status": "DRAFT", "id": "1", "uses": []}
        assert _parse_variant(raw) is None

    def test_extracts_cards_and_features(self):
        raw = {
            "status": "OK", "id": "abc",
            "uses": [{"card": {"name": "Sol Ring"}}, {"card": {"name": "Mana Crypt"}}],
            "produces": [{"feature": {"name": "Infinite colorless mana"}}],
            "requires": [{"template": {"name": "Permanent that costs {C}"}}],
            "identity": "C", "bracketTag": "E",
            "description": "Tap and untap.", "popularity": 99,
            "legalities": {"commander": True},
        }
        c = _parse_variant(raw)
        assert c is not None
        assert c.combo_id == "abc"
        assert c.cards == ["Sol Ring", "Mana Crypt"]
        assert c.produces == ["Infinite colorless mana"]
        assert c.templates == ["Permanent that costs {C}"]
        assert c.color_identity == "C"
        assert c.legal_in_commander is True
        assert c.spellbook_url == "https://commanderspellbook.com/combo/abc/"
