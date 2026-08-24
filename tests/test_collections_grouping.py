"""Named collections: groupings over the master collection.

The model:

    master collection  = everything owned, always the sum of the parts
    collection         = a named subset of it
    deck               = a small sub-collection

The load-bearing rule is that a collection is a grouping ON TOP of ownership,
never instead of it. Moving a card between collections, or deleting a
collection, must not change what is owned — with exactly one exception, which
has to be asked for outright.
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


def _owned(store) -> int:
    """Total copies in the MASTER collection, whatever grouping they are in."""
    items, _total = store.list_items(limit=1000)
    return sum(i.quantity for i in items)


class TestDefaultCollection:
    def test_a_default_exists_from_the_start(self, store):
        collections = store.list_collections()
        assert len(collections) == 1
        assert collections[0]["is_default"] is True

    def test_cards_land_in_it_without_being_asked(self, store):
        store.add_copies("p1", "Sol Ring", quantity=2)
        assert store.list_collections()[0]["cards"] == 2

    def test_it_cannot_be_deleted(self, store):
        default_id = store.default_collection_id()
        result = store.delete_collection(default_id)
        assert result["deleted"] is False
        assert _owned(store) == 0


class TestGrouping:
    def test_the_same_card_can_sit_in_two_collections(self, store):
        binder = store.create_collection("Modern binder")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=2)
        store.add_copies("p1", "Sol Ring", quantity=3, collection_id=binder)
        # Two stacks, one master collection.
        assert _owned(store) == 5

    def test_master_totals_ignore_grouping(self, store):
        a = store.create_collection("A")["collection_id"]
        b = store.create_collection("B")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=1, collection_id=a)
        store.add_copies("p1", "Sol Ring", quantity=1, collection_id=b)
        assert _owned(store) == 2

    def test_collections_report_their_own_counts(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=4, collection_id=trade)
        store.add_copies("p2", "Arcane Signet", quantity=1)
        rows = {c["name"]: c for c in store.list_collections()}
        assert rows["Trade box"]["cards"] == 4
        assert rows["Main Collection"]["cards"] == 1

    def test_names_are_unique_case_insensitively(self, store):
        first = store.create_collection("Bulk")["collection_id"]
        again = store.create_collection("bulk")["collection_id"]
        # Returned rather than raising: the scanner creates these mid-run and
        # must not fail a scan over a name clash.
        assert first == again

    def test_renaming(self, store):
        cid = store.create_collection("Tarde box")["collection_id"]
        assert store.rename_collection(cid, "Trade box") is True
        names = [c["name"] for c in store.list_collections()]
        assert "Trade box" in names

    def test_renaming_onto_an_existing_name_is_refused(self, store):
        store.create_collection("Bulk")
        other = store.create_collection("Binder")["collection_id"]
        with pytest.raises(ValueError):
            store.rename_collection(other, "bulk")


class TestMoving:
    def test_moving_the_whole_stack(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        item = store.add_copies("p1", "Sol Ring", quantity=4)
        result = store.move_copies(item.item_id, trade)
        assert result["moved"] == 4
        assert _owned(store) == 4          # ownership untouched
        rows = {c["name"]: c["cards"] for c in store.list_collections()}
        assert rows["Trade box"] == 4 and rows["Main Collection"] == 0

    def test_moving_part_of_a_stack_splits_it(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        item = store.add_copies("p1", "Sol Ring", quantity=4)
        assert store.move_copies(item.item_id, trade, quantity=2)["moved"] == 2
        assert _owned(store) == 4
        rows = {c["name"]: c["cards"] for c in store.list_collections()}
        assert rows["Trade box"] == 2 and rows["Main Collection"] == 2

    def test_moving_onto_a_matching_stack_merges(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=1, collection_id=trade)
        item = store.add_copies("p1", "Sol Ring", quantity=2)
        store.move_copies(item.item_id, trade)
        rows = [c for c in store.list_collections() if c["name"] == "Trade box"]
        assert rows[0]["cards"] == 3
        assert _owned(store) == 3

    def test_moving_more_than_there_is_moves_what_there_is(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        item = store.add_copies("p1", "Sol Ring", quantity=2)
        assert store.move_copies(item.item_id, trade, quantity=99)["moved"] == 2
        assert _owned(store) == 2

    def test_moving_to_a_collection_that_does_not_exist(self, store):
        item = store.add_copies("p1", "Sol Ring", quantity=2)
        assert store.move_copies(item.item_id, 9999)["moved"] == 0
        assert _owned(store) == 2


class TestDeletingTheGrouping:
    """Deleting a grouping must never delete cardboard."""

    def test_cards_survive_and_move_to_the_default(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=3, collection_id=trade)
        result = store.delete_collection(trade)
        assert result["deleted"] is True
        assert _owned(store) == 3
        assert [c["name"] for c in store.list_collections()] == ["Main Collection"]

    def test_cards_can_be_sent_somewhere_specific(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        binder = store.create_collection("Binder")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=3, collection_id=trade)
        store.delete_collection(trade, move_to=binder)
        rows = {c["name"]: c["cards"] for c in store.list_collections()}
        assert rows["Binder"] == 3
        assert _owned(store) == 3

    def test_merging_sums_matching_stacks(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        binder = store.create_collection("Binder")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=2, collection_id=trade)
        store.add_copies("p1", "Sol Ring", quantity=1, collection_id=binder)
        store.delete_collection(trade, move_to=binder)
        rows = {c["name"]: c["cards"] for c in store.list_collections()}
        assert rows["Binder"] == 3
        assert _owned(store) == 3


class TestDeletingTheCards:
    """The other sense: the whole trade box was sold."""

    def test_cards_leave_the_master_collection(self, store):
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=3, collection_id=trade)
        store.add_copies("p2", "Arcane Signet", quantity=1)
        result = store.delete_collection(trade, discard_cards=True)
        assert result["deleted"] is True
        assert result["cards_removed"] == 3
        # The card that was NOT in that collection is untouched.
        assert _owned(store) == 1

    def test_it_is_never_the_default(self, store):
        """Destroying inventory must be asked for, never inferred."""
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=3, collection_id=trade)
        store.delete_collection(trade)
        assert _owned(store) == 3

    def test_the_removal_is_written_to_the_ledger(self, store):
        """Cost basis and P&L are built on the event log.

        A mass deletion that skipped it would leave both describing cards that
        no longer exist.
        """
        trade = store.create_collection("Trade box")["collection_id"]
        store.add_copies("p1", "Sol Ring", quantity=3, collection_id=trade)
        store.delete_collection(trade, discard_cards=True)
        events = store.recent_events(limit=10)
        removal = [e for e in events if e["reason"] == "collection-deleted"]
        assert removal and removal[0]["delta"] == -3

    def test_the_default_collection_empties_but_survives(self, store):
        """Cards need somewhere to land, so the default cannot be removed."""
        default_id = store.default_collection_id()
        store.add_copies("p1", "Sol Ring", quantity=2)
        result = store.delete_collection(default_id, discard_cards=True)
        assert result.get("emptied") is True
        assert _owned(store) == 0
        assert len(store.list_collections()) == 1


class TestMigration:
    def test_an_existing_collection_keeps_its_cards(self, tmp_path):
        """Upgrading a database from before collections existed.

        Everything already owned belongs to the default collection — that is
        what "it was all one collection before" means — and nothing may be
        lost on the way.
        """
        import sqlite3

        path = tmp_path / "collection.db"
        # Build a store, then strip the column back off to imitate the older
        # schema this has to upgrade from.
        store = CollectionStore(db_path=path)
        store.add_copies("p1", "Sol Ring", quantity=4)
        conn = sqlite3.connect(path)
        # Every index over the column has to go before SQLite will drop it.
        conn.execute("DROP INDEX IF EXISTS idx_ci_stack_v2")
        conn.execute("DROP INDEX IF EXISTS idx_ci_collection")
        conn.execute("ALTER TABLE collection_items DROP COLUMN collection_id")
        conn.execute("DROP TABLE collections")
        # The index the old schema had, which keyed on location alone.
        conn.execute("""CREATE UNIQUE INDEX idx_ci_stack ON collection_items(
            printing_id, finish, condition, language, location)""")
        conn.commit()
        conn.close()

        reopened = CollectionStore(db_path=path)
        assert _owned(reopened) == 4
        collections = reopened.list_collections()
        assert len(collections) == 1
        assert collections[0]["is_default"] is True
        assert collections[0]["cards"] == 4


class TestUpgradingARealDatabase:
    """The upgrade path, which fresh test databases never exercise.

    Both bugs here were invisible to the suite and appeared the moment the
    code met a database an earlier build had written. A test database is
    created by today's CREATE TABLE and therefore already has every column;
    a real one is not.
    """

    def _database_from_an_earlier_build(self, path):
        """A collections table with no `collection_uid`, as v1 wrote it."""
        import sqlite3

        store = CollectionStore(db_path=path)
        store.add_copies("p1", "Sol Ring", quantity=12)
        conn = sqlite3.connect(path)
        conn.execute("DROP INDEX IF EXISTS idx_collections_uid")
        conn.execute("ALTER TABLE collections DROP COLUMN collection_uid")
        conn.commit()
        conn.close()

    def test_it_opens_at_all(self, tmp_path):
        """The schema creates a unique index over a column the migration adds.

        Run in the wrong order, CREATE UNIQUE INDEX fails and the app cannot
        open the user's collection — total loss of access, not a subtle bug.
        """
        path = tmp_path / "collection.db"
        self._database_from_an_earlier_build(path)
        reopened = CollectionStore(db_path=path)
        assert _owned(reopened) == 12

    def test_the_default_collection_gets_the_shared_uid(self, tmp_path):
        """A random uid per device gives each its own "unfiled" pile.

        A removal made on the phone then lands in a collection the desktop
        does not have, so the card comes back on the next sync.
        """
        from densa_deck.collection.storage import DEFAULT_COLLECTION_UID

        path = tmp_path / "collection.db"
        self._database_from_an_earlier_build(path)
        reopened = CollectionStore(db_path=path)
        default = [c for c in reopened.list_collections() if c["is_default"]][0]
        assert default["collection_uid"] == DEFAULT_COLLECTION_UID

    def test_a_random_uid_written_by_a_later_build_is_corrected(self, tmp_path):
        """An intermediate build handed the default a random uid."""
        import sqlite3

        from densa_deck.collection.storage import DEFAULT_COLLECTION_UID

        path = tmp_path / "collection.db"
        store = CollectionStore(db_path=path)
        store.add_copies("p1", "Sol Ring", quantity=3)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE collections SET collection_uid = ? "
                     "WHERE is_default = 1", ("a-random-one",))
        conn.commit()
        conn.close()

        reopened = CollectionStore(db_path=path)
        default = [c for c in reopened.list_collections() if c["is_default"]][0]
        assert default["collection_uid"] == DEFAULT_COLLECTION_UID
        assert _owned(reopened) == 3

    def test_other_collections_keep_their_own_identity(self, tmp_path):
        from densa_deck.collection.storage import DEFAULT_COLLECTION_UID

        path = tmp_path / "collection.db"
        store = CollectionStore(db_path=path)
        store.create_collection("Trade box")
        reopened = CollectionStore(db_path=path)
        uids = {c["name"]: c["collection_uid"] for c in reopened.list_collections()}
        assert uids["Trade box"] != DEFAULT_COLLECTION_UID
        assert uids["Main Collection"] == DEFAULT_COLLECTION_UID
