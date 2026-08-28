"""One desktop app at a time.

Nothing stopped a second copy opening. Two of them share `cards.db`,
`collection.db` and `versions.db`, and SQLite in WAL mode will happily let
both write — so the second window shows a collection the first one has
already changed, edits land in whichever process the click reached, and the
two disagree about what you own with nothing on screen to say why. Closing a
window and opening another is exactly how someone gets into that state.

A lock FILE rather than a socket, because the app already binds a port
conditionally (phone sharing is opt-in) and a lock that only exists when a
feature is enabled is not a lock. It lives beside the databases it protects,
so a second copy pointed at a different data directory — a packaged build and
a source run, say — correctly does NOT block, since those two genuinely are
separate installs and share nothing.

The hard part is not locking. It is a lock left behind by a process that was
killed, because a stale lock file is indistinguishable from a live one by
looking at it. So the pid is written inside and checked: a lock naming a
process that is gone is cleared rather than believed. Refusing to start
because of a crash three days ago would be worse than not locking at all.

Which leaves the two ways "is that pid alive?" can still be the wrong
question:

  * The id was REUSED. The app crashed, the operating system handed its
    number to something else, and that something else is very much alive.
    Believing it locks someone out of their own app permanently — and the
    message tells them to go and end a process that has nothing to do with
    us. So the lock also records WHAT was running, and a pid whose program
    is no longer Densa Deck is treated as gone.

  * Two copies start AT ONCE, both look, both see nothing, and both write.
    Reading and then writing cannot decide that; the file has to be created
    exclusively, so that exactly one of them can win and the loser is told
    who beat it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class AlreadyRunning(Exception):
    """Another copy holds the lock, and it is genuinely alive."""

    def __init__(self, pid: int, path: Path):
        self.pid = pid
        self.path = path
        super().__init__(
            f"Densa Deck is already running (process {pid}). "
            f"Close that window first, or end the process if it has no window."
        )


def _pid_alive(pid: int) -> bool:
    """Is this process id still running?

    Errs towards ALIVE only when it genuinely cannot tell. Being wrong in the
    other direction — deciding a running app is dead — hands its databases to
    a second writer, which is the thing being prevented.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            # PROCESS_QUERY_LIMITED_INFORMATION: enough to ask whether it
            # exists, and permitted across sessions unlike full query rights.
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            # 259 is STILL_ACTIVE. A handle that opens for an exited process
            # is normal on Windows while something still holds a reference.
            return bool(ok) and exit_code.value == 259
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    except Exception:
        return True


def _program_name(pid: int | None = None) -> str:
    """The executable behind a pid, reduced to its file name.

    The file name rather than the full path, because a packaged build and a
    source run are the same application under two paths, and the point of the
    comparison is only to notice that the pid now belongs to something else
    entirely — Explorer, a browser, whatever inherited the number.

    Returns "" when it cannot be determined, and every caller reads that as
    "do not use this signal" rather than as a mismatch. Guessing wrong here
    would take over a live app's lock, which is worse than not checking.
    """
    if pid is None or pid == os.getpid():
        return Path(sys.executable).name if sys.executable else ""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return ""
            try:
                size = wintypes.DWORD(1024)
                buf = ctypes.create_unicode_buffer(size.value)
                ok = kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size))
                return Path(buf.value).name if ok else ""
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""
    try:
        return Path(os.readlink(f"/proc/{pid}/exe")).name
    except OSError:
        return ""


class InstanceLock:
    """Held for the life of the app. Released on close, and on a crash."""

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / "app.lock"
        self.held = False

    def acquire(self) -> None:
        """Take the lock, or raise `AlreadyRunning`."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Created exclusively, so a simultaneous launch cannot have both
        # copies decide the coast is clear. Whoever loses the create goes on
        # to inspect what is already there.
        if self._create_exclusive():
            self.held = True
            return

        existing = self._read_settled()
        if existing is not None and self._is_us(existing):
            raise AlreadyRunning(existing["pid"], self.path)

        # Either it will not parse or it names a process that has gone (or
        # been replaced by something that is not this app). Clear it and take
        # the lock the same exclusive way — and if that fails, another copy
        # got there in the gap, which is a real answer rather than a reason
        # to overwrite them.
        try:
            self.path.unlink()
        except OSError:
            pass
        if self._create_exclusive():
            self.held = True
            return

        winner = self._read()
        raise AlreadyRunning((winner or {}).get("pid", 0), self.path)

    def _create_exclusive(self) -> bool:
        """Create the lock file only if nobody else has. True if we made it."""
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        except OSError:
            # A directory that cannot be written to is not the same as being
            # locked out by another copy, and must not stop the app opening.
            return True
        try:
            os.write(fd, json.dumps(self._stamp()).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _stamp(self) -> dict:
        """What is written inside: who we are, and what program we are."""
        return {"pid": os.getpid(), "program": _program_name()}

    def _is_us(self, existing: dict) -> bool:
        """Is the recorded process still alive AND still this application?

        A lock with no program recorded predates this check, so the pid alone
        decides — the same answer as before, rather than a spurious takeover
        of a lock that is very likely genuine.
        """
        pid = int(existing.get("pid") or 0)
        if not _pid_alive(pid):
            return False
        recorded = (existing.get("program") or "").strip().lower()
        if not recorded:
            return True
        running = (_program_name(pid) or "").strip().lower()
        if not running:
            return True          # could not look; assume the lock is real
        return running == recorded

    def _read_settled(self) -> dict | None:
        """`_read`, but tolerant of a lock that is being written right now.

        Creating the file and writing the pid into it are two steps, so there
        is a sliver between them where the winner's lock exists and is empty.
        A reader that concluded "unparseable, therefore stale" in that sliver
        would delete a live app's lock and start beside it — the exact
        outcome all of this prevents, reached through a gap of microseconds.

        So an unreadable lock is given a moment to become readable before it
        is believed to be rubbish. Only an already-abnormal path pays for
        this, and it costs a tenth of a second at the very worst.
        """
        import time

        for attempt in range(5):
            found = self._read()
            if found is not None:
                return found
            if not self.path.exists():
                return None      # genuinely gone; nothing to wait for
            if attempt < 4:
                time.sleep(0.02)
        return None

    def _read(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            # No file, or one that will not parse. An unreadable lock is not
            # evidence of a running app, and treating it as one would leave
            # someone unable to open their own app with nothing to fix.
            return None
        if not pid:
            return None
        return {"pid": pid, "program": data.get("program", "")}

    def release(self) -> None:
        """Give it up. Only ever removes a lock this process owns."""
        if not self.held:
            return
        try:
            mine = self._read()
            if mine is not None and mine["pid"] == os.getpid():
                self.path.unlink()
        except OSError:
            pass
        self.held = False

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
