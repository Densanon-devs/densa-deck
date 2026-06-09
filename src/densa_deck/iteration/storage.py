"""Persistent log of accepted/rejected proposals.

The point of the log is to give the user a longitudinal view of their
iteration — "you cut 8 cards this week, power went 7.2 → 6.8, gained
2 combo lines." Without this, every iteration session is a blank slate.

Lives in `~/.densa-deck/iterations.db` so it survives across runs. Schema
is intentionally narrow: one row per accepted/rejected proposal.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS iteration_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id TEXT NOT NULL,
        deck_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        card_name TEXT NOT NULL,
        accepted INTEGER NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        signal TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        before_power REAL,
        after_power REAL,
        before_total_cards INTEGER,
        after_total_cards INTEGER,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_iter_deck ON iteration_log(deck_id, created_at DESC)",
]


@dataclass
class IterationRecord:
    """One accepted/rejected proposal row."""

    id: int | None
    deck_id: str
    deck_name: str
    kind: str
    card_name: str
    accepted: bool
    source: str = ""
    signal: str = ""
    reason: str = ""
    before_power: float | None = None
    after_power: float | None = None
    before_total_cards: int | None = None
    after_total_cards: int | None = None
    created_at: str = ""


class IterationStore:
    """SQLite-backed iteration log."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else (Path.home() / ".densa-deck" / "iterations.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def record(self, record: IterationRecord) -> IterationRecord:
        """Append a row. Returns the row with its auto-assigned id + created_at."""
        ts = record.created_at or datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO iteration_log
                   (deck_id, deck_name, kind, card_name, accepted, source, signal,
                    reason, before_power, after_power, before_total_cards,
                    after_total_cards, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.deck_id, record.deck_name, record.kind, record.card_name,
                    1 if record.accepted else 0,
                    record.source, record.signal, record.reason,
                    record.before_power, record.after_power,
                    record.before_total_cards, record.after_total_cards,
                    ts,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
        record.id = new_id
        record.created_at = ts
        return record

    def history(self, deck_id: str, *, limit: int = 50) -> list[IterationRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, deck_id, deck_name, kind, card_name, accepted, source,
                          signal, reason, before_power, after_power,
                          before_total_cards, after_total_cards, created_at
                   FROM iteration_log WHERE deck_id = ? ORDER BY created_at DESC LIMIT ?""",
                (deck_id, limit),
            ).fetchall()
        return [
            IterationRecord(
                id=r[0], deck_id=r[1], deck_name=r[2], kind=r[3], card_name=r[4],
                accepted=bool(r[5]), source=r[6], signal=r[7], reason=r[8],
                before_power=r[9], after_power=r[10],
                before_total_cards=r[11], after_total_cards=r[12],
                created_at=r[13],
            )
            for r in rows
        ]

    def summary(self, deck_id: str) -> dict:
        """Aggregate stats over the deck's iteration history.

        Returns counts of accepted/rejected per kind plus the net power
        delta from the first accepted record to the latest accepted record.
        """
        with self._connect() as conn:
            # ORDER BY id keeps insertion order deterministic even when
            # multiple records land in the same second — SQLite's
            # created_at column has 1s resolution.
            rows = conn.execute(
                """SELECT kind, accepted, before_power, after_power
                   FROM iteration_log WHERE deck_id = ? ORDER BY id""",
                (deck_id,),
            ).fetchall()
        accepted_cuts = sum(1 for r in rows if r[0] == "cut" and r[1])
        rejected_cuts = sum(1 for r in rows if r[0] == "cut" and not r[1])
        accepted_adds = sum(1 for r in rows if r[0] == "add" and r[1])
        rejected_adds = sum(1 for r in rows if r[0] == "add" and not r[1])

        accepted = [r for r in rows if r[1]]
        first_before = next((r[2] for r in accepted if r[2] is not None), None)
        last_after = next((r[3] for r in reversed(accepted) if r[3] is not None), None)
        net_power_delta = None
        if first_before is not None and last_after is not None:
            net_power_delta = round(last_after - first_before, 2)

        return {
            "deck_id": deck_id,
            "total_records": len(rows),
            "accepted_cuts": accepted_cuts,
            "rejected_cuts": rejected_cuts,
            "accepted_adds": accepted_adds,
            "rejected_adds": rejected_adds,
            "net_power_delta": net_power_delta,
        }
