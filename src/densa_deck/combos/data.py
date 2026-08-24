"""Combo dataset persistence + fetch.

We pull the full /variants/ pagination from Commander Spellbook's backend
into a local SQLite database (~30k rows). The fetch is idempotent and
refreshable on demand — the desktop app's Settings panel exposes a
"Refresh combo data" button that re-walks the endpoint.

We deliberately do NOT bundle the snapshot in the PyInstaller package. The
combo set updates frequently as the community adds new variants; bundling
locks the binary to whatever the dataset looked like at build time and
forces a full re-release for each combo update. Fetch-on-first-use lets
v0.2.x ride combo updates without re-shipping the binary.

Table schema:
  combos(
    combo_id TEXT PRIMARY KEY,
    color_identity TEXT,
    bracket_tag TEXT,
    legal_commander INTEGER,
    popularity INTEGER,
    mana_value_needed REAL,
    description TEXT,
    notable_prerequisites TEXT,
    cards_json TEXT,        # ["Card A", "Card B", ...]
    templates_json TEXT,    # ["Permanent that can be cast using {C}", ...]
    produces_json TEXT      # ["Infinite colorless mana", ...]
  )
  combo_card_index(card_name COLLATE NOCASE, combo_id)  -- for fast deck-vs-combo lookup
  metadata(key, value)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

import httpx

from densa_deck.combos.models import Combo

DEFAULT_COMBO_DB_PATH = Path.home() / ".densa-deck" / "combos.db"
SPELLBOOK_API_BASE = "https://backend.commanderspellbook.com"
PAGE_SIZE = 500
USER_AGENT_DEFAULT = "DensaDeck/0.6.0 (combo-fetch)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS combos (
    combo_id TEXT PRIMARY KEY,
    color_identity TEXT DEFAULT '',
    bracket_tag TEXT DEFAULT '',
    legal_commander INTEGER DEFAULT 1,
    popularity INTEGER DEFAULT 0,
    mana_value_needed REAL DEFAULT 0,
    description TEXT DEFAULT '',
    notable_prerequisites TEXT DEFAULT '',
    cards_json TEXT NOT NULL,
    templates_json TEXT DEFAULT '[]',
    produces_json TEXT DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS combo_card_index (
    card_name TEXT NOT NULL COLLATE NOCASE,
    combo_id TEXT NOT NULL,
    PRIMARY KEY (card_name, combo_id)
);
CREATE INDEX IF NOT EXISTS idx_combo_card_index_name ON combo_card_index(card_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS combo_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ComboStore:
    """SQLite-backed combo cache with fast deck-vs-combo lookup.

    Thread-safe via thread-local connections (same pattern as CardDatabase
    in `data/database.py`) — the desktop app's pywebview dispatcher and
    background fetch thread can both touch the store concurrently.
    """

    def __init__(self, db_path: Path | str = DEFAULT_COMBO_DB_PATH):
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

    # -------------------------------------------------------------- counts

    def combo_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) FROM combos").fetchone()
        return row[0] if row else 0

    def get_metadata(self, key: str) -> str | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT value FROM combo_metadata WHERE key = ?", (key,),
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str):
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO combo_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()

    # -------------------------------------------------------------- writes

    def upsert_combos(self, combos: Iterable[Combo], batch_size: int = 500) -> int:
        """Insert or replace combos. Returns the count written.

        We rebuild the combo_card_index entries for each upserted combo so
        a card-rename in upstream gets reflected (delete-then-insert keeps
        the index honest without a full rebuild).
        """
        conn = self.connect()
        written = 0
        batch: list[Combo] = []
        for combo in combos:
            batch.append(combo)
            if len(batch) >= batch_size:
                self._flush_batch(conn, batch)
                written += len(batch)
                batch = []
        if batch:
            self._flush_batch(conn, batch)
            written += len(batch)
        return written

    def _flush_batch(self, conn: sqlite3.Connection, combos: list[Combo]) -> None:
        # Two-step transaction: delete index rows for the touched combos,
        # then upsert the combos table, then re-insert the index rows.
        # All inside a single conn.execute("BEGIN") so a crash mid-flush
        # doesn't leave the index out of sync with the combos table.
        ids = [c.combo_id for c in combos]
        placeholders = ",".join("?" * len(ids))
        conn.execute("BEGIN")
        try:
            if ids:
                conn.execute(
                    f"DELETE FROM combo_card_index WHERE combo_id IN ({placeholders})",
                    ids,
                )
            conn.executemany(
                """INSERT OR REPLACE INTO combos
                   (combo_id, color_identity, bracket_tag, legal_commander,
                    popularity, mana_value_needed, description,
                    notable_prerequisites, cards_json, templates_json, produces_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        c.combo_id, c.color_identity, c.bracket_tag,
                        1 if c.legal_in_commander else 0,
                        int(c.popularity or 0), float(c.mana_value_needed or 0.0),
                        c.description, c.notable_prerequisites,
                        json.dumps(c.cards), json.dumps(c.templates),
                        json.dumps(c.produces),
                    )
                    for c in combos
                ],
            )
            index_rows = [(name, c.combo_id) for c in combos for name in c.cards]
            conn.executemany(
                "INSERT OR IGNORE INTO combo_card_index (card_name, combo_id) VALUES (?, ?)",
                index_rows,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -------------------------------------------------------------- reads

    def lookup_combos_for_card(self, card_name: str) -> list[str]:
        """Return combo IDs that include the given card."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT combo_id FROM combo_card_index WHERE card_name = ? COLLATE NOCASE",
            (card_name,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_combo(self, combo_id: str) -> Combo | None:
        conn = self.connect()
        row = conn.execute(
            """SELECT combo_id, color_identity, bracket_tag, legal_commander,
                      popularity, mana_value_needed, description,
                      notable_prerequisites, cards_json, templates_json,
                      produces_json
               FROM combos WHERE combo_id = ?""",
            (combo_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_combo(row)

    def iter_all_combos(self) -> Iterator[Combo]:
        conn = self.connect()
        for row in conn.execute(
            """SELECT combo_id, color_identity, bracket_tag, legal_commander,
                      popularity, mana_value_needed, description,
                      notable_prerequisites, cards_json, templates_json,
                      produces_json
               FROM combos"""
        ):
            yield _row_to_combo(row)


def _row_to_combo(row: tuple) -> Combo:
    (combo_id, identity, bracket, legal, pop, mvn, desc, notable,
     cards_json, templates_json, produces_json) = row
    return Combo(
        combo_id=str(combo_id),
        color_identity=str(identity or ""),
        bracket_tag=str(bracket or ""),
        legal_in_commander=bool(legal),
        popularity=int(pop or 0),
        mana_value_needed=float(mvn or 0.0),
        description=str(desc or ""),
        notable_prerequisites=str(notable or ""),
        cards=list(json.loads(cards_json or "[]")),
        templates=list(json.loads(templates_json or "[]")),
        produces=list(json.loads(produces_json or "[]")),
        spellbook_url=f"https://commanderspellbook.com/combo/{combo_id}/",
    )


# -------------------------------------------------------------- fetch


def _parse_variant(raw: dict) -> Combo | None:
    """Project an upstream variant JSON onto our Combo model.

    Skip variants with status != "OK" (they're spoiler / unverified /
    not-yet-implemented entries in upstream).
    """
    status = raw.get("status", "")
    if status != "OK":
        return None
    combo_id = str(raw.get("id") or "")
    if not combo_id:
        return None
    cards: list[str] = []
    for use in raw.get("uses") or []:
        card = (use or {}).get("card") or {}
        name = card.get("name")
        if name:
            cards.append(name)
    templates: list[str] = []
    for req in raw.get("requires") or []:
        tmpl = (req or {}).get("template") or {}
        n = tmpl.get("name")
        if n:
            templates.append(n)
    produces: list[str] = []
    for p in raw.get("produces") or []:
        feat = (p or {}).get("feature") or {}
        n = feat.get("name")
        if n:
            produces.append(n)
    legalities = raw.get("legalities") or {}
    return Combo(
        combo_id=combo_id,
        cards=cards,
        templates=templates,
        produces=produces,
        color_identity=str(raw.get("identity") or ""),
        bracket_tag=str(raw.get("bracketTag") or ""),
        description=str(raw.get("description") or ""),
        popularity=int(raw.get("popularity") or 0),
        legal_in_commander=bool(legalities.get("commander", True)),
        spellbook_url=f"https://commanderspellbook.com/combo/{combo_id}/",
        mana_value_needed=float(raw.get("manaValueNeeded") or 0.0),
        notable_prerequisites=str(raw.get("notablePrerequisites") or ""),
    )


async def _walk_variants(
    *,
    user_agent: str,
    progress_cb=None,
    start_url: str | None = None,
) -> list[Combo]:
    """Walk the paginated /variants/ endpoint, yielding parsed combos.

    Polite: 250ms inter-page sleep + a User-Agent identifying Densa Deck.

    **Retries on 429.** This walk is ~60 requests back to back, so tripping a
    rate limiter is an ordinary event, not an exotic one. The original code
    called `raise_for_status()` with no retry, which meant one 429 anywhere in
    the sequence threw away every page fetched so far and showed the user an
    opaque error — observed in the wild on 2026-08-17. Backing off and
    resuming costs a few seconds; failing costs the entire refresh.

    Transient 5xx and network blips get the same treatment for the same
    reason. A hard 4xx (a genuinely bad request) still fails fast, because
    retrying that would just be slower failure.
    """
    out: list[Combo] = []
    url: str | None = start_url or f"{SPELLBOOK_API_BASE}/variants/?limit={PAGE_SIZE}"
    pages = 0
    async with httpx.AsyncClient(
        timeout=60, headers={"User-Agent": user_agent, "Accept": "application/json"},
    ) as client:
        while url:
            try:
                data = await _get_page(client, url, progress_cb=progress_cb,
                                       pages_done=pages, combos_seen=len(out))
            except Exception as exc:
                # Sustained rate limiting, not a blip. Hand back everything
                # fetched so far rather than discarding it — combos are
                # independent facts, so 58,000 of them is worth vastly more
                # than zero, and the next refresh simply tops it up.
                raise PartialComboWalk(out, pages, str(exc), next_url=url) from exc

            for raw in data.get("results") or []:
                combo = _parse_variant(raw)
                if combo:
                    out.append(combo)
            pages += 1
            if progress_cb:
                progress_cb(pages, len(out))
            url = data.get("next")
            if url:
                await asyncio.sleep(PAGE_SPACING_SECONDS)
    return out


class PartialComboWalk(Exception):
    """The walk stopped early but produced usable data.

    Carries the combos already fetched so the caller can persist them instead
    of throwing away the work.
    """

    def __init__(self, combos: list, pages: int, reason: str,
                 next_url: str | None = None):
        super().__init__(reason)
        self.combos = combos
        self.pages = pages
        self.reason = reason
        # Where to pick up. Without this, a re-run restarts at page 1,
        # re-fetches the same pages, and hits the same wall forever — the
        # store would never get past the point where the limiter kicks in.
        self.next_url = next_url


# Spacing between pages. Raised from 0.25s after the live API rate-limited a
# real refresh at page 117 on 2026-08-17 — the dataset has roughly doubled
# since the original figure was chosen, so the old pacing now sustains far
# more requests than the server tolerates.
PAGE_SPACING_SECONDS = 0.6

# How many times to retry a single page before giving up on the whole walk.
MAX_PAGE_ATTEMPTS = 6
# Cap on any single backoff sleep. A server asking for a 10-minute wait via
# Retry-After should not silently freeze the progress bar for 10 minutes.
MAX_BACKOFF_SECONDS = 60.0


async def _get_page(client, url: str, *, progress_cb=None,
                    pages_done: int = 0, combos_seen: int = 0) -> dict:
    """Fetch one page, backing off politely on rate limits.

    Honours `Retry-After` when the server sends it, since that is the server
    telling us exactly what it wants; otherwise exponential backoff.
    """
    last_error: Exception | None = None

    for attempt in range(MAX_PAGE_ATTEMPTS):
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            # Connection reset / timeout mid-walk — same story as a 429.
            last_error = exc
            await _backoff(attempt, None, progress_cb, pages_done, combos_seen,
                           "Connection problem")
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = httpx.HTTPStatusError(
                f"HTTP {resp.status_code}", request=resp.request, response=resp)
            reason = ("Rate limited by Commander Spellbook"
                      if resp.status_code == 429 else
                      f"Server error {resp.status_code}")
            await _backoff(attempt, resp.headers.get("Retry-After"),
                           progress_cb, pages_done, combos_seen, reason)
            continue

        resp.raise_for_status()   # genuine 4xx — retrying won't help
        return resp.json()

    raise RuntimeError(
        f"Commander Spellbook did not respond after {MAX_PAGE_ATTEMPTS} attempts "
        f"({last_error}). Their API is rate limiting or down — your existing "
        f"combo data is unchanged. Try again in a few minutes."
    ) from last_error


async def _backoff(attempt: int, retry_after: str | None, progress_cb,
                   pages_done: int, combos_seen: int, reason: str) -> None:
    delay = 1.0 * (2 ** attempt)
    if retry_after:
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            pass  # HTTP-date form; the exponential default is fine
    delay = min(delay, MAX_BACKOFF_SECONDS)
    if progress_cb:
        # Keep the bar honest — a silent stall reads as a hang.
        try:
            progress_cb(pages_done, combos_seen,
                        f"{reason}; retrying in {delay:.0f}s...")
        except TypeError:
            progress_cb(pages_done, combos_seen)   # older 2-arg callbacks
    await asyncio.sleep(delay)


def refresh_combo_snapshot(
    store: ComboStore | None = None,
    *,
    user_agent: str = USER_AGENT_DEFAULT,
    progress_cb=None,
    restart: bool = False,
) -> int:
    """Fetch a fresh combo snapshot and write it to the local store.

    Returns the number of combos written. Synchronous — wraps the async
    walker in a fresh event loop, mirroring how `_do_ingest` runs the
    Scryfall pipeline from a worker thread.

    `progress_cb(pages_done, combos_seen)` is called once per page so the
    desktop app's progress bar can advance during the walk (~60 pages at
    PAGE_SIZE=500 for a 30k dataset).
    """
    if store is None:
        store = ComboStore()

    # Resume where the last run was cut off. Commander Spellbook rate-limits
    # a full walk partway through, so without this the store would plateau
    # at whatever page the limiter first bites and never advance.
    resume_url = ""
    if not restart and store.get_metadata("last_refresh_partial") == "1":
        resume_url = store.get_metadata("last_refresh_next_url") or ""

    loop = asyncio.new_event_loop()
    partial: PartialComboWalk | None = None
    try:
        try:
            combos = loop.run_until_complete(
                _walk_variants(user_agent=user_agent, progress_cb=progress_cb,
                               start_url=resume_url or None),
            )
        except PartialComboWalk as exc:
            # Keep what we got. A refresh that dies at page 117 used to leave
            # the user with nothing; now it leaves them with 58,000 combos and
            # a clear note that it's incomplete.
            partial = exc
            combos = exc.combos
    finally:
        loop.close()

    written = store.upsert_combos(combos)
    store.set_metadata("last_refresh_at", datetime.now().isoformat(timespec="seconds"))
    store.set_metadata("source", SPELLBOOK_API_BASE)
    store.set_metadata("combo_count", str(written))

    if partial is not None:
        store.set_metadata("last_refresh_partial", "1")
        store.set_metadata("last_refresh_next_url", partial.next_url or "")
        if not written:
            raise RuntimeError(partial.reason)
        raise PartialComboRefresh(written, partial.reason)

    store.set_metadata("last_refresh_partial", "")
    store.set_metadata("last_refresh_next_url", "")
    return written


class PartialComboRefresh(Exception):
    """Refresh saved usable data but did not finish.

    Deliberately an exception rather than a quiet return: the user asked for
    a full refresh and did not get one, so combo detection will have gaps and
    they should know. But `combos_written` is real and already stored.
    """

    def __init__(self, combos_written: int, reason: str):
        self.combos_written = combos_written
        self.reason = reason
        super().__init__(
            f"Saved {combos_written:,} combos, but the refresh didn't finish: "
            f"{reason} Combo detection will work with what was saved; run the "
            f"refresh again later to fill the gaps."
        )
