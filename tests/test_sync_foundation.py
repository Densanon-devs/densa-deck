"""Two-way sync: the properties that stop cards being lost.

This is an inventory of physical property, so the tests here are not about
features — they are about the failure the design exists to prevent. A card
that exists must not stop existing because two devices were edited apart.

The scenarios are the real ones: a phone edited at a shop while the desktop
was off, a sync that ran twice, a delete racing an add, a peer that replays
history it already sent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from densa_deck.collection.storage import CollectionStore
from densa_deck.sync.apply import SyncApplier, stack_delta_event
from densa_deck.sync.log import (
    KIND_COLLECTION_DELETE,
    KIND_STACK_DELTA,
    SyncEvent,
    SyncLog,
    device_id,
)


class Device:
    """One installation: its collection, its log, and its applier."""

    def __init__(self, root: Path, name: str):
        self.name = name
        self.store = CollectionStore(db_path=root / f"{name}.db")
        self.log = SyncLog(root / f"{name}.db", device=f"device-{name}")
        self.applier = SyncApplier(self.store, self.log)

    # A local edit: change the collection AND log it, the way the app does.
    def add(self, printing_id: str, name: str, quantity: int,
            collection_uid: str = "", **kwargs) -> None:
        collection_id = (self.applier._collection_for(collection_uid)
                         if collection_uid else None)
        self.store.add_copies(printing_id, name, quantity=quantity,
                              collection_id=collection_id, **kwargs)
        self.applier.record_stack_delta(
            printing_id=printing_id, card_name=name, delta=quantity,
            collection_uid=collection_uid or self.default_uid(), **kwargs)

    def default_uid(self) -> str:
        return self.store.collection_uid(self.store.default_collection_id())

    def owned(self) -> int:
        items, _ = self.store.list_items(limit=1000)
        return sum(i.quantity for i in items)

    def owned_of(self, printing_id: str) -> int:
        items, _ = self.store.list_items(limit=1000)
        return sum(i.quantity for i in items if i.printing_id == printing_id)


def sync_one_way(source: Device, target: Device) -> dict:
    """Everything source knows that target lacks."""
    cursor = target.log.peer_cursor(source.log.device)
    events, next_cursor = source.log.since(cursor,
                                           exclude_device=target.log.device)
    result = target.applier.apply_many(events)
    target.log.set_peer_cursor(source.log.device, next_cursor)
    return result


def sync_both(a: Device, b: Device) -> None:
    sync_one_way(a, b)
    sync_one_way(b, a)


@pytest.fixture
def devices(tmp_path, monkeypatch):
    monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "device.json"))
    # Separate databases that have never met, which is the situation sync
    # exists to survive.
    return Device(tmp_path, "pc"), Device(tmp_path, "phone")


class TestDeviceIdentity:
    def test_the_id_is_stable(self, tmp_path, monkeypatch):
        path = tmp_path / "device.json"
        monkeypatch.setenv("DENSA_DEVICE_FILE", str(path))
        assert device_id() == device_id()

    def test_two_installs_differ(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "a.json"))
        first = device_id()
        monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "b.json"))
        assert device_id() != first

    def test_an_unwritable_home_still_yields_an_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DENSA_DEVICE_FILE",
                           str(tmp_path / "no" / "such" / "dir" / "d.json"))
        monkeypatch.setattr(Path, "mkdir",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert len(device_id()) >= 8


class TestTheLog:
    def test_events_get_a_monotonic_sequence(self, tmp_path):
        log = SyncLog(tmp_path / "l.db", device="dev-a")
        first = log.record(KIND_STACK_DELTA, {"delta": 1})
        second = log.record(KIND_STACK_DELTA, {"delta": 1})
        assert second.seq == first.seq + 1

    def test_a_known_event_is_refused(self, tmp_path):
        """The idempotency gate every retry leans on."""
        log = SyncLog(tmp_path / "l.db", device="dev-a")
        event = SyncEvent(kind=KIND_STACK_DELTA, payload={"delta": 1},
                          device="dev-b", seq=1)
        assert log.accept(event) is True
        assert log.accept(event) is False

    def test_since_resumes_from_a_cursor(self, tmp_path):
        log = SyncLog(tmp_path / "l.db", device="dev-a")
        for _ in range(5):
            log.record(KIND_STACK_DELTA, {"delta": 1})
        first_batch, cursor = log.since(0, limit=2)
        assert len(first_batch) == 2
        rest, _ = log.since(cursor)
        assert len(rest) == 3

    def test_a_peers_own_events_are_not_echoed_back(self, tmp_path):
        log = SyncLog(tmp_path / "l.db", device="dev-a")
        log.record(KIND_STACK_DELTA, {"delta": 1})
        log.accept(SyncEvent(kind=KIND_STACK_DELTA, payload={"delta": 2},
                             device="dev-b", seq=1))
        events, _ = log.since(0, exclude_device="dev-b")
        assert all(e.device != "dev-b" for e in events)

    def test_a_watermark_never_rewinds(self, tmp_path):
        """A peer re-reading history must not trigger an endless resend."""
        log = SyncLog(tmp_path / "l.db", device="dev-a")
        log.set_peer_cursor("dev-b", 10)
        log.set_peer_cursor("dev-b", 4)
        assert log.peer_cursor("dev-b") == 10


class TestQuantitiesMerge:
    """The core claim: deltas commute, so nothing is lost."""

    def test_both_devices_added_while_apart(self, devices):
        pc, phone = devices
        pc.add("p1", "Sol Ring", 3)
        phone.add("p1", "Sol Ring", 2)
        sync_both(pc, phone)
        # Five cards exist. A snapshot sync would have kept three or two.
        assert pc.owned_of("p1") == 5
        assert phone.owned_of("p1") == 5

    def test_order_of_arrival_does_not_matter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DENSA_DEVICE_FILE", str(tmp_path / "d.json"))
        forward = Device(tmp_path, "fwd-a"), Device(tmp_path, "fwd-b")
        backward = Device(tmp_path, "bwd-a"), Device(tmp_path, "bwd-b")
        for a, b in (forward, backward):
            a.add("p1", "Sol Ring", 3)
            b.add("p1", "Sol Ring", 2)
        sync_one_way(forward[0], forward[1])
        sync_one_way(forward[1], forward[0])
        sync_one_way(backward[1], backward[0])
        sync_one_way(backward[0], backward[1])
        assert forward[0].owned_of("p1") == backward[0].owned_of("p1") == 5

    def test_removals_travel_too(self, devices):
        pc, phone = devices
        pc.add("p1", "Sol Ring", 4)
        sync_both(pc, phone)
        phone.add("p1", "Sol Ring", -1)          # sold one at the shop
        sync_both(pc, phone)
        assert pc.owned_of("p1") == 3
        assert phone.owned_of("p1") == 3

    def test_syncing_twice_changes_nothing(self, devices):
        """A retried or duplicated exchange must not double-count."""
        pc, phone = devices
        pc.add("p1", "Sol Ring", 3)
        sync_both(pc, phone)
        before = phone.owned_of("p1")
        sync_both(pc, phone)
        sync_both(pc, phone)
        assert phone.owned_of("p1") == before == 3

    def test_replayed_events_are_ignored(self, devices):
        """A peer that re-sends history it already sent."""
        pc, phone = devices
        pc.add("p1", "Sol Ring", 2)
        events, _ = pc.log.since(0)
        phone.applier.apply_many(events)
        again = phone.applier.apply_many(events)
        assert again["applied"] == 0
        assert again["duplicates"] == len(events)
        assert phone.owned_of("p1") == 2

    def test_finishes_stay_separate(self, devices):
        """Merging must respect the natural key, not just the card name."""
        pc, phone = devices
        pc.add("p1", "Sol Ring", 1, finish="foil")
        phone.add("p1", "Sol Ring", 1, finish="nonfoil")
        sync_both(pc, phone)
        items, _ = pc.store.list_items(limit=50)
        by_finish = {i.finish.value: i.quantity for i in items}
        assert by_finish == {"foil": 1, "nonfoil": 1}


class TestTheDefaultCollection:
    """"Unfiled" has to mean the same thing on both devices.

    A random uid per device gave each its own unfiled pile: a removal made on
    the phone landed in a collection the desktop did not have, so the card
    came back on the next sync, and the two piles showed up as "Main
    Collection" and "Main Collection (2)".
    """

    def test_both_devices_agree_on_it(self, devices):
        pc, phone = devices
        assert pc.default_uid() == phone.default_uid()

    def test_it_does_not_duplicate_across_a_sync(self, devices):
        pc, phone = devices
        pc.add("p1", "Sol Ring", 1)
        phone.add("p2", "Arcane Signet", 1)
        sync_both(pc, phone)
        names = [c["name"] for c in pc.store.list_collections()]
        assert names.count("Main Collection") == 1


class TestCollectionsAcrossDevices:
    def test_a_collection_made_on_the_phone_appears_on_the_pc(self, devices):
        pc, phone = devices
        made = phone.store.create_collection("Trade box")
        phone.applier.record_collection_upsert(made)
        phone.add("p1", "Sol Ring", 3, collection_uid=made["collection_uid"])
        sync_both(pc, phone)
        names = {c["name"]: c["cards"] for c in pc.store.list_collections()}
        assert names.get("Trade box") == 3

    def test_cards_land_in_the_right_collection(self, devices):
        pc, phone = devices
        binder = phone.store.create_collection("Binder")
        phone.applier.record_collection_upsert(binder)
        phone.add("p1", "Sol Ring", 2, collection_uid=binder["collection_uid"])
        phone.add("p2", "Arcane Signet", 1)          # default collection
        sync_both(pc, phone)
        rows = {c["name"]: c["cards"] for c in pc.store.list_collections()}
        assert rows["Binder"] == 2
        assert pc.owned() == 3

    def test_an_unknown_collection_is_created_not_dropped(self, devices):
        """Cards for a collection this device has never heard of."""
        pc, phone = devices
        event = SyncEvent(kind=KIND_STACK_DELTA, device="device-phone", seq=1,
                          payload=stack_delta_event(
                              printing_id="p1", card_name="Sol Ring", delta=2,
                              collection_uid="uid-never-seen"))
        pc.applier.apply(event)
        assert pc.owned_of("p1") == 2          # the cards survived

    def test_a_name_clash_keeps_both(self, devices):
        """Two devices both made a "Bulk", meaning different boxes.

        Merging would be irreversible; a suffix is not.
        """
        pc, phone = devices
        pc.store.create_collection("Bulk")
        theirs = phone.store.create_collection("Bulk")
        phone.applier.record_collection_upsert(theirs)
        phone.add("p1", "Sol Ring", 2, collection_uid=theirs["collection_uid"])
        sync_one_way(phone, pc)
        names = [c["name"] for c in pc.store.list_collections()]
        assert "Bulk" in names and "Bulk (2)" in names
        assert pc.owned_of("p1") == 2

    def test_renames_propagate(self, devices):
        pc, phone = devices
        made = phone.store.create_collection("Tarde box")
        phone.applier.record_collection_upsert(made)
        sync_both(pc, phone)
        phone.store.rename_collection(made["collection_id"], "Trade box")
        phone.applier.record_collection_upsert(
            {**made, "name": "Trade box"})
        sync_both(pc, phone)
        assert "Trade box" in [c["name"] for c in pc.store.list_collections()]


class TestDeleteRaces:
    def test_deleting_a_grouping_keeps_the_cards_everywhere(self, devices):
        pc, phone = devices
        made = phone.store.create_collection("Trade box")
        phone.applier.record_collection_upsert(made)
        phone.add("p1", "Sol Ring", 3, collection_uid=made["collection_uid"])
        sync_both(pc, phone)

        phone.store.delete_collection(made["collection_id"])
        phone.applier.record_collection_delete(made["collection_uid"])
        sync_both(pc, phone)

        assert pc.owned_of("p1") == 3          # grouping gone, cards kept
        assert phone.owned_of("p1") == 3

    def test_a_delete_without_intent_never_discards(self, devices):
        """An ambiguous delete is read the safe way."""
        pc, phone = devices
        made = pc.store.create_collection("Trade box")
        pc.applier.record_collection_upsert(made)
        pc.add("p1", "Sol Ring", 3, collection_uid=made["collection_uid"])
        sync_both(pc, phone)

        event = SyncEvent(kind=KIND_COLLECTION_DELETE, device="device-pc",
                          seq=99,
                          payload={"collection_uid": made["collection_uid"]})
        phone.applier.apply(event)
        assert phone.owned_of("p1") == 3

    def test_discarding_is_honoured_when_explicit(self, devices):
        pc, phone = devices
        made = pc.store.create_collection("Sold lot")
        pc.applier.record_collection_upsert(made)
        pc.add("p1", "Sol Ring", 3, collection_uid=made["collection_uid"])
        sync_both(pc, phone)

        pc.store.delete_collection(made["collection_id"], discard_cards=True)
        pc.applier.record_collection_delete(made["collection_uid"],
                                            discard_cards=True)
        sync_both(pc, phone)
        assert phone.owned_of("p1") == 0

    def test_cards_added_elsewhere_survive_a_delete(self, devices):
        """The race the "create beats delete" rule exists for.

        The desktop deletes a collection while the phone, offline, adds cards
        to it. The cards must not evaporate.
        """
        pc, phone = devices
        made = pc.store.create_collection("Trade box")
        pc.applier.record_collection_upsert(made)
        sync_both(pc, phone)

        phone.add("p1", "Sol Ring", 4, collection_uid=made["collection_uid"])
        pc.store.delete_collection(made["collection_id"])
        pc.applier.record_collection_delete(made["collection_uid"])

        sync_both(pc, phone)
        assert pc.owned_of("p1") == 4
        assert phone.owned_of("p1") == 4


class TestRobustness:
    def test_an_unknown_kind_is_kept_and_not_acted_on(self, devices):
        """A newer peer must not break an older one, or lose its events."""
        pc, _phone = devices
        event = SyncEvent(kind="something-from-the-future",
                          payload={"whatever": 1}, device="device-x", seq=1)
        result = pc.applier.apply(event)
        assert result["applied"] is False
        assert pc.log.has(event.event_uid)      # stored, so it can be forwarded

    def test_a_malformed_event_does_not_stall_the_batch(self, devices):
        pc, _phone = devices
        good = SyncEvent(kind=KIND_STACK_DELTA, device="device-x", seq=1,
                         payload=stack_delta_event(
                             printing_id="p1", card_name="Sol Ring", delta=2,
                             collection_uid=""))
        bad = SyncEvent(kind=KIND_STACK_DELTA, device="device-x", seq=2,
                        payload={})            # no printing, no delta
        result = pc.applier.apply_many([bad, good])
        assert result["applied"] == 1
        assert pc.owned_of("p1") == 2

    def test_a_zero_delta_is_not_an_edit(self, devices):
        pc, _phone = devices
        event = SyncEvent(kind=KIND_STACK_DELTA, device="device-x", seq=1,
                          payload=stack_delta_event(
                              printing_id="p1", card_name="Sol Ring", delta=0,
                              collection_uid=""))
        assert pc.applier.apply(event)["applied"] is False

    def test_clock_skew_does_not_reorder_quantities(self, devices):
        """Deltas must not depend on timestamps at all.

        A phone with a wrong clock is common. Quantities commute, so the only
        thing skew can affect is a rename — never a card count.
        """
        pc, phone = devices
        pc.add("p1", "Sol Ring", 3)
        phone.add("p1", "Sol Ring", 2)
        for event_log in (pc.log, phone.log):
            with event_log._connect() as conn:
                conn.execute("UPDATE sync_events SET created_at = ?",
                             ("1999-01-01T00:00:00.000+00:00",))
                conn.commit()
        sync_both(pc, phone)
        assert pc.owned_of("p1") == 5


class TestTheKindRegistryMatchesWhatCanActuallyBeApplied:
    """A list of event kinds that disagrees with the applier is a trap.

    `KNOWN_KINDS` listed five of the seven, missing `stack-set` and
    `membership` — the two the FIRST sync is made of. Nothing read it, so
    nothing was broken; the next thing to read it as a filter would have
    dropped the baseline on the floor and shipped a phone that syncs an empty
    collection, with the registry looking authoritative the whole time.

    So it is checked against the code that does the work, rather than trusted.
    """

    def _handled_kinds(self):
        import re
        from pathlib import Path

        import densa_deck.sync.apply as apply_mod

        source = Path(apply_mod.__file__).read_text(encoding="utf-8")
        names = set(re.findall(r'KIND_(\w+)\s*:', source))
        names |= set(re.findall(r'kind == KIND_(\w+)', source))
        return {getattr(apply_mod, f"KIND_{n}", None) or
                getattr(__import__("densa_deck.sync.log", fromlist=["x"]),
                        f"KIND_{n}")
                for n in names}

    def test_every_applicable_kind_is_in_the_registry(self):
        from densa_deck.sync.log import KNOWN_KINDS

        missing = self._handled_kinds() - set(KNOWN_KINDS)
        assert not missing, f"the applier handles {missing}, the registry omits them"

    def test_the_registry_claims_nothing_the_applier_cannot_do(self):
        from densa_deck.sync.log import KNOWN_KINDS

        extra = set(KNOWN_KINDS) - self._handled_kinds()
        assert not extra, f"the registry lists {extra}, which nothing applies"

    def test_the_two_that_were_missing_are_the_baseline(self):
        """Named outright, because these are the ones whose absence would
        have been invisible until a first sync came back empty."""
        from densa_deck.sync.log import (
            KIND_MEMBERSHIP,
            KIND_STACK_SET,
            KNOWN_KINDS,
        )

        assert KIND_STACK_SET in KNOWN_KINDS
        assert KIND_MEMBERSHIP in KNOWN_KINDS
