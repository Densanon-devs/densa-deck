"""SQLite persistence for pods.

Lives at `~/.densa-deck/playgroup.db` by default. Two tables:

  pod_meta(name PK, is_default INTEGER, created_at TEXT)
  pod_members(pod_name, commander_name, archetype, power_level, notes, position,
              PRIMARY KEY (pod_name, commander_name))

Membership is keyed by commander name within a pod — adding a member with a
commander that's already there *updates* the existing slot (archetype, power,
notes). To replace a player who switched commanders, remove the old entry
first; the position field controls display order.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from densa_deck.playgroup.models import Pod, PodMember


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS pod_meta (
        name TEXT PRIMARY KEY,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS pod_members (
        pod_name TEXT NOT NULL,
        commander_name TEXT NOT NULL,
        archetype TEXT NOT NULL DEFAULT 'unknown',
        power_level REAL,
        notes TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (pod_name, commander_name),
        FOREIGN KEY (pod_name) REFERENCES pod_meta(name) ON DELETE CASCADE
    )""",
    "CREATE INDEX IF NOT EXISTS idx_members_pod ON pod_members(pod_name)",
]


class PlaygroupStore:
    """Wraps a SQLite file holding pod data."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else (Path.home() / ".densa-deck" / "playgroup.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

    # ----------------------------------------------------- pod CRUD

    def list_pods(self) -> list[Pod]:
        """All pods, populated with members."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, is_default, created_at FROM pod_meta ORDER BY name COLLATE NOCASE"
            ).fetchall()
            out: list[Pod] = []
            for name, is_default, created_at in rows:
                members = self._load_members(conn, name)
                out.append(Pod(
                    name=name, members=members,
                    is_default=bool(is_default), created_at=created_at,
                ))
            return out

    def get_pod(self, name: str) -> Pod | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, is_default, created_at FROM pod_meta WHERE name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if not row:
                return None
            members = self._load_members(conn, row[0])
            return Pod(name=row[0], members=members, is_default=bool(row[1]), created_at=row[2])

    def get_default_pod(self) -> Pod | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, is_default, created_at FROM pod_meta WHERE is_default = 1 LIMIT 1"
            ).fetchone()
            if not row:
                return None
            members = self._load_members(conn, row[0])
            return Pod(name=row[0], members=members, is_default=True, created_at=row[2])

    def create_pod(self, name: str, *, is_default: bool = False) -> Pod:
        name = name.strip()
        if not name:
            raise ValueError("pod name cannot be empty")
        now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO pod_meta (name, is_default, created_at) VALUES (?, ?, ?)",
                (name, 1 if is_default else 0, now),
            )
            if is_default:
                conn.execute(
                    "UPDATE pod_meta SET is_default = CASE WHEN name = ? THEN 1 ELSE 0 END",
                    (name,),
                )
            conn.commit()
        return self.get_pod(name)  # type: ignore[return-value]

    def delete_pod(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM pod_meta WHERE name = ? COLLATE NOCASE", (name,))
            conn.commit()
            return cur.rowcount > 0

    def set_default(self, name: str) -> bool:
        """Mark `name` as the default pod, clearing the flag on others."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM pod_meta WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE pod_meta SET is_default = CASE WHEN name = ? THEN 1 ELSE 0 END",
                (row[0],),
            )
            conn.commit()
            return True

    # ----------------------------------------------------- member CRUD

    def add_member(self, pod_name: str, member: PodMember) -> Pod:
        """Add or update a member (keyed by commander name) within a pod."""
        if not self.get_pod(pod_name):
            self.create_pod(pod_name)
        with self._connect() as conn:
            # Compute the next position only if this is a fresh insert; an
            # update keeps the existing position so display order doesn't shift.
            existing = conn.execute(
                "SELECT position FROM pod_members WHERE pod_name = ? AND commander_name = ? COLLATE NOCASE",
                (pod_name, member.commander_name),
            ).fetchone()
            if existing is None:
                pos_row = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) FROM pod_members WHERE pod_name = ?",
                    (pod_name,),
                ).fetchone()
                position = (pos_row[0] if pos_row else -1) + 1
            else:
                position = existing[0]
            conn.execute(
                """INSERT OR REPLACE INTO pod_members
                   (pod_name, commander_name, archetype, power_level, notes, position)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    pod_name, member.commander_name, member.archetype,
                    member.power_level, member.notes, position,
                ),
            )
            conn.commit()
        return self.get_pod(pod_name)  # type: ignore[return-value]

    def remove_member(self, pod_name: str, commander_name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM pod_members WHERE pod_name = ? AND commander_name = ? COLLATE NOCASE",
                (pod_name, commander_name),
            )
            conn.commit()
            return cur.rowcount > 0

    def _load_members(self, conn: sqlite3.Connection, pod_name: str) -> list[PodMember]:
        rows = conn.execute(
            """SELECT commander_name, archetype, power_level, notes, position
               FROM pod_members WHERE pod_name = ? ORDER BY position, commander_name""",
            (pod_name,),
        ).fetchall()
        return [
            PodMember(
                commander_name=name, archetype=arch, power_level=pwr,
                notes=notes or "", position=pos,
            )
            for (name, arch, pwr, notes, pos) in rows
        ]
