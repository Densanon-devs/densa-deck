"""Printing-level allocation — binding specific physical copies to decks."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.allocation import (
    allocate,
    allocation_map,
    allocations_for_deck,
    clear_deck_allocations,
    deallocate,
    reconcile,
    unallocated_copies,
)
from densa_deck.collection.storage import CollectionStore
from densa_deck.data.database import CardDatabase, printing_row_from_scryfall

CHEAP = "cheap-1"
FOIL = "foil-1"


def _raw(pid, name, set_code, num, *, usd="1.50", foil=None,
         finishes=("nonfoil",)):
    return {
        "id": pid, "oracle_id": "o-sol", "name": name, "set": set_code,
        "set_name": set_code.upper(), "collector_number": num, "rarity": "rare",
        "lang": "en", "released_at": "2020-01-01", "finishes": list(finishes),
        "frame": "2015", "border_color": "black", "promo_types": [],
        "games": ["paper"], "tcgplayer_id": 1,
        "prices": {"usd": usd, "usd_foil": foil, "usd_etched": None},
    }


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = CardDatabase(db_path=root / "cards.db")
        db.upsert_printings([
            printing_row_from_scryfall(_raw(CHEAP, "Sol Ring", "cmm", "410"), "t"),
            printing_row_from_scryfall(
                _raw(FOIL, "Sol Ring", "sld", "99", usd="200.00", foil="600.00",
                     finishes=("nonfoil", "foil")), "t"),
        ])
        store = CollectionStore(db_path=root / "collection.db")
        yield store, db
        db.close()


class TestAllocate:
    def test_bind_a_copy_to_a_deck(self, env):
        store, db = env
        item = store.add_copies(FOIL, "Sol Ring", quantity=1, finish="foil")
        r = allocate(store, "edh-deck", item.item_id)
        assert r["card_name"] == "Sol Ring"
        assert unallocated_copies(store, item.item_id) == 0

    def test_partial_allocation_leaves_the_rest_free(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=4)
        allocate(store, "deck-a", item.item_id, quantity=3)
        assert unallocated_copies(store, item.item_id) == 1

    def test_cannot_allocate_more_than_owned(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=2)
        with pytest.raises(ValueError, match="unallocated"):
            allocate(store, "deck-a", item.item_id, quantity=3)

    def test_second_deck_cannot_take_a_spoken_for_copy(self, env):
        """The whole point: stop the same cardboard counting twice."""
        store, _ = env
        item = store.add_copies(FOIL, "Sol Ring", quantity=1, finish="foil")
        allocate(store, "deck-a", item.item_id)
        with pytest.raises(ValueError, match="free a copy"):
            allocate(store, "deck-b", item.item_id)

    def test_reallocating_the_same_slot_updates_rather_than_stacks(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=4)
        allocate(store, "deck-a", item.item_id, quantity=1)
        allocate(store, "deck-a", item.item_id, quantity=3)
        assert unallocated_copies(store, item.item_id) == 1

    def test_zones_are_separate_slots(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=2)
        allocate(store, "deck-a", item.item_id, zone="mainboard")
        allocate(store, "deck-a", item.item_id, zone="sideboard")
        assert unallocated_copies(store, item.item_id) == 0

    def test_unknown_item_refused(self, env):
        store, _ = env
        with pytest.raises(ValueError, match="no collection item"):
            allocate(store, "deck-a", 9999)

    def test_non_positive_quantity_refused(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=1)
        with pytest.raises(ValueError):
            allocate(store, "deck-a", item.item_id, quantity=0)


class TestDeallocate:
    def test_frees_the_copy(self, env):
        store, _ = env
        item = store.add_copies(FOIL, "Sol Ring", quantity=1, finish="foil")
        allocate(store, "deck-a", item.item_id)
        assert deallocate(store, "deck-a", item.item_id) is True
        assert unallocated_copies(store, item.item_id) == 1

    def test_freed_copy_can_go_to_another_deck(self, env):
        store, _ = env
        item = store.add_copies(FOIL, "Sol Ring", quantity=1, finish="foil")
        allocate(store, "deck-a", item.item_id)
        deallocate(store, "deck-a", item.item_id)
        allocate(store, "deck-b", item.item_id)   # must not raise
        assert unallocated_copies(store, item.item_id) == 0

    def test_deallocating_nothing_reports_false(self, env):
        store, _ = env
        assert deallocate(store, "deck-a", 1) is False

    def test_clearing_a_deck_frees_everything(self, env):
        store, _ = env
        a = store.add_copies(CHEAP, "Sol Ring", quantity=1)
        b = store.add_copies(FOIL, "Sol Ring", quantity=1, finish="foil")
        allocate(store, "deck-a", a.item_id)
        allocate(store, "deck-a", b.item_id)
        assert clear_deck_allocations(store, "deck-a") == 2
        assert unallocated_copies(store, a.item_id) == 1


class TestReads:
    def test_deck_view_carries_printing_detail(self, env):
        store, db = env
        item = store.add_copies(FOIL, "Sol Ring", quantity=1, finish="foil",
                                location="Binder")
        allocate(store, "edh", item.item_id)
        rows = allocations_for_deck(store, db, "edh")
        assert len(rows) == 1
        r = rows[0]
        assert r["set_code"] == "sld"
        assert r["finish"] == "foil"
        assert r["location"] == "Binder"
        assert r["unit_price_usd"] == 600.00   # foil priced as foil

    def test_allocation_map(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=4)
        allocate(store, "a", item.item_id, quantity=2)
        assert allocation_map(store)[str(item.item_id)] == 2

    def test_unallocated_for_missing_item(self, env):
        store, _ = env
        assert unallocated_copies(store, 4242) == 0


class TestReconcile:
    def test_drops_allocations_for_deleted_stacks(self, env):
        """A sold card must not stay permanently earmarked."""
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=1)
        allocate(store, "deck-a", item.item_id)
        store.delete_item(item.item_id)
        result = reconcile(store)
        assert result["removed"] == 1
        assert allocation_map(store) == {}

    def test_trims_allocations_that_exceed_a_shrunken_stack(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=4)
        allocate(store, "deck-a", item.item_id, quantity=4)
        store.set_item_quantity(item.item_id, 2)
        result = reconcile(store)
        assert result["trimmed"] == [{"card_name": "Sol Ring", "was": 4, "now": 2}]
        assert allocation_map(store)[str(item.item_id)] == 2

    def test_healthy_data_is_untouched(self, env):
        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=2)
        allocate(store, "deck-a", item.item_id, quantity=1)
        result = reconcile(store)
        assert result == {"removed": 0, "trimmed": []}
        assert allocation_map(store)[str(item.item_id)] == 1

    def test_reconcile_on_empty_store(self, env):
        store, _ = env
        assert reconcile(store) == {"removed": 0, "trimmed": []}


class TestDefaultBehaviourUnchanged:
    def test_oracle_level_ownership_ignores_allocations(self, env):
        """A deck with no allocations must behave exactly as before.

        Allocation is advisory. It refines which object is earmarked; it must
        never change what the collection reports owning.
        """
        from densa_deck.collection.ownership import ownership_rows

        store, _ = env
        item = store.add_copies(CHEAP, "Sol Ring", quantity=4)
        before = ownership_rows(store)["sol ring"].owned
        allocate(store, "deck-a", item.item_id, quantity=2)
        after = ownership_rows(store)["sol ring"].owned
        assert before == after == 4
