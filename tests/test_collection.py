"""Collection storage, printings, and ownership maths."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.models import (
    CONDITION_MULTIPLIERS,
    CollectionItem,
    Condition,
    Finish,
    OwnershipRow,
)
from densa_deck.collection.ownership import (
    committed_by_name,
    ownership_for_deck,
    ownership_rows,
)
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.models import Deck, DeckEntry, Zone

# Two printings of the same card with wildly different prices — the whole
# reason printing-level tracking exists.
SOL_RING_CMM = "11111111-1111-1111-1111-111111111111"
SOL_RING_SLD = "22222222-2222-2222-2222-222222222222"
SOL_RING_ORACLE = "6ad8011d-3471-4369-9d68-b264cc027487"
SKITHIRYX_SOM = "33333333-3333-3333-3333-333333333333"


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield CollectionStore(db_path=Path(tmp) / "collection.db")


@pytest.fixture
def card_db():
    with tempfile.TemporaryDirectory() as tmp:
        db = CardDatabase(db_path=Path(tmp) / "cards.db")
        yield db
        db.close()


def _raw_printing(pid, name, set_code, num, *, oracle=SOL_RING_ORACLE,
                  usd="1.50", foil=None, finishes=("nonfoil",), games=("paper",),
                  released="2023-08-04", set_name="Test Set"):
    return {
        "id": pid, "oracle_id": oracle, "name": name, "set": set_code,
        "set_name": set_name, "collector_number": num, "rarity": "uncommon",
        "lang": "en", "released_at": released, "finishes": list(finishes),
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": list(games), "tcgplayer_id": 12345,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": None},
    }


class TestPrintingRowParsing:
    def test_paper_printing_is_kept(self):
        row = printing_row_from_scryfall(_raw_printing(SOL_RING_CMM, "Sol Ring", "cmm", "410"), "now")
        assert row is not None
        assert row[0] == SOL_RING_CMM
        assert row[3] == "cmm"  # set code lowercased

    def test_digital_only_printing_is_dropped(self):
        # You cannot physically own an Arena card.
        raw = _raw_printing("x", "Sol Ring", "ana", "1", games=("arena", "mtgo"))
        assert printing_row_from_scryfall(raw, "now") is None

    def test_finishes_and_prices_split_out(self):
        raw = _raw_printing(SOL_RING_SLD, "Sol Ring", "sld", "99",
                            usd="21.72", foil="30.00", finishes=("nonfoil", "foil"))
        row = printing_row_from_scryfall(raw, "now")
        assert row[9] == "nonfoil,foil"
        assert row[14] == 21.72   # price_usd
        assert row[15] == 30.00   # price_usd_foil

    def test_missing_price_stays_none_not_zero(self):
        # NULL means unknown, never free. An unpriced card counted as $0
        # would silently understate every collection total.
        raw = _raw_printing("y", "Sol Ring", "abc", "1", usd=None)
        row = printing_row_from_scryfall(raw, "now")
        assert row[14] is None

    def test_unparseable_price_degrades_to_none(self):
        raw = _raw_printing("z", "Sol Ring", "abc", "1", usd="not-a-number")
        assert printing_row_from_scryfall(raw, "now")[14] is None


class TestPrintingsTable:
    def test_upsert_and_lookup(self, card_db):
        rows = [
            printing_row_from_scryfall(_raw_printing(SOL_RING_CMM, "Sol Ring", "cmm", "410"), "t"),
            printing_row_from_scryfall(
                _raw_printing(SOL_RING_SLD, "Sol Ring", "sld", "99", usd="21.72"), "t"),
        ]
        card_db.upsert_printings(rows)
        assert card_db.printing_count() == 2

        found = card_db.printings_for_card("sol ring")
        assert len(found) == 2
        assert {f["set_code"] for f in found} == {"cmm", "sld"}

    def test_lookup_by_set_and_collector_number(self, card_db):
        # The scanner's fast path: modern cards print these two fields on the
        # card face, which identifies the exact printing with no image work.
        card_db.upsert_printings([
            printing_row_from_scryfall(_raw_printing(SOL_RING_CMM, "Sol Ring", "cmm", "410"), "t")
        ])
        hit = card_db.find_printing_by_set_number("CMM", "410")
        assert hit is not None and hit["printing_id"] == SOL_RING_CMM
        assert card_db.find_printing_by_set_number("cmm", "999") is None

    def test_cheapest_printing_skips_unpriced(self, card_db):
        card_db.upsert_printings([
            printing_row_from_scryfall(_raw_printing("a", "Sol Ring", "s1", "1", usd=None), "t"),
            printing_row_from_scryfall(_raw_printing("b", "Sol Ring", "s2", "2", usd="1.50"), "t"),
            printing_row_from_scryfall(_raw_printing("c", "Sol Ring", "s3", "3", usd="21.72"), "t"),
        ])
        cheapest = card_db.cheapest_printing_for_card("Sol Ring")
        assert cheapest["printing_id"] == "b"

    def test_upsert_is_idempotent(self, card_db):
        row = printing_row_from_scryfall(_raw_printing(SOL_RING_CMM, "Sol Ring", "cmm", "410"), "t")
        card_db.upsert_printings([row])
        card_db.upsert_printings([row])
        assert card_db.printing_count() == 1

    def test_printings_survive_a_card_reingest(self, card_db):
        """The reason printings and ownership are not columns on `cards`.

        upsert_cards is INSERT OR REPLACE over an explicit column list, which
        is DELETE + INSERT — anything outside that list gets wiped. Printings
        live in their own table precisely so a card refresh can't erase them.
        """
        from densa_deck.models import Card, CardLayout

        card_db.upsert_printings([
            printing_row_from_scryfall(_raw_printing(SOL_RING_CMM, "Sol Ring", "cmm", "410"), "t")
        ])
        card_db.upsert_cards([Card(
            scryfall_id=SOL_RING_CMM, oracle_id=SOL_RING_ORACLE, name="Sol Ring",
            layout=CardLayout.NORMAL, type_line="Artifact",
        )])
        assert card_db.printing_count() == 1


class TestAddAndRemove:
    def test_add_creates_a_stack(self, store):
        item = store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=2, oracle_id=SOL_RING_ORACLE)
        assert item.quantity == 2
        assert store.owned_count("Sol Ring") == 2

    def test_adding_twice_increments_one_stack(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=3)
        items, total = store.list_items()
        assert total == 1
        assert items[0].quantity == 4

    def test_different_printings_are_different_stacks(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        store.add_copies(SOL_RING_SLD, "Sol Ring", quantity=1)
        items, total = store.list_items()
        assert total == 2
        assert store.owned_count("Sol Ring") == 2

    def test_finish_and_condition_separate_stacks(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, finish=Finish.NONFOIL)
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, finish=Finish.FOIL)
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, condition=Condition.LP)
        _, total = store.list_items()
        assert total == 3

    def test_location_separates_stacks(self, store):
        # The same card in two boxes is two stacks — that's how people
        # actually find cardboard.
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, location="Binder A")
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, location="Bulk box")
        _, total = store.list_items()
        assert total == 2
        assert sorted(store.locations()) == ["Binder A", "Bulk box"]

    def test_removing_to_zero_deletes_the_stack(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=2)
        store.remove_copies(SOL_RING_CMM, "Sol Ring", quantity=2)
        items, total = store.list_items()
        assert total == 0
        assert store.owned_count("Sol Ring") == 0

    def test_removing_more_than_owned_floors_at_zero(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        store.remove_copies(SOL_RING_CMM, "Sol Ring", quantity=5)
        assert store.owned_count("Sol Ring") == 0

    def test_removing_from_nothing_is_a_noop(self, store):
        item = store.remove_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        assert item.quantity == 0
        assert store.owned_count("Sol Ring") == 0

    def test_set_quantity_exact(self, store):
        item = store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=2)
        assert store.set_item_quantity(item.item_id, 7)
        assert store.owned_count("Sol Ring") == 7

    def test_set_quantity_zero_removes(self, store):
        item = store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=2)
        store.set_item_quantity(item.item_id, 0)
        assert store.owned_count("Sol Ring") == 0

    def test_set_quantity_on_missing_item(self, store):
        assert store.set_item_quantity(9999, 3) is False

    def test_update_item_metadata(self, store):
        item = store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        assert store.update_item(item.item_id, location="Deck box 3", notes="signed")
        fetched = store.get_item(item.item_id)
        assert fetched.location == "Deck box 3"
        assert fetched.notes == "signed"

    def test_update_rejects_unknown_fields(self, store):
        item = store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        assert store.update_item(item.item_id, quantity=999) is False
        assert store.owned_count("Sol Ring") == 1

    def test_requires_printing_and_name(self, store):
        with pytest.raises(ValueError):
            store.add_copies("", "Sol Ring")
        with pytest.raises(ValueError):
            store.add_copies(SOL_RING_CMM, "")


class TestEventLog:
    def test_every_change_is_logged(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=2)
        store.remove_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        events = store.recent_events()
        assert len(events) == 2
        assert events[0]["delta"] == -1
        assert events[1]["delta"] == 2

    def test_event_survives_stack_deletion(self, store):
        # "Where did those four copies go" must stay answerable after the
        # stack itself is gone — this is also Phase 5's cost-basis ledger.
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        store.remove_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        assert len(store.recent_events()) == 2


class TestQueriesAndSummary:
    def test_filter_by_name(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        store.add_copies(SKITHIRYX_SOM, "Skithiryx, the Blight Dragon", quantity=1)
        items, total = store.list_items(name_like="skith")
        assert total == 1
        assert items[0].card_name.startswith("Skithiryx")

    def test_filter_by_finish(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, finish=Finish.FOIL)
        store.add_copies(SOL_RING_SLD, "Sol Ring", quantity=1)
        items, total = store.list_items(finish="foil")
        assert total == 1

    def test_pagination(self, store):
        for i in range(10):
            store.add_copies(f"pid-{i}", f"Card {i:02d}", quantity=1)
        page, total = store.list_items(limit=3, offset=0)
        assert total == 10 and len(page) == 3

    def test_summary_counts(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=2)
        store.add_copies(SOL_RING_SLD, "Sol Ring", quantity=1, finish=Finish.FOIL)
        store.add_copies(SKITHIRYX_SOM, "Skithiryx, the Blight Dragon", quantity=1)
        s = store.summary()
        assert s.total_cards == 4
        assert s.unique_cards == 2       # two distinct card names
        assert s.unique_printings == 3   # three distinct printings
        assert s.by_finish["foil"] == 1

    def test_owned_by_name_is_case_insensitive(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        store.add_copies(SOL_RING_SLD, "sol ring", quantity=2)
        assert store.owned_by_name()["sol ring"] == 3

    def test_owned_by_oracle_aggregates_printings(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1, oracle_id=SOL_RING_ORACLE)
        store.add_copies(SOL_RING_SLD, "Sol Ring", quantity=2, oracle_id=SOL_RING_ORACLE)
        assert store.owned_by_oracle()[SOL_RING_ORACLE] == 3

    def test_clear(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=3)
        assert store.clear() == 1
        assert store.summary().total_cards == 0


class TestConditionPricing:
    def test_multiplier_applied(self):
        item = CollectionItem(printing_id="p", card_name="Sol Ring",
                              condition=Condition.MP, quantity=2, unit_price_usd=10.0)
        assert item.condition_adjusted_price == 7.0
        assert item.stack_value_usd == 14.0

    def test_nm_is_full_price(self):
        item = CollectionItem(printing_id="p", card_name="Sol Ring",
                              condition=Condition.NM, quantity=1, unit_price_usd=10.0)
        assert item.condition_adjusted_price == 10.0

    def test_unknown_price_stays_unknown(self):
        item = CollectionItem(printing_id="p", card_name="Sol Ring", quantity=3)
        assert item.condition_adjusted_price is None
        assert item.stack_value_usd is None

    def test_multipliers_are_monotonic(self):
        order = [Condition.NM, Condition.LP, Condition.MP, Condition.HP, Condition.DMG]
        vals = [CONDITION_MULTIPLIERS[c] for c in order]
        assert vals == sorted(vals, reverse=True)


class _FakeSnapshot:
    def __init__(self, decklist):
        self.decklist = decklist


class _FakeVersionStore:
    """Stands in for VersionStore — only list_decks/get_latest are used."""

    def __init__(self, decks: dict[str, dict[str, int]]):
        self._decks = decks

    def list_decks(self):
        return [{"deck_id": d} for d in self._decks]

    def get_latest(self, deck_id):
        cards = self._decks.get(deck_id)
        return _FakeSnapshot(cards) if cards is not None else None


class TestOwnershipMaths:
    def test_available_is_owned_minus_committed(self):
        row = OwnershipRow(card_name="Sol Ring", owned=4, committed=3)
        assert row.available == 1
        assert row.shortfall == 0

    def test_over_commitment_reports_shortfall_not_negative(self):
        row = OwnershipRow(card_name="Sol Ring", owned=1, committed=3)
        assert row.available == 0
        assert row.shortfall == 2

    def test_committed_counts_latest_version_of_each_deck(self):
        vs = _FakeVersionStore({"deck-a": {"Sol Ring": 1}, "deck-b": {"Sol Ring": 1}})
        assert committed_by_name(vs)["sol ring"] == 2

    def test_committed_can_exclude_one_deck(self):
        vs = _FakeVersionStore({"deck-a": {"Sol Ring": 1}, "deck-b": {"Sol Ring": 1}})
        assert committed_by_name(vs, exclude_deck_id="deck-a")["sol ring"] == 1

    def test_committed_tolerates_a_broken_deck(self):
        class Broken(_FakeVersionStore):
            def get_latest(self, deck_id):
                if deck_id == "bad":
                    raise RuntimeError("corrupt snapshot")
                return super().get_latest(deck_id)

        vs = Broken({"bad": {"Sol Ring": 9}, "good": {"Sol Ring": 1}})
        # One unreadable deck understates commitment; it must not break the view.
        assert committed_by_name(vs)["sol ring"] == 1

    def test_no_version_store_means_nothing_committed(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=4)
        rows = ownership_rows(store)
        assert rows["sol ring"].owned == 4
        assert rows["sol ring"].committed == 0

    def test_three_decks_sharing_a_playset(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=4)
        vs = _FakeVersionStore({
            "a": {"Sol Ring": 1}, "b": {"Sol Ring": 1}, "c": {"Sol Ring": 1},
        })
        rows = ownership_rows(store, vs)
        assert rows["sol ring"].owned == 4
        assert rows["sol ring"].committed == 3
        assert rows["sol ring"].available == 1


class TestOwnershipForDeck:
    def _deck(self, cards: dict[str, int]):
        return Deck(
            name="Test",
            entries=[DeckEntry(card_name=n, quantity=q, zone=Zone.MAINBOARD)
                     for n, q in cards.items()],
        )

    def test_owned_and_missing_split(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        result = ownership_for_deck(
            self._deck({"Sol Ring": 1, "Phyrexian Crusader": 1}), store)
        assert result["owned_distinct"] == 1
        assert result["missing_distinct"] == 1
        assert result["missing_copies"] == 1

    def test_partial_ownership_counts_the_gap(self, store):
        store.add_copies(SOL_RING_CMM, "Lightning Bolt", quantity=2)
        result = ownership_for_deck(self._deck({"Lightning Bolt": 4}), store)
        row = result["cards"][0]
        assert row["owned"] == 2 and row["needed"] == 4 and row["missing"] == 2

    def test_deck_does_not_compete_with_itself(self, store):
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        vs = _FakeVersionStore({"mine": {"Sol Ring": 1}})
        result = ownership_for_deck(self._deck({"Sol Ring": 1}), store, vs, deck_id="mine")
        row = result["cards"][0]
        assert row["missing"] == 0
        assert row["blocked"] == 0

    def test_copy_committed_elsewhere_is_blocked_not_missing(self, store):
        # Owning the card but having it sleeved in another deck is a different
        # problem from not owning it: unsleeve vs buy.
        store.add_copies(SOL_RING_CMM, "Sol Ring", quantity=1)
        vs = _FakeVersionStore({"other": {"Sol Ring": 1}})
        result = ownership_for_deck(self._deck({"Sol Ring": 1}), store, vs, deck_id="mine")
        row = result["cards"][0]
        assert row["missing"] == 0
        assert row["blocked"] == 1
        assert result["blocked_distinct"] == 1

    def test_empty_deck(self, store):
        result = ownership_for_deck(self._deck({}), store)
        assert result["distinct_cards"] == 0
        assert result["cards"] == []

    def test_duplicate_entries_are_summed(self, store):
        deck = Deck(name="T", entries=[
            DeckEntry(card_name="Sol Ring", quantity=1, zone=Zone.MAINBOARD),
            DeckEntry(card_name="Sol Ring", quantity=2, zone=Zone.SIDEBOARD),
        ])
        result = ownership_for_deck(deck, store)
        assert result["cards"][0]["needed"] == 3


class TestPrintingResolution:
    """`collection add` must never file the wrong card.

    Set code + collector number identifies a printing on its own, so the card
    name is redundant — but when the two disagree one of them is a typo, and
    guessing would put the wrong cardboard in someone's collection.
    """

    @pytest.fixture
    def db_two_cards(self, card_db):
        card_db.upsert_printings([
            printing_row_from_scryfall(
                _raw_printing(SOL_RING_CMM, "Sol Ring", "cmm", "410"), "t"),
            printing_row_from_scryfall(
                _raw_printing("plains-1", "Plains", "som", "233",
                              oracle="oid-plains"), "t"),
            printing_row_from_scryfall(
                _raw_printing(SOL_RING_SLD, "Sol Ring", "sld", "99"), "t"),
        ])
        return card_db

    def test_exact_set_and_number_resolves(self, db_two_cards):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(db_two_cards, "Sol Ring", "cmm", "410")
        assert err is None
        assert hit["printing_id"] == SOL_RING_CMM

    def test_name_mismatch_is_refused(self, db_two_cards):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(db_two_cards, "Sol Ring", "som", "233")
        assert hit is None
        assert "is 'Plains', not 'Sol Ring'" in err

    def test_case_insensitive_name_match(self, db_two_cards):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(db_two_cards, "  sol ring  ", "cmm", "410")
        assert err is None and hit["printing_id"] == SOL_RING_CMM

    def test_ambiguous_name_is_refused(self, db_two_cards):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(db_two_cards, "Sol Ring", None, None)
        assert hit is None
        assert "2 printings" in err

    def test_set_alone_disambiguates(self, db_two_cards):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(db_two_cards, "Sol Ring", "sld", None)
        assert err is None and hit["printing_id"] == SOL_RING_SLD

    def test_unknown_printing_reports_clearly(self, db_two_cards):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(db_two_cards, "Sol Ring", "zzz", "1")
        assert hit is None and "No printing ZZZ #1" in err

    def test_empty_catalogue_points_at_sync(self, card_db):
        from densa_deck.cli import _resolve_one_printing
        hit, err = _resolve_one_printing(card_db, "Sol Ring", None, None)
        assert hit is None and "collection sync" in err
