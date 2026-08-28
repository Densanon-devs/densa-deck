"""The pull cursor, and why "swipe to sync" hung forever.

`since()` returns events after a cursor, minus the caller's own — harmless to
send them back, but it halves the traffic. The cursor it returns has to be
HOW FAR THE SCAN REACHED, not the last row it handed over.

It was the last row handed over. So a window containing nothing but the
peer's own events returned no rows and left the cursor exactly where it was,
while the caller was told `more` because the head was still beyond it. The
phone asked again, got the same nothing, and looped until someone force-quit
the app.

And that is not an exotic shape: the tail of the log is entirely the peer's
own events immediately after the peer pushes — which is what happens every
single time someone edits on the phone and then syncs.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from densa_deck.sync.log import SyncEvent, SyncLog


@pytest.fixture
def log():
    with tempfile.TemporaryDirectory() as tmp:
        yield SyncLog(db_path=Path(tmp) / "sync.db", device="pc")


def _event(i: int, device: str) -> SyncEvent:
    return SyncEvent(event_uid=f"e{i}", device=device, seq=i,
                     kind="stack-delta", payload={"n": i}, created_at="now")


def _drain(log, peer: str, *, cap: int = 50) -> tuple[int, int]:
    """Pull the way a phone does. Returns (rounds, events seen).

    Bounded so a regression fails the test instead of hanging the suite —
    which is precisely what it did to the app.
    """
    cursor, seen, rounds = 0, 0, 0
    while rounds < cap:
        events, cursor = log.since(cursor, limit=500, exclude_device=peer)
        seen += len(events)
        rounds += 1
        if cursor >= log.head():
            break
    return rounds, seen


class TestTheCursorAlwaysMovesForward:
    def test_a_page_of_only_the_peers_events_still_advances(self, log):
        """The hang, in one assertion. Every event belongs to the caller, so
        none come back — and the cursor has to move anyway."""
        for i in range(5):
            log.accept(_event(i, "phone"))
        events, cursor = log.since(0, exclude_device="phone")
        assert events == []
        assert cursor == log.head(), "scanning past your own events is progress"

    def test_syncing_terminates_when_the_tail_is_the_peers_own(self, log):
        # The exact shape after a phone pushes: a few of ours, then a run of
        # theirs at the end of the log.
        for i in range(3):
            log.accept(_event(i, "pc"))
        for i in range(3, 10):
            log.accept(_event(i, "phone"))

        rounds, seen = _drain(log, "phone")
        assert rounds == 1, "one round, not fifty"
        assert seen == 3, "and it still delivered the desktop's own events"

    def test_a_log_that_is_entirely_the_peers_terminates(self, log):
        for i in range(20):
            log.accept(_event(i, "phone"))
        rounds, seen = _drain(log, "phone")
        assert rounds == 1
        assert seen == 0

    def test_nothing_is_skipped_by_advancing_past_the_filtered(self, log):
        # Advancing to the end of the window must not step over a real event
        # that was interleaved with the peer's.
        for i in range(10):
            log.accept(_event(i, "pc" if i % 2 == 0 else "phone"))
        rounds, seen = _drain(log, "phone")
        assert seen == 5, "every desktop event arrived"

    def test_an_empty_log_does_not_move_the_cursor(self, log):
        events, cursor = log.since(0)
        assert events == []
        assert cursor == 0

    def test_a_cursor_past_the_end_stays_put(self, log):
        log.accept(_event(1, "pc"))
        events, cursor = log.since(999)
        assert events == []
        assert cursor == 999

    def test_paging_walks_the_whole_log_without_repeating(self, log):
        for i in range(12):
            log.accept(_event(i, "pc"))
        cursor, seen, rounds = 0, [], 0
        while rounds < 20:
            events, cursor = log.since(cursor, limit=5)
            seen.extend(e.event_uid for e in events)
            rounds += 1
            if cursor >= log.head():
                break
        assert len(seen) == len(set(seen)) == 12
        assert rounds == 3, "12 events at 5 a page"

    def test_no_filter_still_reports_the_last_row_it_returned(self, log):
        for i in range(3):
            log.accept(_event(i, "pc"))
        events, cursor = log.since(0, limit=2)
        assert len(events) == 2
        assert cursor == 2, "the window end and the last row agree here"


class TestTheBaselineDeliversEveryStack:
    """The first sync, which is what a phone falls back on when it has
    nothing — and therefore the worst possible place for a card to go missing.

    Every baseline event carries an id derived from the stack itself, so a
    phone that asks twice recognises the second set as duplicates rather than
    doubling the collection. That only works if the id is as specific as the
    STACK KEY the far side uses. It was not: `location` was missing, so the
    same printing in two boxes produced two events with one id, and the phone
    skipped the second as already known. The cards in it never arrived, and
    nothing anywhere said so.
    """

    @pytest.fixture
    def api(self):
        import tempfile
        from pathlib import Path

        from densa_deck.app.api import AppApi
        from densa_deck.collection.storage import CollectionStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = AppApi(db_path=root / "cards.db",
                       version_db_path=root / "versions.db")
            a._collection_store = CollectionStore(db_path=root / "collection.db")
            yield a
            a.close()

    def _baseline(self, api):
        reply = api.sync_pull(since=0, limit=1000, peer="probe")
        return reply.get("data", reply)["events"]

    def _stack_sets(self, api):
        return [e for e in self._baseline(api) if e["kind"] == "stack-set"]

    def test_two_boxes_of_one_printing_are_two_events(self, api):
        """The bug, exactly. Same card, same finish, same condition, two
        locations — and the phone received one of them."""
        store = api._get_collection_store()
        store.add_copies("p1", "Death Wind", quantity=1, location="binder")
        store.add_copies("p1", "Death Wind", quantity=2, location="deck box")

        events = self._stack_sets(api)
        assert len(events) == 2
        uids = [e["event_uid"] for e in events]
        assert len(set(uids)) == 2, "one id for two stacks loses one of them"

    def test_the_whole_collection_arrives(self, api):
        store = api._get_collection_store()
        store.add_copies("p1", "Death Wind", quantity=1, location="binder")
        store.add_copies("p1", "Death Wind", quantity=2, location="deck box")
        store.add_copies("p2", "Radha", quantity=2)

        events = self._stack_sets(api)
        # Deduplicated by uid the way the receiving side does it.
        by_uid = {e["event_uid"]: e for e in events}
        delivered = sum(e["payload"]["quantity"] for e in by_uid.values())
        assert delivered == store.summary().total_cards == 5

    def test_finish_condition_and_language_still_separate_stacks(self, api):
        # The parts that were already there have to stay there.
        store = api._get_collection_store()
        store.add_copies("p1", "Death Wind", quantity=1, finish="foil")
        store.add_copies("p1", "Death Wind", quantity=1, finish="nonfoil")
        store.add_copies("p1", "Death Wind", quantity=1, condition="LP")
        uids = {e["event_uid"] for e in self._stack_sets(api)}
        assert len(uids) == 3

    def test_asking_twice_gives_identical_ids(self, api):
        """A retried first sync must be recognised as duplicates, not applied
        again — a first sync is exactly when a connection drops."""
        store = api._get_collection_store()
        store.add_copies("p1", "Death Wind", quantity=1, location="binder")
        store.add_copies("p1", "Death Wind", quantity=2, location="deck box")
        first = [e["event_uid"] for e in self._stack_sets(api)]
        second = [e["event_uid"] for e in self._stack_sets(api)]
        assert first == second

    def test_a_stack_with_no_location_still_gets_an_id(self, api):
        # Empty is the normal case; it must not collapse the id or collide
        # with a stack whose location happens to be something else.
        store = api._get_collection_store()
        store.add_copies("p1", "Death Wind", quantity=1)
        store.add_copies("p1", "Death Wind", quantity=1, location="binder")
        uids = {e["event_uid"] for e in self._stack_sets(api)}
        assert len(uids) == 2

    def test_memberships_are_one_per_stack_and_list(self, api):
        # The other half of the baseline, keyed by item_id, which is unique
        # per stack — so it does not share this failure.
        store = api._get_collection_store()
        item = store.add_copies("p1", "Death Wind", quantity=1, location="a")
        other = store.add_copies("p1", "Death Wind", quantity=1, location="b")
        members = [e for e in self._baseline(api) if e["kind"] == "membership"]
        assert len({e["event_uid"] for e in members}) == len(members)


class TestThePageLimitCountsTwoDifferentThings:
    """The other half of the cursor, and the bug the first fix introduced.

    Two queries run per page and their LIMITs do not mean the same thing: the
    one that returns rows limits MATCHES, the one that moves the cursor limits
    rows SCANNED. So when the peer's own events crowd the front of a window,
    the matches come from beyond it and the cursor is left behind rows that
    were already handed over. Ask again and you are given them again.

    Nothing corrupts — the receiver knows those uids and skips them — but the
    paging burns rounds re-sending what it just sent, in exactly the case
    paging exists to handle. The cursor has to be the FURTHER of the two.
    """

    def test_matches_beyond_the_window_are_not_sent_twice(self, log):
        # Five of the phone's, then three of ours: at limit=3 the first
        # window is all phone, and the matches all lie past its end.
        for i in range(5):
            log.accept(_event(i, "phone"))
        for i in range(5, 8):
            log.accept(_event(i, "pc"))

        cursor, seen, rounds = 0, [], 0
        while rounds < 30:
            events, cursor = log.since(cursor, limit=3, exclude_device="phone")
            seen.extend(e.event_uid for e in events)
            rounds += 1
            if cursor >= log.head():
                break

        assert seen == ["e5", "e6", "e7"], "delivered once, in order"
        assert len(seen) == len(set(seen)), "and never delivered twice"

    def test_a_long_tail_of_the_peers_own_after_the_matches(self, log):
        """The real shape from the desktop's log: three of ours buried in a
        long run of the phone's, with more of theirs after."""
        for i in range(5):
            log.accept(_event(i, "phone"))
        for i in range(5, 8):
            log.accept(_event(i, "pc"))
        for i in range(8, 20):
            log.accept(_event(i, "phone"))

        cursor, seen, rounds = 0, [], 0
        while rounds < 40:
            events, cursor = log.since(cursor, limit=3, exclude_device="phone")
            seen.extend(e.event_uid for e in events)
            rounds += 1
            if cursor >= log.head():
                break

        assert sorted(seen) == ["e5", "e6", "e7"]
        assert rounds < 40, "and it terminated rather than being cut off"

    def test_the_cursor_never_goes_backwards(self, log):
        """Whatever the mix, each page reports a cursor at least as far as the
        one before — the property everything else here depends on."""
        for i in range(30):
            log.accept(_event(i, "pc" if i % 7 == 0 else "phone"))
        cursor, rounds = 0, 0
        while rounds < 60:
            _events, nxt = log.since(cursor, limit=4, exclude_device="phone")
            assert nxt >= cursor, f"cursor went {cursor} -> {nxt}"
            if nxt == cursor and cursor < log.head():
                raise AssertionError("stalled below the head")
            cursor = nxt
            rounds += 1
            if cursor >= log.head():
                break
        assert cursor == log.head()
