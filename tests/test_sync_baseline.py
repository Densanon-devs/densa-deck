"""The first sync has to hand over the collection, not a replay of the log.

Found on a real installation: the desktop held ten stacks and the sync log
held five events. The other five predate logging, so a phone replaying from
zero could never learn about them — and no amount of re-syncing would ever
fix it, because those cards are simply not in the stream. From the phone the
symptom is "the desktop has more cards than I can see", with everything
apparently working.

A baseline is the one place an ABSOLUTE quantity is allowed. Everything else
in this system is a delta on purpose, so two devices editing offline both keep
their change; an absolute set cannot commute. It is safe here only because a
device taking a baseline has no state of its own to lose, and these tests are
mostly about holding that line.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi
from densa_deck.collection.storage import DEFAULT_COLLECTION_UID, CollectionStore
from densa_deck.sync.apply import SyncApplier
from densa_deck.sync.log import SyncEvent, SyncLog


@pytest.fixture
def api():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = AppApi(db_path=root / "cards.db", version_db_path=root / "v.db")
        a._collection_store = CollectionStore(db_path=root / "collection.db")
        yield a
        a.close()


def _data(reply):
    return reply.get("data", reply) if isinstance(reply, dict) else reply


def _pull(api, since=0, peer="phone-1"):
    return _data(api.sync_pull(since=since, peer=peer))


class TestCardsThatPredateTheLog:
    def test_they_are_still_handed_over(self, api):
        """The whole bug: written straight to the store, never logged."""
        store = api._get_collection_store()
        store.add_copies("p-old", "Ancient Tomb", quantity=4)

        events = _pull(api)["events"]

        sets = [e for e in events if e["kind"] == "stack-set"]
        assert len(sets) == 1
        assert sets[0]["payload"]["card_name"] == "Ancient Tomb"
        assert sets[0]["payload"]["quantity"] == 4

    def test_the_baseline_replaces_the_log_rather_than_preceding_it(self, api):
        """Sending both would double every card that does have events.

        Current quantities already include everything the log describes, so a
        baseline followed by a replay counts those cards twice.
        """
        store = api._get_collection_store()
        store.add_copies("p-old", "Ancient Tomb", quantity=4)
        api.scan_commit_raw("p-new", "Sol Ring", "nonfoil", "NM") if hasattr(
            api, "scan_commit_raw") else None
        api._log_stack_delta("p-new", "Sol Ring", 2)
        store.add_copies("p-new", "Sol Ring", quantity=2)

        pulled = _pull(api)
        assert pulled["baseline"] is True

        totals = {
            e["payload"]["card_name"]: e["payload"]["quantity"]
            for e in pulled["events"] if e["kind"] == "stack-set"
        }
        assert totals == {"Ancient Tomb": 4, "Sol Ring": 2}
        assert not any(e["kind"] == "stack-delta" for e in pulled["events"])

    def test_the_cursor_skips_past_everything_the_baseline_covered(self, api):
        """Otherwise the next pull replays events already accounted for."""
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=1)
        api._log_stack_delta("p-1", "Card One", 1)

        pulled = _pull(api)
        assert pulled["cursor"] == pulled["head"]
        assert pulled["more"] is False

        after = _pull(api, since=pulled["cursor"])
        assert after["events"] == []


class TestAskingTwice:
    def test_the_same_baseline_comes_back_identically(self, api):
        """A first sync is exactly when a connection gets interrupted.

        Random uids here would double the collection on any retry.
        """
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=3)

        first = _pull(api)["events"]
        second = _pull(api)["events"]
        assert [e["event_uid"] for e in first] == [e["event_uid"] for e in second]

    def test_applying_it_twice_does_not_double_anything(self, api):
        """The receiving end recognises the repeat and does nothing."""
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=3)
        events = [SyncEvent.from_dict(e) for e in _pull(api)["events"]]

        with tempfile.TemporaryDirectory() as tmp:
            far = CollectionStore(db_path=Path(tmp) / "phone.db")
            applier = SyncApplier(far, SyncLog(far.db_path, device="phone-1"))
            applier.apply_many(events)
            applier.apply_many(events)

            items, _ = far.list_items(limit=100)
            assert sum(i.quantity for i in items) == 3


class TestWhatArrivesOnTheOtherSide:
    def test_the_far_side_ends_up_agreeing_exactly(self, api):
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=3)
        store.add_copies("p-2", "Card Two", quantity=1, finish="foil")
        store.add_copies("p-3", "Card Three", quantity=7)

        events = [SyncEvent.from_dict(e) for e in _pull(api)["events"]]

        with tempfile.TemporaryDirectory() as tmp:
            far = CollectionStore(db_path=Path(tmp) / "phone.db")
            SyncApplier(far, SyncLog(far.db_path, device="phone-1")).apply_many(events)

            here, _ = store.list_items(limit=100)
            there, _ = far.list_items(limit=100)
            assert sorted((i.card_name, i.quantity, i.finish) for i in there) == \
                   sorted((i.card_name, i.quantity, i.finish) for i in here)

    def test_a_foil_and_a_nonfoil_stay_separate(self, api):
        """Merging them would misprice the collection silently."""
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=2, finish="nonfoil")
        store.add_copies("p-1", "Card One", quantity=1, finish="foil")

        sets = [e for e in _pull(api)["events"] if e["kind"] == "stack-set"]
        assert sorted(e["payload"]["finish"] for e in sets) == ["foil", "nonfoil"]

    def test_collections_arrive_before_the_cards_that_name_them(self, api):
        """A stack naming a collection that does not exist yet is filed as
        unsorted on the far side, which quietly loses the grouping."""
        store = api._get_collection_store()
        uid = store.create_collection("Trade binder")["collection_uid"]
        store.add_copies("p-1", "Card One", quantity=1,
                         collection_id=store.collection_by_uid(uid)["collection_id"])

        events = _pull(api)["events"]
        kinds = [e["kind"] for e in events]
        assert kinds.index("collection-upsert") < kinds.index("stack-set")

    def test_an_empty_stack_is_not_carried(self, api):
        """A stack at zero is not a card you own."""
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=2)
        store.add_copies("p-1", "Card One", quantity=-2)

        sets = [e for e in _pull(api)["events"] if e["kind"] == "stack-set"]
        assert sets == []

    def test_an_empty_collection_still_syncs_without_error(self, api):
        pulled = _pull(api)
        assert pulled["baseline"] is True
        assert all(e["kind"] == "collection-upsert" for e in pulled["events"])


class TestAbsoluteSetsStayOutOfNormalSync:
    def test_an_ordinary_pull_carries_deltas_only(self, api):
        """`stack-set` cannot commute. If it ever leaked into routine syncing,
        two devices editing the same stack offline would lose one of the
        edits — the exact failure the delta design exists to prevent."""
        store = api._get_collection_store()
        store.add_copies("p-1", "Card One", quantity=1)
        api._log_stack_delta("p-1", "Card One", 1)

        after = _pull(api, since=0)["cursor"]
        api._log_stack_delta("p-1", "Card One", 1)

        events = _pull(api, since=after)["events"]
        assert events, "a later change should still arrive"
        assert all(e["kind"] != "stack-set" for e in events)


class TestSettingAQuantity:
    def test_it_lands_on_the_number_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CollectionStore(db_path=Path(tmp) / "c.db")
            log = SyncLog(store.db_path, device="here")
            applier = SyncApplier(store, log)

            store.add_copies("p-1", "Card One", quantity=9)
            applier.apply(SyncEvent(
                event_uid="b-1", device="far", kind="stack-set",
                payload={"printing_id": "p-1", "card_name": "Card One",
                         "quantity": 2, "finish": "nonfoil", "condition": "NM",
                         "language": "en",
                         "collection_uid": DEFAULT_COLLECTION_UID}))

            assert store.stack_quantity("p-1") == 2

    def test_setting_to_zero_empties_the_stack(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CollectionStore(db_path=Path(tmp) / "c.db")
            applier = SyncApplier(store, SyncLog(store.db_path, device="here"))

            store.add_copies("p-1", "Card One", quantity=3)
            applier.apply(SyncEvent(
                event_uid="b-1", device="far", kind="stack-set",
                payload={"printing_id": "p-1", "card_name": "Card One",
                         "quantity": 0, "finish": "nonfoil", "condition": "NM",
                         "language": "en",
                         "collection_uid": DEFAULT_COLLECTION_UID}))

            assert store.stack_quantity("p-1") == 0
