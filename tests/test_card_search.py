"""Regression tests for CardDatabase.search_structured — the SQL-filtered
card search powering the Build tab.

Covers each filter dimension independently plus a few combinations.
Uses a tiny in-memory card library so tests are fast and the expected
results are obvious from inspection.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardLayout, Color, Legality


@pytest.fixture
def card_lib():
    """Tiny library picked to exercise each filter without being trivially
    ambiguous — each card hits at least one "distinguishing" axis another
    card doesn't."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "cards.db"
        db = CardDatabase(db_path=db_path)
        cards = [
            Card(
                scryfall_id="sid-sol", oracle_id="oid-sol", name="Sol Ring",
                layout=CardLayout.NORMAL, cmc=1, mana_cost="{1}",
                type_line="Artifact", colors=[], color_identity=[],
                legalities={"commander": Legality.LEGAL, "modern": Legality.BANNED},
                rarity="uncommon", set_code="LEA",
                price_usd=2.0, is_artifact=True,
            ),
            Card(
                scryfall_id="sid-counter", oracle_id="oid-counter",
                name="Counterspell", layout=CardLayout.NORMAL, cmc=2,
                mana_cost="{U}{U}", type_line="Instant",
                colors=[Color.BLUE], color_identity=[Color.BLUE],
                legalities={"commander": Legality.LEGAL, "modern": Legality.LEGAL},
                rarity="common", set_code="LEA",
                price_usd=1.5, is_instant=True,
            ),
            Card(
                scryfall_id="sid-cult", oracle_id="oid-cult", name="Cultivate",
                layout=CardLayout.NORMAL, cmc=3, mana_cost="{2}{G}",
                type_line="Sorcery", colors=[Color.GREEN], color_identity=[Color.GREEN],
                legalities={"commander": Legality.LEGAL, "modern": Legality.LEGAL},
                rarity="common", set_code="CMR",
                price_usd=0.25, is_sorcery=True,
            ),
            Card(
                scryfall_id="sid-atraxa", oracle_id="oid-atraxa",
                name="Atraxa, Praetors' Voice", layout=CardLayout.NORMAL, cmc=4,
                mana_cost="{G}{W}{U}{B}", type_line="Legendary Creature — Angel Horror",
                colors=[Color.WHITE, Color.BLUE, Color.BLACK, Color.GREEN],
                color_identity=[Color.WHITE, Color.BLUE, Color.BLACK, Color.GREEN],
                legalities={"commander": Legality.LEGAL},
                rarity="mythic", set_code="C16",
                price_usd=25.0, is_creature=True,
            ),
            Card(
                scryfall_id="sid-forest", oracle_id="oid-forest", name="Forest",
                layout=CardLayout.NORMAL, cmc=0, mana_cost="",
                type_line="Basic Land — Forest",
                colors=[], color_identity=[Color.GREEN],
                legalities={"commander": Legality.LEGAL, "modern": Legality.LEGAL},
                rarity="common", set_code="LEA",
                price_usd=None, is_land=True,
            ),
            Card(
                scryfall_id="sid-lotus", oracle_id="oid-lotus", name="Black Lotus",
                layout=CardLayout.NORMAL, cmc=0, mana_cost="{0}",
                type_line="Artifact", colors=[], color_identity=[],
                legalities={"commander": Legality.BANNED, "vintage": Legality.RESTRICTED},
                rarity="mythic", set_code="LEA",
                price_usd=20000.0, is_artifact=True,
            ),
        ]
        db.upsert_cards(cards)
        yield db
        db.close()


class TestStructuredSearch:
    def test_name_substring_matches_case_insensitive(self, card_lib):
        cards, total = card_lib.search_structured(name="sol")
        names = [c.name for c in cards]
        assert "Sol Ring" in names
        assert total == 1

    def test_cmc_range(self, card_lib):
        cards, total = card_lib.search_structured(cmc_min=2, cmc_max=3)
        names = sorted(c.name for c in cards)
        # Counterspell (2) + Cultivate (3) — not Sol Ring (1), not Atraxa (4)
        assert names == ["Counterspell", "Cultivate"]
        assert total == 2

    def test_types_filter(self, card_lib):
        cards, _ = card_lib.search_structured(types=["artifact"])
        names = sorted(c.name for c in cards)
        assert names == ["Black Lotus", "Sol Ring"]

    def test_format_legal_excludes_banned(self, card_lib):
        cards, _ = card_lib.search_structured(format_legal="commander")
        names = sorted(c.name for c in cards)
        # Black Lotus is banned in commander — must NOT appear
        assert "Black Lotus" not in names
        # Atraxa is legal — must appear
        assert "Atraxa, Praetors' Voice" in names

    def test_format_legal_excludes_restricted(self, card_lib):
        cards, _ = card_lib.search_structured(format_legal="vintage")
        names = [c.name for c in cards]
        # Black Lotus is restricted in vintage — explicitly NOT "legal"
        assert "Black Lotus" not in names

    def test_rarity_filter(self, card_lib):
        cards, _ = card_lib.search_structured(rarity="mythic")
        names = sorted(c.name for c in cards)
        assert names == ["Atraxa, Praetors' Voice", "Black Lotus"]

    def test_max_price_keeps_null_price(self, card_lib):
        # Forest has price_usd=None — should NOT be excluded by a price cap.
        cards, _ = card_lib.search_structured(max_price=5.0)
        names = sorted(c.name for c in cards)
        assert "Forest" in names
        assert "Atraxa, Praetors' Voice" not in names  # $25 > $5
        assert "Black Lotus" not in names

    def test_color_identity_commander_subset(self, card_lib):
        # Picking W+U+B+G as the commander's color identity — Atraxa
        # herself fits exactly. Counterspell (U) and Cultivate (G) also
        # fit. Forest (G identity, no colors) fits. Colorless cards
        # always fit a commander identity filter.
        cards, _ = card_lib.search_structured(
            colors=["W", "U", "B", "G"], color_match="identity",
        )
        names = sorted(c.name for c in cards)
        # All 4-color + colorless cards fit; only RED-only cards would be
        # excluded (none in our library).
        assert "Atraxa, Praetors' Voice" in names
        assert "Counterspell" in names
        assert "Cultivate" in names
        assert "Forest" in names
        assert "Sol Ring" in names  # colorless — identity = []

    def test_color_identity_narrower_excludes_wider(self, card_lib):
        # Mono-green identity: Cultivate + Forest yes, Counterspell (U)
        # and Atraxa (4-color) no. Sol Ring / Black Lotus colorless
        # always qualify.
        cards, _ = card_lib.search_structured(
            colors=["G"], color_match="identity",
        )
        names = sorted(c.name for c in cards)
        assert "Cultivate" in names
        assert "Forest" in names
        assert "Counterspell" not in names
        assert "Atraxa, Praetors' Voice" not in names

    def test_color_any_mode(self, card_lib):
        # Any-of-selected: Counterspell (U) matches even though other
        # cards use different colors.
        cards, _ = card_lib.search_structured(
            colors=["U"], color_match="any",
        )
        names = sorted(c.name for c in cards)
        assert "Counterspell" in names
        # Atraxa has U in her identity too
        assert "Atraxa, Praetors' Voice" in names
        # Cultivate is mono-green, should NOT match
        assert "Cultivate" not in names

    def test_pagination_respects_offset(self, card_lib):
        # Fetch sorted-alphabetically, two pages of size 2.
        page1, total = card_lib.search_structured(limit=2, offset=0)
        page2, total2 = card_lib.search_structured(limit=2, offset=2)
        assert total == total2  # total is filter-invariant of offset
        # No overlap between pages
        names1 = {c.name for c in page1}
        names2 = {c.name for c in page2}
        assert not (names1 & names2)

    def test_combined_filters(self, card_lib):
        # Green cards, CMC 3, commander-legal — should be only Cultivate.
        cards, total = card_lib.search_structured(
            colors=["G"], color_match="identity",
            cmc_min=3, cmc_max=3, format_legal="commander",
        )
        names = [c.name for c in cards]
        assert names == ["Cultivate"]
        assert total == 1
