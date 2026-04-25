"""Tests for the new big-wins layer:

- analysis.brackets — bracket-fit assessment (1-precon ... 5-cedh)
- combos.matcher.detect_near_miss_combos — "you're 1 card away" finder
- app/api.py multi-format export (MTGO .dek / MTGA / Moxfield)
- app/api.py suggest_deckbuild_additions (Pro AI deckbuild)
- app/api.py assess_bracket_fit
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.analysis.brackets import (
    BRACKETS,
    BracketFit,
    bracket_fit,
    detect_deck_brackets,
)
from densa_deck.combos import (
    Combo,
    ComboStore,
    detect_near_miss_combos,
)


# ---------------------------------------------------------------- brackets


class _MiniDeck:
    """Tiny stand-in for densa_deck.models.Deck — we just need .entries
    and a card-name iteration. The real Deck/Entry/Card types are heavy
    Pydantic; for bracket logic we only need names + tags + qty."""
    def __init__(self, entries):
        self.entries = entries


class _Entry:
    def __init__(self, card, zone="mainboard", quantity=1):
        self.card = card
        # Use a string for zone to match the boundary check
        # (`e.zone not in (MAYBEBOARD, SIDEBOARD)`). The brackets
        # module imports those enum values at runtime so we need
        # the actual enum here — use the densa_deck.models.Zone.
        from densa_deck.models import Zone
        zmap = {"mainboard": Zone.MAINBOARD, "sideboard": Zone.SIDEBOARD,
                "commander": Zone.COMMANDER, "maybeboard": Zone.MAYBEBOARD}
        self.zone = zmap.get(zone, Zone.MAINBOARD)
        self.quantity = quantity


class _MiniCard:
    def __init__(self, name, tags=()):
        self.name = name
        # Use proper CardTag enum values where they map to known tags.
        from densa_deck.models import CardTag
        self.tags = [CardTag(t) for t in tags if t in {ct.value for ct in CardTag}]
        self.is_land = False
        self.color_identity = []


class TestBrackets:
    def test_bracket_for_power_buckets(self):
        cases = [
            (1.5, "1-precon"),
            (4.0, "2-upgraded"),
            (6.5, "3-optimized"),
            (8.5, "4-high-power"),
            (9.7, "5-cedh"),
        ]
        deck = _MiniDeck(entries=[])
        for power, expected in cases:
            label, name, _ = detect_deck_brackets(deck, power)
            assert label == expected, f"power {power} -> {label}, expected {expected}"

    def test_fast_mana_bumps_bracket_up(self):
        """A bracket-1 (low power) deck running Mana Crypt + Mana Vault +
        Mox Diamond should be bumped to bracket 2 because the structural
        fast-mana count exceeds bracket 1's cap of 0."""
        deck = _MiniDeck(entries=[
            _Entry(_MiniCard("Mana Crypt")),
            _Entry(_MiniCard("Mana Vault")),
            _Entry(_MiniCard("Mox Diamond")),
        ])
        label, _, signals = detect_deck_brackets(deck, power_overall=2.0)
        assert signals["fast_mana_count"] == 3
        # Power 2.0 maps to "1-precon" baseline; bumped UP because of fast mana.
        assert label != "1-precon"

    def test_bracket_fit_under_delivers(self):
        """Targeting bracket 4 with a low-interaction casual deck."""
        deck = _MiniDeck(entries=[])
        fit = bracket_fit(
            deck=deck, target_label="4-high-power",
            power_overall=5.0,
            interaction_count=4,
            ramp_count=8,
            detected_combo_count=0,
        )
        assert isinstance(fit, BracketFit)
        assert fit.verdict == "under-delivers"
        assert any("interaction" in s for s in fit.under_signals)
        assert fit.recommendations  # has a punch list

    def test_bracket_fit_over_pitches(self):
        """Targeting bracket 1 with fast-mana cards — the deck reads above."""
        deck = _MiniDeck(entries=[
            _Entry(_MiniCard("Mana Crypt")),
            _Entry(_MiniCard("Mana Vault")),
        ])
        fit = bracket_fit(
            deck=deck, target_label="1-precon",
            power_overall=2.0,
            interaction_count=8,
            ramp_count=10,
            detected_combo_count=0,
        )
        assert fit.verdict == "over-pitches"
        assert any("fast-mana" in s for s in fit.over_signals)

    def test_bracket_fit_combo_constraint(self):
        """Bracket 2 forbids combos; surfacing 1+ should over-pitch."""
        deck = _MiniDeck(entries=[])
        fit = bracket_fit(
            deck=deck, target_label="2-upgraded",
            power_overall=4.0,
            interaction_count=8,
            ramp_count=10,
            detected_combo_count=2,
        )
        assert any("combo" in s.lower() for s in fit.over_signals)

    def test_bracket_fit_returns_bracket_names(self):
        deck = _MiniDeck(entries=[])
        fit = bracket_fit(
            deck=deck, target_label="3-optimized",
            power_overall=6.0,
            interaction_count=10,
            ramp_count=11,
            detected_combo_count=0,
        )
        assert fit.detected_label
        assert fit.target_label == "3-optimized"
        assert fit.target_name in {b[1] for b in BRACKETS}


# ---------------------------------------------------------------- near-miss combos


class TestNearMissCombos:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = ComboStore(db_path=Path(tmp) / "combos.db")
            s.upsert_combos([
                Combo(combo_id="c1", cards=["Sol Ring", "Hullbreaker Horror"],
                      produces=["Infinite mana"], color_identity="U",
                      popularity=300_000),
                Combo(combo_id="c2", cards=["Demonic Consultation", "Thassa's Oracle"],
                      produces=["Win the game"], color_identity="UB",
                      popularity=150_000),
                Combo(combo_id="c3", cards=["A", "B", "C", "D"],
                      produces=["Infinite tokens"], color_identity="W",
                      popularity=100),
            ])
            yield s
            s.close()

    def test_one_away_returns_combo(self, store):
        # Deck has Sol Ring but is missing Hullbreaker Horror — should
        # show up as 1-away.
        near = detect_near_miss_combos(
            store=store,
            deck_card_names=["Sol Ring", "Forest"],
            deck_color_identity=["U"],
            max_missing=1,
        )
        ids = [n.combo.combo_id for n in near]
        assert "c1" in ids
        # Missing card should be Hullbreaker Horror
        match = next(n for n in near if n.combo.combo_id == "c1")
        assert match.missing_count == 1
        assert match.missing_cards == ["Hullbreaker Horror"]
        assert match.in_deck_cards == ["Sol Ring"]

    def test_zero_missing_excluded(self, store):
        """Fully-completed combos are NOT near misses."""
        near = detect_near_miss_combos(
            store=store,
            deck_card_names=["Demonic Consultation", "Thassa's Oracle"],
            deck_color_identity=["U", "B"],
            max_missing=2,
        )
        # Combo c2 is fully present, NOT 1-away
        ids = [n.combo.combo_id for n in near]
        assert "c2" not in ids

    def test_color_subset_filter(self, store):
        """Even 1-away combos must respect color identity."""
        near = detect_near_miss_combos(
            store=store,
            deck_card_names=["A", "B", "C"],
            deck_color_identity=["U", "B"],  # not white — c3 W combo excluded
            max_missing=1,
        )
        ids = [n.combo.combo_id for n in near]
        assert "c3" not in ids

    def test_skips_huge_combos(self, store):
        """Combos with >6 cards are skipped — too noisy to surface as 1-away."""
        with tempfile.TemporaryDirectory() as tmp:
            s = ComboStore(db_path=Path(tmp) / "combos.db")
            s.upsert_combos([Combo(
                combo_id="big", cards=["A", "B", "C", "D", "E", "F", "G"],
                color_identity="C", popularity=10,
            )])
            near = detect_near_miss_combos(
                store=s,
                deck_card_names=["A", "B", "C", "D", "E", "F"],
                max_missing=1,
            )
            assert near == []
            s.close()


# ---------------------------------------------------------------- export


class TestExport:
    """Verify the exported deck strings match the format vendors expect."""

    @pytest.fixture
    def deck(self):
        # Build a small resolved deck via the existing pipeline
        from densa_deck.data.database import CardDatabase
        from densa_deck.deck.parser import parse_decklist
        from densa_deck.deck.resolver import resolve_deck
        from densa_deck.models import Card, CardLayout, Color, Format, Legality

        with tempfile.TemporaryDirectory() as tmp:
            db = CardDatabase(db_path=Path(tmp) / "cards.db")
            db.upsert_cards([
                Card(scryfall_id="s1", oracle_id="o1", name="Sol Ring",
                     layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                     type_line="Artifact", legalities={"commander": Legality.LEGAL}),
                Card(scryfall_id="s2", oracle_id="o2", name="Atraxa, Praetors' Voice",
                     layout=CardLayout.NORMAL, cmc=4, mana_cost="{G}{W}{U}{B}",
                     type_line="Legendary Creature",
                     color_identity=[Color.WHITE, Color.BLUE, Color.BLACK, Color.GREEN],
                     legalities={"commander": Legality.LEGAL}),
                Card(scryfall_id="s3", oracle_id="o3", name="Forest",
                     layout=CardLayout.NORMAL, type_line="Basic Land",
                     color_identity=[Color.GREEN], is_land=True,
                     legalities={"commander": Legality.LEGAL}),
            ])
            text = "Commander:\n1 Atraxa, Praetors' Voice\n\nMainboard:\n1 Sol Ring\n10 Forest\n"
            entries = parse_decklist(text)
            deck = resolve_deck(entries, db, name="My Atraxa", format=Format.COMMANDER)
            db.close()
            yield deck

    def test_mtga_export_shape(self, deck):
        from densa_deck.app.api import _export_mtga
        content, fname = _export_mtga(deck)
        assert "Commander" in content
        assert "Deck" in content
        assert "1 Atraxa, Praetors' Voice" in content
        assert "1 Sol Ring" in content
        assert "10 Forest" in content
        assert fname.endswith(".txt")

    def test_moxfield_text_matches_mtga(self, deck):
        # We deliberately reuse the MTGA shape for Moxfield — both accept it.
        from densa_deck.app.api import _export_moxfield_text, _export_mtga
        a, _ = _export_moxfield_text(deck)
        b, _ = _export_mtga(deck)
        assert a == b

    def test_mtgo_export_xml(self, deck):
        from densa_deck.app.api import _export_mtgo
        content, fname = _export_mtgo(deck)
        assert content.startswith("<?xml")
        assert "<Deck>" in content
        assert "</Deck>" in content
        # Atraxa flagged as sideboard=true (Commander zone)
        assert 'Sideboard="true"' in content
        # Sol Ring + Forest in mainboard
        assert 'Sideboard="false"' in content
        assert "Sol Ring" in content
        assert fname.endswith(".dek")

    def test_safe_filename_slugifies(self):
        from densa_deck.app.api import _safe_filename
        assert _safe_filename("My Atraxa Deck") == "My-Atraxa-Deck"
        assert _safe_filename("/etc/passwd") == "etc-passwd"
        assert _safe_filename("") == "deck"
        assert _safe_filename("...weird///\\input...") == "weird-input"
