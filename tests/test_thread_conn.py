"""Stores close EVERY thread's connection, not only the caller's.

Before, `close()` closed the calling thread's connection and left every
other thread's open -- one per phone-bridge request -- which leaked handles
in the app and made 33 tests error on Windows when their temp directory was
removed under an open cards.db / versions.db.
"""

from __future__ import annotations

import os
import sqlite3
import threading

import pytest

from densa_deck.combos.data import ComboStore
from densa_deck.data.database import CardDatabase
from densa_deck.data.rulings import RulingsStore
from densa_deck.data.thread_conn import ThreadConnections, connect_shared
from densa_deck.versioning.storage import VersionStore


def _in_thread(fn):
    out = {}

    def run():
        try:
            out["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            out["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if "error" in out:
        raise out["error"]
    return out.get("value")


def _is_closed(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


@pytest.mark.parametrize("make", [
    lambda p: CardDatabase(db_path=p / "cards.db"),
    lambda p: ComboStore(db_path=p / "combos.db"),
    lambda p: RulingsStore(db_path=p / "rulings.db"),
    lambda p: VersionStore(db_path=p / "versions.db"),
])
def test_close_closes_connections_made_on_other_threads(tmp_path, make):
    store = make(tmp_path)
    mine = store.connect()
    theirs = _in_thread(store.connect)
    assert theirs is not mine

    store.close()

    assert _is_closed(mine)
    assert _is_closed(theirs)
    # And the files can go -- the Windows symptom this was about.
    for f in tmp_path.iterdir():
        os.remove(f)


def test_a_finished_threads_connection_is_reaped_on_the_next_connect(tmp_path):
    store = VersionStore(db_path=tmp_path / "versions.db")
    dead = _in_thread(store.connect)          # its thread has now ended
    assert not _is_closed(dead)

    store.connect()                           # any new connection reaps it

    assert _is_closed(dead)
    assert store._local.open_count() == 1


def test_a_store_still_works_after_close(tmp_path):
    store = VersionStore(db_path=tmp_path / "versions.db")
    store.connect()
    store.close()
    assert store.list_decks() == []


def test_per_connection_notes_are_per_thread(tmp_path):
    tc = ThreadConnections()
    tc.conn = connect_shared(tmp_path / "a.db")
    tc.meta["attached"] = "x"

    assert _in_thread(lambda: tc.meta.get("attached")) is None
    assert tc.meta["attached"] == "x"


def test_notes_go_with_the_connection_they_describe(tmp_path):
    tc = ThreadConnections()
    tc.conn = connect_shared(tmp_path / "a.db")
    tc.meta["attached"] = "x"
    tc.conn = connect_shared(tmp_path / "b.db")   # a different connection
    assert "attached" not in tc.meta


def test_unknown_attributes_are_refused_not_silently_shared():
    with pytest.raises(AttributeError):
        ThreadConnections().collection_attached = "x"


def test_collection_attach_is_tracked_per_thread(tmp_path):
    """The regression the shared flag caused: thread B believed thread A's
    ATTACH applied to its own connection and queried a missing schema."""
    coll = tmp_path / "collection.db"
    c = sqlite3.connect(coll)
    c.execute("CREATE TABLE collection_items (x)")
    c.commit()
    c.close()
    db = CardDatabase(db_path=tmp_path / "cards.db")

    assert db.attach_collection(coll)
    db.connect().execute("SELECT * FROM collection.collection_items")

    def other_thread():
        assert db.attach_collection(coll)
        db.connect().execute("SELECT * FROM collection.collection_items")
    _in_thread(other_thread)
    db.close()
