"""Phase 3 — deck value vs build value vs cost to complete."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.deck_value import shopping_list_text, value_deck
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.models import Deck, DeckEntry, Zone

CHEAP_SOL = "sol-cheap"
FOIL_SOL = "sol-foil"
BOLT = "bolt-1"
NOPRICE = "obscure-1"


def _raw(pid, name, set_code, num, *, usd=None, foil=None, oracle="o",
         finishes=("nonfoil",)):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_code.upper() + " Set", "collector_number": num,
        "rarity": "rare", "lang": "en", "released_at": "2020-01-01",
        "finishes": list(finishes), "frame": "2015", "border_color": "black",
        "promo_types": [], "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": None},
    }


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            # Same card, two printings, 400x apart.
            printing_row_from_scryfall(
                _raw(CHEAP_SOL, "Sol Ring", "cmm", "410", usd="1.50",
                     foil="4.00", finishes=("nonfoil", "foil"), oracle="o-sol"), "t"),
            printing_row_from_scryfall(
                _raw(FOIL_SOL, "Sol Ring", "sld", "99", usd="200.00",
                     foil="600.00", finishes=("nonfoil", "foil"), oracle="o-sol"), "t"),
            printing_row_from_scryfall(
                _raw(BOLT, "Lightning Bolt", "lea", "161", usd="10.00",
                     oracle="o-bolt"), "t"),
            printing_row_from_scryfall(
                _raw(NOPRICE, "Obscure Card", "old", "1", oracle="o-obs"), "t"),
        ])
        store = CollectionStore(db_path=root / "collection.db")
        yield store, db
        db.close()


def _deck(cards: dict[str, int], name="Test Deck"):
    return Deck(name=name, entries=[
        DeckEntry(card_name=n, quantity=q, zone=Zone.MAINBOARD)
        for n, q in cards.items()
    ])


class TestBuildValue:
    def test_build_value_uses_cheapest_printing(self, env):
        store, db = env
        v = value_deck(_deck({"Sol Ring": 1}), store, db)
        assert v["build_value_usd"] == 1.50  # not the $200 Secret Lair

    def test_build_value_scales_with_quantity(self, env):
        store, db = env
        v = value_deck(_deck({"Lightning Bolt": 4}), store, db)
        assert v["build_value_usd"] == 40.00

    def test_unpriced_card_counted_not_zeroed(self, env):
        store, db = env
        v = value_deck(_deck({"Lightning Bolt": 1, "Obscure Card": 1}), store, db)
        assert v["build_value_usd"] == 10.00
        assert v["build_value_unpriced"] == 1


class TestDeckValue:
    def test_owned_copy_valued_at_what_you_own(self, env):
        store, db = env
        store.add_copies(FOIL_SOL, "Sol Ring", quantity=1, finish="foil")  # $600
        v = value_deck(_deck({"Sol Ring": 1}), store, db)
        assert v["deck_value_usd"] == 600.00
        # ...but building it fresh is still cheap.
        assert v["build_value_usd"] == 1.50

    def test_unowned_card_falls_back_to_cheapest(self, env):
        store, db = env
        v = value_deck(_deck({"Sol Ring": 1}), store, db)
        assert v["deck_value_usd"] == 1.50

    def test_cheapest_owned_copy_wins_not_the_dearest(self, env):
        """Owning a bulk copy and a foil, the deck is assumed to run the bulk one.

        Assuming the expensive copy would inflate the number and flatter the
        user — wrong direction for a tool people make money decisions with.
        """
        store, db = env
        store.add_copies(CHEAP_SOL, "Sol Ring", quantity=1)
        store.add_copies(FOIL_SOL, "Sol Ring", quantity=1, finish="foil")
        v = value_deck(_deck({"Sol Ring": 1}), store, db)
        assert v["deck_value_usd"] == 1.50

    def test_partial_ownership_mixes_owned_and_market(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=2)   # own 2 @ $10
        v = value_deck(_deck({"Lightning Bolt": 4}), store, db)
        assert v["deck_value_usd"] == 40.00  # 2 owned + 2 at cheapest

    def test_condition_discounts_owned_copies(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=1, condition="MP")
        v = value_deck(_deck({"Lightning Bolt": 1}), store, db)
        assert v["deck_value_usd"] == 7.00  # 10.00 * 0.70


class TestCostToComplete:
    def test_only_missing_cards_count(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        v = value_deck(_deck({"Lightning Bolt": 4, "Sol Ring": 1}), store, db)
        assert v["cost_to_complete_usd"] == 1.50
        assert v["missing_distinct"] == 1

    def test_partial_shortfall(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        v = value_deck(_deck({"Lightning Bolt": 4}), store, db)
        assert v["cost_to_complete_usd"] == 30.00  # 3 short at $10

    def test_owning_everything_costs_nothing(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=4)
        v = value_deck(_deck({"Lightning Bolt": 4}), store, db)
        assert v["cost_to_complete_usd"] == 0.0
        assert v["shopping_list"] == []

    def test_unpriced_missing_card_is_flagged(self, env):
        store, db = env
        v = value_deck(_deck({"Obscure Card": 1}), store, db)
        assert v["cost_to_complete_usd"] == 0.0
        assert v["cost_to_complete_unpriced"] == 1

    def test_shopping_list_is_pasteable(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        v = value_deck(_deck({"Lightning Bolt": 4, "Sol Ring": 2}), store, db)
        text = shopping_list_text(v)
        assert "3 Lightning Bolt" in text
        assert "2 Sol Ring" in text

    def test_shopping_list_sorted_by_cost(self, env):
        store, db = env
        v = value_deck(_deck({"Sol Ring": 1, "Lightning Bolt": 1}), store, db)
        # Dearest gap first — that's the decision that matters.
        assert v["shopping_list"][0]["card_name"] == "Lightning Bolt"


class _FakeSnapshot:
    def __init__(self, decklist):
        self.decklist = decklist


class _FakeVersionStore:
    def __init__(self, decks):
        self._decks = decks

    def list_decks(self):
        return [{"deck_id": d} for d in self._decks]

    def get_latest(self, deck_id):
        cards = self._decks.get(deck_id)
        return _FakeSnapshot(cards) if cards is not None else None


class TestAllocationContext:
    def test_blocked_copies_surface_in_deck_value(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        vs = _FakeVersionStore({"other": {"Lightning Bolt": 1}})
        v = value_deck(_deck({"Lightning Bolt": 1}), store, db, vs, deck_id="mine")
        row = v["cards"][0]
        assert row["blocked"] == 1
        assert row["missing"] == 0
        # Owned but unavailable is not a purchase.
        assert v["cost_to_complete_usd"] == 0.0

    def test_deck_excluded_from_its_own_commitment(self, env):
        store, db = env
        store.add_copies(BOLT, "Lightning Bolt", quantity=1)
        vs = _FakeVersionStore({"mine": {"Lightning Bolt": 1}})
        v = value_deck(_deck({"Lightning Bolt": 1}), store, db, vs, deck_id="mine")
        assert v["cards"][0]["blocked"] == 0


class TestEdges:
    def test_empty_deck(self, env):
        store, db = env
        v = value_deck(_deck({}), store, db)
        assert v["distinct_cards"] == 0
        assert v["deck_value_usd"] == 0.0

    def test_no_catalogue_reports_everything_unpriced(self, env):
        store, db = env
        conn = db.connect()
        conn.execute("DELETE FROM card_printings")
        conn.commit()
        v = value_deck(_deck({"Sol Ring": 1}), store, db)
        assert v["build_value_usd"] == 0.0
        assert v["build_value_unpriced"] == 1

    def test_duplicate_entries_summed(self, env):
        store, db = env
        deck = Deck(name="T", entries=[
            DeckEntry(card_name="Lightning Bolt", quantity=1, zone=Zone.MAINBOARD),
            DeckEntry(card_name="Lightning Bolt", quantity=3, zone=Zone.SIDEBOARD),
        ])
        v = value_deck(deck, store, db)
        assert v["cards"][0]["needed"] == 4
        assert v["build_value_usd"] == 40.00

    def test_case_insensitive_matching(self, env):
        store, db = env
        store.add_copies(BOLT, "lightning bolt", quantity=1)
        v = value_deck(_deck({"Lightning Bolt": 1}), store, db)
        assert v["cards"][0]["owned"] == 1


class TestBulkPriceLookup:
    def test_chunks_beyond_sqlite_parameter_limit(self, env):
        """A 1,000-card name list must not blow the host-parameter cap."""
        _, db = env
        names = [f"Fake Card {i}" for i in range(1000)] + ["Lightning Bolt"]
        prices = db.cheapest_prices_for_names(names)
        assert prices["lightning bolt"] == 10.00

    def test_empty_list(self, env):
        _, db = env
        assert db.cheapest_prices_for_names([]) == {}

    def test_unpriced_names_absent_not_zero(self, env):
        _, db = env
        prices = db.cheapest_prices_for_names(["Obscure Card"])
        assert "obscure card" not in prices


class TestOwnershipSearchFilter:
    """Build-tab "owned only" filters in SQL across both databases."""

    @pytest.fixture
    def searchable(self, env):
        from densa_deck.models import Card, CardLayout, Legality
        store, db = env
        db.upsert_cards([
            Card(scryfall_id="c1", oracle_id="o-sol", name="Sol Ring",
                 layout=CardLayout.NORMAL, type_line="Artifact",
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="c2", oracle_id="o-bolt", name="Lightning Bolt",
                 layout=CardLayout.NORMAL, type_line="Instant",
                 legalities={"commander": Legality.LEGAL}),
            Card(scryfall_id="c3", oracle_id="o-obs", name="Obscure Card",
                 layout=CardLayout.NORMAL, type_line="Sorcery",
                 legalities={"commander": Legality.LEGAL}),
        ])
        store.add_copies(CHEAP_SOL, "Sol Ring", quantity=1)
        assert db.attach_collection(store.db_path)
        return store, db

    def test_owned_only(self, searchable):
        _, db = searchable
        cards, total = db.search_structured(ownership="owned")
        assert total == 1
        assert cards[0].name == "Sol Ring"

    def test_unowned_only(self, searchable):
        _, db = searchable
        cards, total = db.search_structured(ownership="unowned")
        assert total == 2
        assert "Sol Ring" not in [c.name for c in cards]

    def test_no_filter_returns_everything(self, searchable):
        _, db = searchable
        _, total = db.search_structured()
        assert total == 3

    def test_combines_with_other_filters(self, searchable):
        _, db = searchable
        _, total = db.search_structured(ownership="owned", types=["instant"])
        assert total == 0

    def test_ownership_is_case_insensitive(self, env):
        from densa_deck.models import Card, CardLayout
        store, db = env
        db.upsert_cards([Card(scryfall_id="c1", oracle_id="o", name="Sol Ring",
                              layout=CardLayout.NORMAL, type_line="Artifact")])
        store.add_copies(CHEAP_SOL, "sol ring", quantity=1)
        db.attach_collection(store.db_path)
        _, total = db.search_structured(ownership="owned")
        assert total == 1

    def test_zero_quantity_stacks_do_not_count_as_owned(self, searchable):
        store, db = searchable
        store.remove_copies(CHEAP_SOL, "Sol Ring", quantity=1)
        _, total = db.search_structured(ownership="owned")
        assert total == 0

    def test_attach_is_idempotent(self, searchable):
        store, db = searchable
        assert db.attach_collection(store.db_path)
        assert db.attach_collection(store.db_path)
        _, total = db.search_structured(ownership="owned")
        assert total == 1

    def test_attach_missing_file_reports_false(self, env):
        _, db = env
        assert db.attach_collection(Path("does-not-exist.db")) is False
