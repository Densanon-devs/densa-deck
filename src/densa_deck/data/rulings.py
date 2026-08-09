"""Opt-in Scryfall rulings cache.

Rulings are the official clarifications Wizards publishes for individual
cards ("does this trigger if the creature already left?"). Nothing in the
simulator or the validator needs them — they're prose for humans — so they
are **not** part of the normal card ingest and are never downloaded
automatically. A user who wants them asks for them.

The dataset is a separate ~5 MB gzipped bulk file from Scryfall, stored in
its own database (`~/.densa-deck/rulings.db`) so that opting out is a file
delete and users who never opt in carry no extra weight. This mirrors how
the Commander Spellbook combo cache works.

Rulings text is © Wizards of the Coast, distributed via Scryfall. Both
attributions are surfaced wherever rulings are displayed.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable, Iterable

import httpx

from densa_deck.data.scryfall import (
    bulk_download_url,
    bulk_size_bytes,
    download_bulk_file,
    iter_bulk_records,
)

DEFAULT_RULINGS_DB_PATH = Path.home() / ".densa-deck" / "rulings.db"
SCRYFALL_BULK_API = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "rulings"

RULINGS_ATTRIBUTION = (
    "Rulings © Wizards of the Coast, provided via Scryfall (https://scryfall.com)."
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rulings (
    oracle_id TEXT NOT NULL,
    published_at TEXT DEFAULT '',
    source TEXT DEFAULT '',
    comment TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rulings_oracle ON rulings(oracle_id);

CREATE TABLE IF NOT EXISTS rulings_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class RulingsStore:
    """SQLite-backed rulings cache, keyed by Scryfall oracle_id.

    Thread-safe via thread-local connections, matching CardDatabase and
    ComboStore — the desktop dispatcher and a background download thread
    can both touch it.
    """

    def __init__(self, db_path: Path | str = DEFAULT_RULINGS_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            with self._schema_lock:
                if not self._schema_ready:
                    conn.executescript(_SCHEMA)
                    self._schema_ready = True
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # --- reads ---

    def ruling_count(self) -> int:
        try:
            row = self.connect().execute("SELECT COUNT(*) FROM rulings").fetchone()
        except sqlite3.DatabaseError:
            return 0
        return row[0] if row else 0

    def is_installed(self) -> bool:
        """True when the user has opted in and the download completed."""
        return self.db_path.exists() and self.ruling_count() > 0

    def get_metadata(self, key: str) -> str | None:
        row = self.connect().execute(
            "SELECT value FROM rulings_metadata WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str):
        conn = self.connect()
        conn.execute(
            "INSERT INTO rulings_metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()

    def rulings_for_oracle_id(self, oracle_id: str) -> list[dict]:
        """All rulings for a card, oldest first."""
        if not oracle_id:
            return []
        rows = self.connect().execute(
            "SELECT published_at, source, comment FROM rulings "
            "WHERE oracle_id = ? ORDER BY published_at",
            (oracle_id,),
        ).fetchall()
        return [
            {"published_at": r[0], "source": r[1], "comment": r[2]} for r in rows
        ]

    # --- writes ---

    def replace_all(self, records: Iterable[dict],
                    progress: Callable[[int], None] | None = None) -> int:
        """Replace the cache with a fresh set of rulings.

        Written to a temporary table and swapped at the end so an
        interrupted download leaves the previous cache intact rather than
        a half-populated one.
        """
        conn = self.connect()
        conn.execute("DROP TABLE IF EXISTS rulings_new")
        conn.execute(
            "CREATE TABLE rulings_new ("
            "oracle_id TEXT NOT NULL, published_at TEXT DEFAULT '', "
            "source TEXT DEFAULT '', comment TEXT NOT NULL)"
        )
        count = 0
        batch: list[tuple] = []
        for record in records:
            oracle_id = record.get("oracle_id")
            comment = record.get("comment")
            if not oracle_id or not comment:
                continue
            batch.append((
                oracle_id,
                record.get("published_at") or "",
                record.get("source") or "",
                comment,
            ))
            if len(batch) >= 5000:
                conn.executemany("INSERT INTO rulings_new VALUES (?, ?, ?, ?)", batch)
                count += len(batch)
                batch.clear()
                if progress:
                    progress(count)
        if batch:
            conn.executemany("INSERT INTO rulings_new VALUES (?, ?, ?, ?)", batch)
            count += len(batch)
            if progress:
                progress(count)

        conn.execute("DROP TABLE IF EXISTS rulings")
        conn.execute("ALTER TABLE rulings_new RENAME TO rulings")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rulings_oracle ON rulings(oracle_id)")
        conn.commit()
        return count

    def remove(self) -> bool:
        """Delete the rulings cache entirely. Opting back out is a file delete."""
        self.close()
        removed = False
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                path.unlink()
                removed = True
        self._schema_ready = False
        return removed


async def fetch_rulings_manifest() -> dict:
    """Scryfall bulk-data manifest entry for rulings."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(SCRYFALL_BULK_API, headers={"Accept": "application/json"})
        resp.raise_for_status()
        for item in resp.json().get("data", []):
            if item.get("type") == BULK_TYPE:
                return item
    raise RuntimeError(f"Could not find bulk data type '{BULK_TYPE}' in Scryfall API response")


async def download_rulings(
    store: RulingsStore | None = None,
    progress: Callable[[int], None] | None = None,
) -> int:
    """Download and store the rulings dataset. Returns the ruling count.

    Explicitly invoked — there is no auto-check and no launch-time prompt.
    """
    if store is None:
        store = RulingsStore()

    manifest = await fetch_rulings_manifest()
    url = bulk_download_url(manifest)
    updated_at = manifest.get("updated_at", "")

    cache_dir = store.db_path.parent / "bulk"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".jsonl.gz" if url.endswith(".gz") else ".json"
    dest = cache_dir / f"rulings{suffix}"

    try:
        await download_bulk_file(url, dest)
        count = store.replace_all(iter_bulk_records(dest), progress=progress)
        store.set_metadata("scryfall_rulings_updated_at", updated_at)
        store.set_metadata("ruling_count", str(count))
        store.set_metadata("size_bytes", str(bulk_size_bytes(manifest)))
        from datetime import datetime as _dt
        store.set_metadata(
            "last_download_completed_at", _dt.now().isoformat(timespec="seconds")
        )
        return count
    finally:
        dest.unlink(missing_ok=True)


async def rulings_update_available(store: RulingsStore | None = None) -> dict:
    """Is Scryfall's rulings file newer than the one we downloaded?

    Only meaningful once the user has opted in. Fails open so a network
    problem never blocks anything.
    """
    if store is None:
        store = RulingsStore()
    if not store.is_installed():
        return {"installed": False, "available": False}
    try:
        manifest = await fetch_rulings_manifest()
    except Exception as exc:  # network down, Scryfall unhappy, etc.
        return {"installed": True, "available": False, "error": str(exc)}
    remote = str(manifest.get("updated_at", ""))
    local = store.get_metadata("scryfall_rulings_updated_at") or ""
    return {
        "installed": True,
        "available": bool(remote and remote > local),
        "remote_updated_at": remote,
        "local_updated_at": local,
        "size_mb": round(bulk_size_bytes(manifest) / (1024 * 1024), 1),
    }
