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
"""

from __future__ import annotations

import json
import os
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


class InstanceLock:
    """Held for the life of the app. Released on close, and on a crash."""

    def __init__(self, data_dir: Path | str):
        self.path = Path(data_dir) / "app.lock"
        self.held = False

    def acquire(self) -> None:
        """Take the lock, or raise `AlreadyRunning`."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        existing = self._read()
        if existing is not None and _pid_alive(existing):
            raise AlreadyRunning(existing, self.path)

        # Either nothing was there or it named a process that has gone. Either
        # way this copy takes it. Written whole rather than appended so a
        # half-written file from a bad shutdown cannot read as a valid pid.
        self.path.write_text(
            json.dumps({"pid": os.getpid()}), encoding="utf-8")
        self.held = True

    def _read(self) -> int | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
        except (OSError, ValueError, TypeError):
            # No file, or one that will not parse. An unreadable lock is not
            # evidence of a running app, and treating it as one would leave
            # someone unable to open their own app with nothing to fix.
            return None
        return pid or None

    def release(self) -> None:
        """Give it up. Only ever removes a lock this process owns."""
        if not self.held:
            return
        try:
            if self._read() == os.getpid():
                self.path.unlink()
        except OSError:
            pass
        self.held = False

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
