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
