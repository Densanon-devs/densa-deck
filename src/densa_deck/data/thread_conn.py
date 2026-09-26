"""One SQLite connection per thread, all of which can actually be closed.

The stores (cards, combos, rulings, versions) hand each thread its own
connection, because a connection is pinned to the thread that made it. They
kept those in a `threading.local` and closed only the CALLING thread's on
`close()`. Every other thread's connection -- a background download, and
above all each request thread of the phone bridge, which is a new thread per
request -- stayed open until the garbage collector happened to reach it.

In the app that is a slow handle leak for as long as a phone is connected.
In the tests it was 33 errors: the temp directory is removed while a
request thread's connection still holds cards.db or versions.db open.

`ThreadConnections` keeps the `.conn` attribute interface of the local it
replaces, so the stores read and write `self._local.conn` exactly as before.
It additionally remembers every connection it has handed out, closes those
belonging to threads that have finished whenever a new one is made, and
closes all of them on `close_all()`. Connections are opened with
`check_same_thread=False` so that the closing can happen from whichever
thread shuts the store down; each is still only USED by its own thread.
"""

from __future__ import annotations

import sqlite3
import threading


class ThreadConnections:
    # Slots, so that `store._local.something = x` raises instead of quietly
    # becoming one value shared by every thread -- which is what a plain
    # attribute on this object would be, unlike on a threading.local.
    __slots__ = ("_lock", "_by_thread", "_meta")

    def __init__(self):
        self._lock = threading.Lock()
        self._by_thread: dict[threading.Thread, sqlite3.Connection] = {}
        self._meta: dict[threading.Thread, dict] = {}

    @property
    def meta(self) -> dict:
        """Per-connection notes (e.g. which database is ATTACHed to it).

        Belongs to the current thread's connection and is dropped with it,
        so a note can never describe a connection other than the one in use.
        """
        me = threading.current_thread()
        with self._lock:
            return self._meta.setdefault(me, {})

    @property
    def conn(self) -> sqlite3.Connection | None:
        with self._lock:
            return self._by_thread.get(threading.current_thread())

    @conn.setter
    def conn(self, value: sqlite3.Connection | None) -> None:
        me = threading.current_thread()
        stale: list[sqlite3.Connection] = []
        with self._lock:
            # Reap connections whose threads have ended. A thread-per-request
            # server would otherwise leave one behind per request.
            for thread in [t for t in self._by_thread if t is not me and not t.is_alive()]:
                stale.append(self._by_thread.pop(thread))
                self._meta.pop(thread, None)
            old = self._by_thread.pop(me, None)
            if old is not value:
                self._meta.pop(me, None)
            if value is not None:
                self._by_thread[me] = value
        if old is not None and old is not value:
            stale.append(old)
        for c in stale:
            _close_quietly(c)

    def close_all(self) -> None:
        with self._lock:
            conns = list(self._by_thread.values())
            self._by_thread.clear()
            self._meta.clear()
        for c in conns:
            _close_quietly(c)

    def open_count(self) -> int:
        with self._lock:
            return len(self._by_thread)


def connect_shared(path) -> sqlite3.Connection:
    """A connection that `ThreadConnections.close_all` may close from any thread."""
    return sqlite3.connect(str(path), check_same_thread=False)


def _close_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    except Exception:
        pass
