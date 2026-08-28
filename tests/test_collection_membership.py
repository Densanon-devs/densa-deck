"""Collections as filters rather than boxes.

A physical card is in one physical box, and `collection_items.collection_id`
says which. That is a different question from which LISTS it belongs to: the
same card can be part of a set you are completing, a deck you have built, and
the seventy-five you took to a tournament, all at once and without moving.

So membership is many-to-many, and the rule that has to hold above all others
is that a filter cannot destroy what it filters. Taking a card out of a list
must never take it out of the collection.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.collection.storage import CollectionStore


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield CollectionStore(db_path=Path(tmp) / "collection.db")


def _stack(store, name="Sol Ring", printing="p1", quantity=1):
    return store.add_copies(printing, name, quantity=quantity)


def _collection(store, name):
    made = store.create_collection(name)
    return made["collection_id"]


def _owned(store):
    items, _ = store.list_items(limit=200)
    return sum(i.quantity for i in items)


class TestBelongingToSeveralAtOnce:
    def test_a_card_can_be_in_two_lists(self, store):
        item = _stack(store)
        store.add_to_collection(item.item_id, _collection(store, "Ravnica set"))
        store.add_to_collection(item.item_id, _collection(store, "Tournament 75"))

        names = {c["name"] for c in store.collections_for_item(item.item_id)}
        assert {"Ravnica set", "Tournament 75"} <= names

    def test_adding_to_one_does_not_remove_it_from_another(self, store):
        """The entire difference from the old model, in one assertion."""
        item = _stack(store)
        first = _collection(store, "Ravnica set")
        store.add_to_collection(item.item_id, first)
        store.add_to_collection(item.item_id, _collection(store, "Deck"))

        assert first in {c["collection_id"] for c in store.collections_for_item(item.item_id)}

    def test_adding_twice_is_not_an_error_and_not_a_duplicate(self, store):
        item = _stack(store)
        target = _collection(store, "Ravnica set")
        assert store.add_to_collection(item.item_id, target) is True
        assert store.add_to_collection(item.item_id, target) is False
        assert len(store.collections_for_item(item.item_id)) == 2  # + default

    def test_it_stays_in_the_default_pile_as_well(self, store):
        # The default is "cards I haven't filed anywhere". Being in a named
        # list does not stop a card being owned, and the master collection is
        # still the sum of the physical cards.
        item = _stack(store)
        store.add_to_collection(item.item_id, _collection(store, "Deck"))
        assert _owned(store) == 1


class TestAFilterCannotDestroyWhatItFilters:
    def test_removing_from_a_list_keeps_the_card(self, store):
        item = _stack(store, quantity=3)
        target = _collection(store, "Deck")
        store.add_to_collection(item.item_id, target)

        store.remove_from_collection(item.item_id, target)

        assert _owned(store) == 3
        assert target not in {c["collection_id"] for c in store.collections_for_item(item.item_id)}

    def test_removing_from_every_list_still_keeps_the_card(self, store):
        item = _stack(store, quantity=2)
        for c in store.collections_for_item(item.item_id):
            store.remove_from_collection(item.item_id, c["collection_id"])

        assert store.collections_for_item(item.item_id) == []
        assert _owned(store) == 2, "a card in no list is still a card you own"

    def test_removing_something_that_was_not_there_is_a_no_op(self, store):
        item = _stack(store)
        assert store.remove_from_collection(item.item_id, _collection(store, "Deck")) is False
        assert _owned(store) == 1


class TestMovingRatherThanTagging:
    def test_a_move_replaces_every_list(self, store):
        """For when the card really has gone in another box."""
        item = _stack(store)
        store.add_to_collection(item.item_id, _collection(store, "Deck"))
        binder = _collection(store, "Trade binder")

        store.move_to_collection(item.item_id, binder)

        names = [c["name"] for c in store.collections_for_item(item.item_id)]
        assert names == ["Trade binder"]

    def test_a_move_also_changes_where_it_is_filed(self, store):
        item = _stack(store)
        binder = _collection(store, "Trade binder")
        store.move_to_collection(item.item_id, binder)

        items, _ = store.list_items(limit=10)
        assert items[0].collection_id == binder

    def test_a_move_does_not_change_how_many_you_own(self, store):
        item = _stack(store, quantity=4)
        store.move_to_collection(item.item_id, _collection(store, "Trade binder"))
        assert _owned(store) == 4


class TestTheOverlapView:
    def test_a_card_in_one_list_is_not_an_overlap(self, store):
        _stack(store)
        assert store.overlaps() == []

    def test_a_card_in_two_lists_shows_up(self, store):
        item = _stack(store)
        store.add_to_collection(item.item_id, _collection(store, "Ravnica set"))

        rows = store.overlaps()
        assert len(rows) == 1
        assert rows[0]["collection_count"] == 2
        assert "Ravnica set" in rows[0]["collections"]

    def test_owning_enough_copies_is_not_overcommitted(self, store):
        """Two lists, two copies. Nothing is wrong — it is doing two jobs."""
        item = _stack(store, quantity=2)
        store.add_to_collection(item.item_id, _collection(store, "Deck A"))

        assert store.overlaps()[0]["overcommitted"] is False

    def test_more_lists_than_copies_is_flagged(self, store):
        """The case worth surfacing: you find out at the table otherwise."""
        item = _stack(store, quantity=1)
        store.add_to_collection(item.item_id, _collection(store, "Deck A"))
        store.add_to_collection(item.item_id, _collection(store, "Deck B"))

        row = store.overlaps()[0]
        assert row["collection_count"] == 3
        assert row["quantity"] == 1
        assert row["overcommitted"] is True

    def test_it_names_the_lists_so_you_know_where_to_look(self, store):
        item = _stack(store)
        store.add_to_collection(item.item_id, _collection(store, "Deck A"))
        store.add_to_collection(item.item_id, _collection(store, "Deck B"))

        assert {"Deck A", "Deck B"} <= set(store.overlaps()[0]["collections"])

    def test_a_card_you_no_longer_own_drops_out(self, store):
        # Otherwise removing the last copy leaves a conflict listed for a
        # card that is not in the house.
        item = _stack(store)
        store.add_to_collection(item.item_id, _collection(store, "Deck A"))
        store.add_copies("p1", "Sol Ring", quantity=-1)

        assert store.overlaps() == []

    def test_the_most_contested_card_comes_first(self, store):
        one = _stack(store, "Sol Ring", "p1")
        two = _stack(store, "Arcane Signet", "p2")
        store.add_to_collection(one.item_id, _collection(store, "Deck A"))
        store.add_to_collection(two.item_id, _collection(store, "Deck B"))
        store.add_to_collection(two.item_id, _collection(store, "Deck C"))

        assert store.overlaps()[0]["card_name"] == "Arcane Signet"


class TestUpgradingAnExistingDatabase:
    def test_what_was_filed_is_what_it_belongs_to(self, store):
        """A collection that suddenly read as empty would look like loss."""
        binder = _collection(store, "Trade binder")
        store.add_copies("p9", "Black Lotus", quantity=1, collection_id=binder)

        # Reopening runs the migration against a database that already has
        # membership rows; it must not undo anything.
        again = CollectionStore(db_path=store.db_path)
        items, _ = again.list_items(limit=10)
        filed = [i for i in items if i.card_name == "Black Lotus"][0]
        assert binder in {c["collection_id"]
                          for c in again.collections_for_item(filed.item_id)}

    def test_the_backfill_does_not_re_run_over_edits(self, store):
        """Once you have moved things about, `collection_id` is no longer the
        whole story and re-running would quietly undo your work."""
        item = _stack(store)
        deck = _collection(store, "Deck")
        store.add_to_collection(item.item_id, deck)
        before = {c["collection_id"] for c in store.collections_for_item(item.item_id)}

        again = CollectionStore(db_path=store.db_path)
        after = {c["collection_id"] for c in again.collections_for_item(item.item_id)}
        assert after == before


class TestDeletingAListLeavesNothingPointingAtIt:
    """A collection is gone; the rows saying what was in it must go with it.

    Membership rows outlive their collection harmlessly only because
    `collection_id` is AUTOINCREMENT and a deleted id is never reissued. That
    is a property of the sequence counter, not of the data — a restore, a
    rebuild, or an export/import that renumbers would hand a new list the old
    id, and it would open holding cards nobody put in it.

    The phone already cleaned these up in `deleteCollection`; the desktop did
    not, so the same action left the two sides describing different worlds.
    """

    def _orphans(self, store):
        with store._connect() as conn:
            return conn.execute(
                """SELECT COUNT(*) FROM collection_membership m
                   WHERE NOT EXISTS (SELECT 1 FROM collections c
                                     WHERE c.collection_id = m.collection_id)"""
            ).fetchone()[0]

    def test_merging_a_list_away_leaves_no_orphan_rows(self, store):
        item = _stack(store)
        binder = _collection(store, "Trade binder")
        store.add_to_collection(item.item_id, binder)
        assert self._orphans(store) == 0

        store.delete_collection(binder)
        assert self._orphans(store) == 0, "the list is gone; its rows are not"

    def test_and_the_cards_themselves_survive_it(self, store):
        """The rule that outranks the rest: a filter cannot destroy what it
        filters."""
        item = _stack(store, quantity=3)
        binder = _collection(store, "Trade binder")
        store.add_to_collection(item.item_id, binder)

        store.delete_collection(binder)
        assert _owned(store) == 3

    def test_discarding_the_cards_too_also_clears_them(self, store):
        # The destructive branch is a different code path and forgot the same
        # thing independently.
        item = _stack(store, quantity=2)
        box = _collection(store, "Sold in a lot")
        store.add_to_collection(item.item_id, box)
        store.move_to_collection(item.item_id, box)  # and physically filed there

        store.delete_collection(box, discard_cards=True)
        assert self._orphans(store) == 0
        assert _owned(store) == 0, "this branch is meant to remove the cards"

    def test_a_reused_id_cannot_inherit_the_old_lists_cards(self, store):
        """The failure the cleanup exists to prevent, forced by hand."""
        item = _stack(store)
        old = _collection(store, "Old list")
        store.add_to_collection(item.item_id, old)
        store.delete_collection(old)

        with store._connect() as conn:
            conn.execute(
                """INSERT INTO collections
                       (collection_id, name, is_default,
                        created_at, updated_at)
                   VALUES (?, ?, 0, '2026-01-01', '2026-01-01')""", (old, "Brand new list"))
            conn.commit()

        lists = {c["collection_id"] for c in store.collections_for_item(item.item_id)}
        assert old not in lists, "a new list opened holding someone else's cards"
