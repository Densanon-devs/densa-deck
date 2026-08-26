"""The append-only event log that two devices exchange.

Every change either device makes is written here as an event before it is
considered done. Sync is then nothing more exotic than "send the events the
other side hasn't got, and apply the ones it sends back".

Three properties make that safe, and all three are load-bearing:

* **Idempotent.** Every event carries a UUID and applying a known uid is a
  no-op, so a retried push, a duplicated response or a sync that ran twice
  cannot double-count a single card.
* **Commutative where it counts.** Quantity changes are deltas, so order of
  arrival cannot change the result. `+2` then `+3` and `+3` then `+2` both
  leave five.
* **Ordered enough to resume.** Each device numbers its own events with a
  local monotonic `seq`, so a peer can say "everything after 41" and get
  exactly that without either side keeping a per-peer copy of the data.

The seq is deliberately per-device rather than global: a global counter would
need coordination, which is the thing being avoided.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# One identity per installation, minted once and kept. Sync is meaningless
# without a stable answer to "who am I" — a device that forgets re-sends its
# whole history as if it were new.
DEVICE_ID_FILE = Path.home() / ".densa-deck" / "device.json"

# Event kinds. Kept as plain strings rather than an enum because they travel
# over the wire to a TypeScript client, and an unknown kind from a newer peer
# must be storable and forwardable rather than a parse error.
KIND_STACK_DELTA = "stack-delta"
# Absolute, and used ONLY for the first-sync baseline. See
# SyncApplier._apply_stack_set for why it is confined to that.
KIND_STACK_SET = "stack-set"
# Which lists a stack belongs to. Addressed by the stack's NATURAL key and the
# collection's uid, never by local integer ids: two devices mint their own
# item_ids and collection_ids offline, and each would think the other meant
# its own rows.
KIND_MEMBERSHIP = "membership"
KIND_COLLECTION_UPSERT = "collection-upsert"
KIND_COLLECTION_DELETE = "collection-delete"
KIND_DECK_UPSERT = "deck-upsert"
KIND_DECK_DELETE = "deck-delete"

KNOWN_KINDS = frozenset({
    KIND_STACK_DELTA,
    KIND_COLLECTION_UPSERT,
    KIND_COLLECTION_DELETE,
    KIND_DECK_UPSERT,
    KIND_DECK_DELETE,
})


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def device_id(path: Path | None = None) -> str:
    """This installation's stable identity, created on first use."""
    target = path or _device_path()
    try:
        stored = json.loads(target.read_text(encoding="utf-8")).get("device_id", "")
        if isinstance(stored, str) and len(stored) >= 8:
            return stored
    except (OSError, ValueError):
        pass
    minted = str(uuid.uuid4())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"device_id": minted}), encoding="utf-8")
    except OSError:
        # An unwritable home means the id lasts only as long as the process.
        # Sync degrades to re-sending history rather than failing outright.
        pass
    return minted


def _device_path() -> Path:
    import os
    override = os.environ.get("DENSA_DEVICE_FILE")
    return Path(override) if override else DEVICE_ID_FILE


@dataclass
class SyncEvent:
    """One thing that happened, addressed so any device can apply it."""

    kind: str
    payload: dict
    event_uid: str = field(default_factory=lambda: str(uuid.uuid4()))
    device: str = ""
    seq: int = 0
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "event_uid": self.event_uid,
            "device": self.device,
            "seq": self.seq,
            "kind": self.kind,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SyncEvent:
        payload = data.get("payload")
        if isinstance(payload, str):        # tolerated: some clients send JSON text
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        return cls(
            kind=str(data.get("kind", "")),
            payload=payload if isinstance(payload, dict) else {},
            event_uid=str(data.get("event_uid") or uuid.uuid4()),
            device=str(data.get("device", "")),
            seq=int(data.get("seq") or 0),
            created_at=str(data.get("created_at") or _now()),
        )


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS sync_events (
        event_uid TEXT PRIMARY KEY,
        device TEXT NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )""",
    # The cursor a peer resumes from: everything this device knows, in the
    # order it learned it. Ordering by (device, seq) alone would not do —
    # events learned FROM a peer keep that peer's seq, so a local monotonic
    # rowid is what makes "give me everything after N" answerable.
    "CREATE INDEX IF NOT EXISTS idx_sync_device_seq ON sync_events(device, seq)",
    "CREATE INDEX IF NOT EXISTS idx_sync_applied ON sync_events(applied_at)",
    """CREATE TABLE IF NOT EXISTS sync_peers (
        peer TEXT PRIMARY KEY,
        cursor INTEGER NOT NULL DEFAULT 0,
        last_seen_at TEXT NOT NULL
    )""",
]


class SyncLog:
    """Storage for events, and the cursors peers resume from.

    Lives in the same file as the collection so a single backup captures both
    the data and the history that explains it, and so writing an event and
    applying it can share one transaction.
    """

    def __init__(self, db_path: Path | str, *, device: str | None = None):
        self.db_path = Path(db_path)
        self.device = device or device_id()
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.commit()

    # ------------------------------------------------------------- writing

    def record(self, kind: str, payload: dict, *, conn=None) -> SyncEvent:
        """Log something this device just did."""
        event = SyncEvent(kind=kind, payload=payload, device=self.device)
        if conn is not None:
            event.seq = self._next_seq(conn)
            self._insert(conn, event)
            return event
        with self._connect() as own:
            event.seq = self._next_seq(own)
            self._insert(own, event)
            own.commit()
        return event

    def _next_seq(self, conn) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM sync_events WHERE device = ?",
            (self.device,)).fetchone()
        return int(row[0]) + 1

    def _insert(self, conn, event: SyncEvent) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO sync_events
               (event_uid, device, seq, kind, payload_json, created_at, applied_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event.event_uid, event.device, event.seq, event.kind,
             json.dumps(event.payload), event.created_at, _now()))

    def accept(self, event: SyncEvent, *, conn=None) -> bool:
        """Store an event learned from a peer. False if already known.

        The return value is the idempotency gate every caller leans on: a
        duplicate must not be applied a second time, and this is where that
        is decided.
        """
        if conn is not None:
            return self._accept(conn, event)
        with self._connect() as own:
            accepted = self._accept(own, event)
            own.commit()
            return accepted

    def _accept(self, conn, event: SyncEvent) -> bool:
        known = conn.execute(
            "SELECT 1 FROM sync_events WHERE event_uid = ?",
            (event.event_uid,)).fetchone()
        if known:
            return False
        self._insert(conn, event)
        return True

    def has(self, event_uid: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM sync_events WHERE event_uid = ?",
                                (event_uid,)).fetchone() is not None

    # ------------------------------------------------------------- reading

    def since(self, cursor: int = 0, *, limit: int = 500,
              exclude_device: str = "") -> tuple[list[SyncEvent], int]:
        """Events this device knows after `cursor`, plus the next cursor.

        `exclude_device` keeps a peer's own events from being echoed back to
        it. Harmless if they were — they would be recognised as known and
        dropped — but it halves the traffic on every exchange.
        """
        where = "WHERE rowid > ?"
        params: list = [int(cursor)]
        if exclude_device:
            where += " AND device != ?"
            params.append(exclude_device)
        params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT rowid, event_uid, device, seq, kind, payload_json,
                           created_at
                    FROM sync_events {where} ORDER BY rowid LIMIT ?""",
                params).fetchall()

        events = [SyncEvent(
            event_uid=r[1], device=r[2], seq=r[3], kind=r[4],
            payload=json.loads(r[5]), created_at=r[6]) for r in rows]
        next_cursor = rows[-1][0] if rows else int(cursor)
        return events, next_cursor

    def head(self) -> int:
        """The cursor meaning "everything currently known"."""
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM sync_events").fetchone()
            return int(row[0])

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM sync_events").fetchone()[0])

    # ------------------------------------------------------------- peers

    def peer_cursor(self, peer: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT cursor FROM sync_peers WHERE peer = ?",
                               (peer,)).fetchone()
            return int(row[0]) if row else 0

    def set_peer_cursor(self, peer: str, cursor: int) -> None:
        """Remember how far a peer has caught up.

        Only ever moves forward. A peer that reports an older cursor is
        re-reading history, which is safe — every event is idempotent — but
        must not rewind the watermark and cause an endless resend loop.
        """
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sync_peers (peer, cursor, last_seen_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(peer) DO UPDATE SET
                     cursor = MAX(cursor, excluded.cursor),
                     last_seen_at = excluded.last_seen_at""",
                (peer, int(cursor), _now()))
            conn.commit()

    def peers(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT peer, cursor, last_seen_at FROM sync_peers "
                "ORDER BY last_seen_at DESC").fetchall()
        return [{"peer": r[0], "cursor": r[1], "last_seen_at": r[2]} for r in rows]
