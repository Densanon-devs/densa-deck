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


class TestAPidThatBelongsToSomethingElseNow:
    """Process ids get reused, and a crashed app's number can end up on a
    browser tab or Explorer.

    Believing that is the worst outcome the lock has: the app refuses to
    open, refuses again tomorrow, and the message tells the user to go and
    end a process that has nothing to do with Densa Deck. So the lock records
    what program it belonged to, and a live pid running something else counts
    as gone.
    """

    def _write(self, data_dir, **fields):
        (data_dir / "app.lock").write_text(json.dumps(fields), encoding="utf-8")

    def test_a_live_pid_running_a_different_program_is_taken_over(self, data_dir):
        # This process is certainly alive, and is certainly not that.
        self._write(data_dir, pid=os.getpid(), program="not-densa-deck.exe")
        lock = InstanceLock(data_dir)
        lock.acquire()          # must not raise
        assert lock.held

    def test_a_live_pid_running_the_same_program_still_blocks(self, data_dir):
        from densa_deck.app.single_instance import _program_name

        self._write(data_dir, pid=os.getpid(), program=_program_name())
        with pytest.raises(AlreadyRunning):
            InstanceLock(data_dir).acquire()

    def test_an_old_lock_with_no_program_recorded_is_still_believed(self, data_dir):
        """Locks written before this check exist on disk right now. Without a
        program to compare, the pid alone decides — the previous behaviour,
        rather than a takeover of a lock that is probably genuine."""
        self._write(data_dir, pid=os.getpid())
        with pytest.raises(AlreadyRunning):
            InstanceLock(data_dir).acquire()

    def test_what_it_writes_can_be_read_back(self, data_dir):
        lock = InstanceLock(data_dir)
        lock.acquire()
        stored = json.loads((data_dir / "app.lock").read_text(encoding="utf-8"))
        assert stored["pid"] == os.getpid()
        assert stored["program"], "a lock with no program is the old shape"


class TestTwoCopiesStartingAtTheSameMoment:
    """Look-then-write cannot decide a tie: both copies look, both see an
    empty directory, both write, and both believe they hold it — the exact
    situation the lock exists to prevent, reached through the lock. The file
    therefore has to be created exclusively, so the operating system picks a
    winner.
    """

    def test_creating_it_twice_does_not_succeed_twice(self, data_dir):
        first, second = InstanceLock(data_dir), InstanceLock(data_dir)
        assert first._create_exclusive() is True
        assert second._create_exclusive() is False, "both copies took the lock"

    def test_the_loser_does_not_overwrite_the_winners_stamp(self, data_dir):
        first = InstanceLock(data_dir)
        first.acquire()
        with pytest.raises(AlreadyRunning):
            InstanceLock(data_dir).acquire()
        stored = json.loads((data_dir / "app.lock").read_text(encoding="utf-8"))
        assert stored["pid"] == os.getpid()

    def test_a_lock_caught_mid_write_is_not_mistaken_for_rubbish(self, data_dir):
        """The other sliver: the file is created and the pid written a moment
        later. A reader that lands in between sees an empty file, and must not
        conclude the holder is dead and delete it."""
        (data_dir / "app.lock").write_bytes(b"")      # created, not yet written

        lock = InstanceLock(data_dir)
        settled = lock._read_settled()
        # It stays unreadable for the whole wait here, so the answer is None —
        # but the point is that it WAITED rather than answering instantly.
        assert settled is None
        assert (data_dir / "app.lock").exists(), "it deleted a lock it never read"

    def test_a_lock_that_becomes_readable_during_the_wait_is_believed(self, data_dir):
        """Same sliver, with the writer finishing in time — which is what
        actually happens, since the two steps are microseconds apart."""
        path = data_dir / "app.lock"
        path.write_bytes(b"")
        lock = InstanceLock(data_dir)

        real_read, calls = lock._read, {"n": 0}

        def finishing_write():
            calls["n"] += 1
            if calls["n"] == 2:        # the writer lands between attempts
                path.write_text(json.dumps({"pid": os.getpid(),
                                            "program": "densa-deck.exe"}),
                                encoding="utf-8")
            return real_read()

        lock._read = finishing_write
        found = lock._read_settled()
        assert found is not None and found["pid"] == os.getpid()

    def test_a_stale_lock_is_still_cleared_under_the_new_scheme(self, data_dir):
        # The exclusive create must not break takeover of a dead lock, which
        # is the common case and the one people actually hit.
        (data_dir / "app.lock").write_text(
            json.dumps({"pid": 999_999_998, "program": "densa-deck.exe"}),
            encoding="utf-8")
        lock = InstanceLock(data_dir)
        lock.acquire()
        assert lock.held

    def test_a_permanently_corrupt_lock_is_still_taken_over(self, data_dir):
        """Waiting must not turn into refusing. A file that never becomes
        readable is rubbish, and rubbish must not lock anyone out."""
        (data_dir / "app.lock").write_text("{not json", encoding="utf-8")
        lock = InstanceLock(data_dir)
        lock.acquire()
        assert lock.held

    def test_acquiring_waits_for_a_lock_that_is_mid_write(self, data_dir):
        """Through `acquire`, not around it — the waiting is only worth
        anything if the path that decides to delete a lock is the one doing
        it. A live holder caught between creating its lock and writing its
        pid must not have that lock taken out from under it."""
        path = data_dir / "app.lock"
        path.write_bytes(b"")                      # created, pid not yet in it

        from densa_deck.app.single_instance import _program_name

        lock = InstanceLock(data_dir)
        real_read, calls = lock._read, {"n": 0}

        def finishing_write():
            calls["n"] += 1
            if calls["n"] == 2:                    # the holder finishes here
                path.write_text(
                    json.dumps({"pid": os.getpid(), "program": _program_name()}),
                    encoding="utf-8")
            return real_read()

        lock._read = finishing_write
        with pytest.raises(AlreadyRunning):
            lock.acquire()
        assert not lock.held, "it started beside a live copy"


class TestTellingTheSecondCopyWhyItWillNotOpen:
    """The refusal has to be visible, and it must not hang.

    A double-clicked shortcut has no console, so a printed line is a silent
    failure — which is what "it just does not open" looks like from outside.
    A modal fixes that and creates the opposite problem: a scripted launch
    blocks forever on a dialog nobody is there to dismiss.

    `isatty()` cannot separate them. It is False for the shortcut AND for
    `densa-deck app > log.txt`, which has a console, is a script, and is the
    one that hangs. Asking the PROCESS whether a console is attached does
    separate them.
    """

    def test_a_redirected_launch_is_treated_as_having_a_console(self, monkeypatch):
        """Output redirected to a file: isatty is False, but there is a
        console and a person watching a terminal, so no modal."""
        import sys

        from densa_deck.app import main as main_mod

        class NotATty:
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stderr", NotATty())
        shown = []
        monkeypatch.setattr(main_mod.sys, "platform", "win32")

        import ctypes
        if not hasattr(ctypes, "windll"):
            pytest.skip("Windows-only path")

        class FakeKernel:
            @staticmethod
            def GetConsoleWindow():
                return 12345            # a console exists

        class FakeUser:
            @staticmethod
            def MessageBoxW(*args):
                shown.append(args)
                return 1

        class FakeWinDLL:
            kernel32 = FakeKernel
            user32 = FakeUser

        monkeypatch.setattr(ctypes, "windll", FakeWinDLL)
        main_mod._report_already_running(
            AlreadyRunning(4321, Path("app.lock")))
        assert not shown, "it raised a modal that nothing would ever dismiss"

    def test_a_shortcut_with_no_console_still_gets_told(self, monkeypatch):
        """The case the dialog exists for: nothing printed anywhere, so the
        window is the only way the person finds out."""
        import ctypes
        import sys

        from densa_deck.app import main as main_mod

        if not hasattr(ctypes, "windll"):
            pytest.skip("Windows-only path")

        class NotATty:
            def isatty(self):
                return False

        monkeypatch.setattr(sys, "stderr", NotATty())
        monkeypatch.setattr(main_mod.sys, "platform", "win32")
        shown = []

        class FakeKernel:
            @staticmethod
            def GetConsoleWindow():
                return 0                # no console at all

        class FakeUser:
            @staticmethod
            def MessageBoxW(*args):
                shown.append(args)
                return 1

        class FakeWinDLL:
            kernel32 = FakeKernel
            user32 = FakeUser

        monkeypatch.setattr(ctypes, "windll", FakeWinDLL)
        main_mod._report_already_running(
            AlreadyRunning(4321, Path("app.lock")))
        assert shown, "the only channel there was, and it said nothing"
        assert "4321" in shown[0][1], "the message must name the live process"

    def test_a_real_terminal_never_gets_a_dialog(self, monkeypatch):
        import sys

        from densa_deck.app import main as main_mod

        class IsATty:
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stderr", IsATty())
        shown = []
        monkeypatch.setattr(main_mod, "webview", None, raising=False)
        # Nothing to stub: returning early is the whole behaviour.
        main_mod._report_already_running(AlreadyRunning(1, Path("app.lock")))
        assert not shown
