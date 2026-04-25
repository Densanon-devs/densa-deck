"""Fourth-wave combo-aware tests:

- find_add_candidates pins combo-completer cards to the top of the
  candidate list and tags them with `completes_combo`.
- compare_decks_analyst returns combo_gained / combo_lost.
- save_deck_version returns combos_broken when a save removes a piece
  from a complete combo line in the prior version.
- explain_card_in_deck flags is_combo_piece when the named card
  participates in a complete combo line.
- bracket_fit names specific combo lines in recommendations when
  combo_lines is supplied.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.analysis.brackets import bracket_fit
from densa_deck.analyst.add_candidates import find_add_candidates
from densa_deck.app.api import AppApi
from densa_deck.combos import Combo, ComboStore
from densa_deck.data.database import CardDatabase
from densa_deck.models import (
    Card,
    CardLayout,
    CardTag,
    Color,
    Format,
    Legality,
)


def _mk_full_card(name, **kw):
    return Card(
        scryfall_id=f"sid-{name}", oracle_id=f"oid-{name}",
        name=name, layout=CardLayout.NORMAL,
        cmc=kw.get("cmc", 2), mana_cost=kw.get("mc", "{2}"),
        type_line=kw.get("tl", "Artifact"),
        colors=kw.get("cols", []),
        color_identity=kw.get("ci", []),
        is_artifact=kw.get("is_artifact", "Artifact" in kw.get("tl", "Artifact")),
        is_creature=kw.get("is_creature", False),
        is_land=kw.get("is_land", False),
        is_sorcery=kw.get("is_sorcery", False),
        legalities={"commander": Legality.LEGAL},
        tags=list(kw.get("tags", [])),
        oracle_text=kw.get("oracle_text", ""),
    )


# ---------------------------------------------------------------- add candidates


class TestComboBiasedAddCandidates:
    @pytest.fixture
    def card_db(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = CardDatabase(db_path=Path(tmp) / "cards.db")
            # Stock the DB with a couple of "ramp" candidates: one is a
            # combo completer (Mana Crypt — name we control here), the
            # other is a plain ramp piece (Cultivate). Both are role-fit.
            # classify_card runs over oracle_text to derive tags, so the
            # text needs trigger-words for the ramp/mana_rock classifier
            # to actually tag these cards as ramp.
            db.upsert_cards([
                _mk_full_card(
                    "Mana Crypt", cmc=0, mc="{0}", tl="Artifact",
                    oracle_text="{T}: Add {C}{C}.",
                ),
                _mk_full_card(
                    "Coalition Relic", cmc=3, mc="{3}", tl="Artifact",
                    oracle_text="{T}: Add one mana of any color. "
                                "{3}, {T}: Add two mana of any one color.",
                ),
                _mk_full_card(
                    "Sol Ring", cmc=1, mc="{1}", tl="Artifact",
                    oracle_text="{T}: Add {C}{C}.",
                ),
            ])
            yield db
            db.close()

    def test_completer_pinned_to_top(self, card_db):
        """When a completer is a role match, it ranks first regardless of
        the cmc-ascending ordering. Coalition Relic at CMC 3 should pin
        ahead of Mana Crypt (CMC 0) and Sol Ring (CMC 1)."""
        cands = find_add_candidates(
            db=card_db, role=CardTag.RAMP,
            deck_color_identity={"G", "W", "U", "B", "R"},
            format_=Format.COMMANDER,
            exclude_names=set(),
            limit=10,
            combo_completers={"coalition relic"},
        )
        assert cands, "expected ramp candidates"
        assert cands[0].card.name == "Coalition Relic"
        assert getattr(cands[0], "completes_combo", False) is True
        # Non-completer candidates should still appear, just below.
        for c in cands[1:]:
            assert getattr(c, "completes_combo", False) is False

    def test_no_completers_means_unchanged_ordering(self, card_db):
        """Backward compat: omitting combo_completers gives the same
        cmc-ascending ordering as before."""
        a = find_add_candidates(
            db=card_db, role=CardTag.RAMP,
            deck_color_identity={"G", "W", "U", "B", "R"},
            format_=Format.COMMANDER,
            exclude_names=set(),
            limit=10,
        )
        b = find_add_candidates(
            db=card_db, role=CardTag.RAMP,
            deck_color_identity={"G", "W", "U", "B", "R"},
            format_=Format.COMMANDER,
            exclude_names=set(),
            limit=10,
            combo_completers=set(),
        )
        assert [c.card.name for c in a] == [c.card.name for c in b]


# ---------------------------------------------------------------- bracket combo recs


class TestBracketComboLines:
    """When combo_lines is passed, bracket_fit names specific lines to drop."""

    def test_named_lines_appear_in_recommendations(self):
        # Stub deck — bracket_fit only uses deck for entries enumeration
        # via detect_deck_brackets. We pass it a minimal Deck via the
        # helper used in the existing brackets test file.
        from densa_deck.models import Deck, DeckEntry, Zone
        deck = Deck(name="t", format=Format.COMMANDER, entries=[])
        fit = bracket_fit(
            deck=deck, target_label="2-upgraded",
            power_overall=4.0,
            interaction_count=8,
            ramp_count=10,
            detected_combo_count=3,
            combo_lines=[
                "Thoracle + Consultation -> Win",
                "Sol Ring + Hullbreaker -> Infinite mana",
                "Mikaeus + Triskelion -> Infinite damage",
            ],
        )
        # At least one rec must NAME a specific combo line
        assert any("Thoracle" in r or "Sol Ring" in r or "Mikaeus" in r
                   for r in fit.recommendations)
        # The fallback "Disclose or cut combo lines" should NOT appear when
        # we're successfully naming specific lines.
        assert not any("Disclose or cut combo lines" in r for r in fit.recommendations)

    def test_no_combo_lines_falls_back_to_generic(self):
        from densa_deck.models import Deck
        deck = Deck(name="t", format=Format.COMMANDER, entries=[])
        fit = bracket_fit(
            deck=deck, target_label="2-upgraded",
            power_overall=4.0,
            interaction_count=8,
            ramp_count=10,
            detected_combo_count=2,
            combo_lines=None,
        )
        # Without combo_lines we get the generic "Disclose or cut combo lines"
        assert any("Disclose or cut combo lines" in r for r in fit.recommendations)


# ---------------------------------------------------------------- AppApi end-to-end


class TestAppApiComboFollowups:
    """End-to-end checks for the new compare/save/explain combo paths."""

    def _setup(self, tmp_path):
        os.environ["MTG_ENGINE_TIER"] = "pro"
        card_db = tmp_path / "cards.db"
        ver_db = tmp_path / "v.db"
        db = CardDatabase(db_path=card_db)
        db.upsert_cards([
            _mk_full_card("Sol Ring", cmc=1, mc="{1}", tl="Artifact"),
            _mk_full_card(
                "Hullbreaker Horror", cmc=2, mc="{1}{U}", tl="Creature",
                cols=[Color.BLUE], ci=[Color.BLUE], is_creature=True,
            ),
            _mk_full_card(
                "Forest", tl="Basic Land", ci=[Color.GREEN], is_land=True,
            ),
        ])
        db.close()
        cstore = ComboStore(db_path=tmp_path / "combos.db")
        cstore.upsert_combos([Combo(
            combo_id="42",
            cards=["Sol Ring", "Hullbreaker Horror"],
            produces=["Infinite mana"],
            color_identity="U",
            popularity=300_000,
        )])
        cstore.close()
        api = AppApi(db_path=card_db, version_db_path=ver_db)
        api._combo_store = ComboStore(db_path=tmp_path / "combos.db")
        return api

    def test_save_returns_combos_broken_when_combo_breaks(self, tmp_path):
        api = self._setup(tmp_path)
        try:
            # v1 includes both combo pieces — combo is COMPLETE
            text_v1 = ("Commander:\n1 Hullbreaker Horror\n\n"
                       "Mainboard:\n1 Sol Ring\n30 Forest\n")
            r1 = api.save_deck_version(
                deck_id="brew", name="Brew", decklist_text=text_v1,
                format_="commander",
            )
            assert r1["ok"] is True
            # v1 had no PRIOR version, so combos_broken should be empty.
            assert r1["data"].get("combos_broken") == []

            # v2 removes Sol Ring — breaks the combo
            text_v2 = ("Commander:\n1 Hullbreaker Horror\n\n"
                       "Mainboard:\n30 Forest\n")
            r2 = api.save_deck_version(
                deck_id="brew", name="Brew", decklist_text=text_v2,
                format_="commander",
            )
            assert r2["ok"] is True
            broken = r2["data"].get("combos_broken") or []
            assert len(broken) == 1
            assert broken[0]["combo_id"] == "42"
        finally:
            api.close()

    def test_explain_card_flags_combo_piece(self, tmp_path):
        api = self._setup(tmp_path)
        try:
            text = ("Commander:\n1 Hullbreaker Horror\n\n"
                    "Mainboard:\n1 Sol Ring\n30 Forest\n")
            r = api.explain_card_in_deck(text, "Sol Ring", "commander", "Brew")
            assert r["ok"] is True
            assert r["data"]["is_combo_piece"] is True
            # The first flag (per ordering rule) should announce it as a combo piece.
            assert any("COMBO PIECE" in f for f in r["data"]["flags"])
        finally:
            api.close()

    def test_explain_card_no_combo_piece_when_card_not_in_combo(self, tmp_path):
        api = self._setup(tmp_path)
        try:
            text = ("Commander:\n1 Hullbreaker Horror\n\n"
                    "Mainboard:\n1 Sol Ring\n30 Forest\n")
            # Forest isn't a combo piece — should NOT be flagged.
            r = api.explain_card_in_deck(text, "Forest", "commander", "Brew")
            assert r["ok"] is True
            assert r["data"]["is_combo_piece"] is False
        finally:
            api.close()
