"""One desktop app at a time.

Two copies share `cards.db`, `collection.db` and `versions.db`, and SQLite in
WAL mode lets both write — so the second window shows a collection the first
has already changed, and the two disagree about what you own with nothing on
screen to explain it. Closing a window and opening another is exactly how
someone gets there.

The hard part is not locking; it is a lock left behind by a process that was
killed. A stale lock and a live one look identical from the outside, and
refusing to start because of a crash three days ago is worse than not locking
at all — so every test here is really about telling those two apart.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from densa_deck.app.single_instance import (
    AlreadyRunning,
    InstanceLock,
    _pid_alive,
)


@pytest.fixture
def data_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestHoldingIt:
    def test_the_first_copy_gets_it(self, data_dir):
        lock = InstanceLock(data_dir)
        lock.acquire()
        assert lock.held
        assert (data_dir / "app.lock").exists()

    def test_the_second_copy_is_turned_away(self, data_dir):
        first = InstanceLock(data_dir)
        first.acquire()
        with pytest.raises(AlreadyRunning):
            InstanceLock(data_dir).acquire()

    def test_the_message_names_the_process_so_it_can_be_ended(self, data_dir):
        # "Already running" with no way to find the offender is a dead end
        # when the window has been closed and the process has not.
        InstanceLock(data_dir).acquire()
        with pytest.raises(AlreadyRunning) as caught:
            InstanceLock(data_dir).acquire()
        assert str(os.getpid()) in str(caught.value)
        assert caught.value.pid == os.getpid()

    def test_releasing_lets_the_next_one_in(self, data_dir):
        first = InstanceLock(data_dir)
        first.acquire()
        first.release()
        InstanceLock(data_dir).acquire()          # no raise

    def test_a_directory_that_does_not_exist_yet_is_made(self, data_dir):
        nested = data_dir / "not" / "there"
        InstanceLock(nested).acquire()
        assert (nested / "app.lock").exists()

    def test_it_works_as_a_context_manager(self, data_dir):
        with InstanceLock(data_dir):
            with pytest.raises(AlreadyRunning):
                InstanceLock(data_dir).acquire()
        InstanceLock(data_dir).acquire()          # released on the way out


class TestSurvivingACrash:
    """A lock file outliving the app that wrote it must not lock someone out
    of their own collection."""

    def test_a_lock_naming_a_dead_process_is_taken_over(self, data_dir):
        # PID 2^31-1 does not exist on any machine this will run on.
        (data_dir / "app.lock").write_text(json.dumps({"pid": 2147483647}),
                                           encoding="utf-8")
        InstanceLock(data_dir).acquire()          # no raise

    def test_an_unreadable_lock_is_not_evidence_of_a_running_app(self, data_dir):
        # A half-written file from a bad shutdown. Believing it would leave
        # someone unable to open the app with nothing to fix.
        (data_dir / "app.lock").write_text("{not json", encoding="utf-8")
        InstanceLock(data_dir).acquire()

    def test_an_empty_lock_file_is_taken_over(self, data_dir):
        (data_dir / "app.lock").write_text("", encoding="utf-8")
        InstanceLock(data_dir).acquire()

    def test_a_lock_with_no_pid_in_it_is_taken_over(self, data_dir):
        (data_dir / "app.lock").write_text(json.dumps({}), encoding="utf-8")
        InstanceLock(data_dir).acquire()

    def test_releasing_never_deletes_someone_else_s_lock(self, data_dir):
        """The dangerous inverse. A copy that failed to acquire must not be
        able to unlock the copy that is actually running."""
        holder = InstanceLock(data_dir)
        holder.acquire()

        loser = InstanceLock(data_dir)
        with pytest.raises(AlreadyRunning):
            loser.acquire()
        loser.release()                            # a no-op: it holds nothing

        assert (data_dir / "app.lock").exists()
        assert json.loads((data_dir / "app.lock").read_text())["pid"] == os.getpid()

    def test_releasing_twice_is_harmless(self, data_dir):
        lock = InstanceLock(data_dir)
        lock.acquire()
        lock.release()
        lock.release()


class TestKnowingWhoIsAlive:
    def test_this_process_is_alive(self):
        assert _pid_alive(os.getpid()) is True

    def test_a_process_that_cannot_exist_is_not(self):
        assert _pid_alive(2147483647) is False

    def test_nonsense_pids_are_not_alive(self):
        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False


class TestSeparateInstallsDoNotBlockEachOther:
    def test_two_data_directories_are_two_apps(self, data_dir):
        """A packaged build and a source run are genuinely separate installs
        pointed at separate databases. They share nothing, so one must not
        stop the other opening."""
        other = data_dir / "elsewhere"
        InstanceLock(data_dir).acquire()
        InstanceLock(other).acquire()             # no raise
