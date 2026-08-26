"""SQLite persistence for the physical collection.

Lives at `~/.densa-deck/collection.db` — deliberately its own file, separate
from `cards.db`. Two reasons, both load-bearing:

1. `CardDatabase.upsert_cards` uses `INSERT OR REPLACE` against an explicit
   column list. That is DELETE + INSERT, so any column bolted onto `cards`
   outside that list is silently wiped on the next Scryfall re-ingest. A
   user's collection must never be destroyed by a card-data update.
2. `cards.db` is disposable — deleting it costs a six-second re-download.
   `collection.db` is not. Splitting on "can this be re-downloaded?" means the
   irreplaceable half is one small file the user can back up.

Tables:

  collection_items   one row per stack of identical physical copies
  collection_events  append-only audit of every quantity change

The event log is not bookkeeping for its own sake: it answers "where did these
four copies come from" and it is the ledger Phase 5's cost-basis and P&L work
is built on, so it is cheaper to write it from the start than to retrofit it.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from densa_deck.collection.models import (
    CollectionItem,
    CollectionSummary,
    Condition,
    Finish,
)

DEFAULT_COLLECTION_NAME = "Main Collection"

# The default collection is a singleton CONCEPT — "cards I haven't filed
# anywhere" — so every device has to agree on it without ever having spoken.
# A random uid per device meant the desktop and the phone each had their own
# unfiled pile: removals made on one landed in a collection the other did not
# have, and syncing produced a second "Main Collection (2)". A fixed uid is
# what makes "the default" the same thing everywhere.
DEFAULT_COLLECTION_UID = "00000000-0000-4000-8000-00000000d0cc"

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS collection_items (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        printing_id TEXT NOT NULL,
        oracle_id TEXT NOT NULL DEFAULT '',
        card_name TEXT NOT NULL,
        finish TEXT NOT NULL DEFAULT 'nonfoil',
        condition TEXT NOT NULL DEFAULT 'NM',
        language TEXT NOT NULL DEFAULT 'en',
        quantity INTEGER NOT NULL DEFAULT 0,
        location TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        acquired_at TEXT,
        unit_cost_usd REAL,
        acquisition_id INTEGER,
        collection_id INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # Named collections: a grouping over what is owned, never a replacement
    # for it. The master collection is the sum of every stack regardless of
    # which collection it sits in, so nothing here can make a card stop
    # being owned.
    """CREATE TABLE IF NOT EXISTS collections (
        collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
        -- Stable across devices. The integer id above is a local fact: two
        -- devices editing offline both mint 2, and on sync each would think
        -- the other meant its own collection.
        collection_uid TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'collection',
        notes TEXT NOT NULL DEFAULT '',
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_name
       ON collections(name COLLATE NOCASE)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_uid
       ON collections(collection_uid)""",
    # The stack key. Same printing in a different box — or in a different
    # named collection — is a different stack, so both are part of identity
    # rather than free-text afterthoughts.
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_ci_stack_v2 ON collection_items(
        printing_id, finish, condition, language, location, collection_id
    )""",
    # ---- collections as FILTERS, not boxes -------------------------------
    # `collection_items.collection_id` says where a stack is FILED — one
    # place, because a physical card is in one physical box. That is not the
    # same question as which lists it belongs to: the same card can be part
    # of a set you are completing, a deck you have built, and the seventy-five
    # you took to a tournament, all at once and without moving.
    #
    # So membership is its own table and many-to-many. Filing stays a
    # property of the stack; belonging is a relationship.
    """CREATE TABLE IF NOT EXISTS collection_membership (
        item_id INTEGER NOT NULL,
        collection_id INTEGER NOT NULL,
        added_at TEXT NOT NULL,
        PRIMARY KEY (item_id, collection_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cm_collection ON collection_membership(collection_id)",
    "CREATE INDEX IF NOT EXISTS idx_cm_item ON collection_membership(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_ci_oracle ON collection_items(oracle_id)",
    "CREATE INDEX IF NOT EXISTS idx_ci_collection ON collection_items(collection_id)",
    "CREATE INDEX IF NOT EXISTS idx_ci_name ON collection_items(card_name COLLATE NOCASE)",
    """CREATE TABLE IF NOT EXISTS collection_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        printing_id TEXT NOT NULL,
        card_name TEXT NOT NULL,
        delta INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT 'manual',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ce_item ON collection_events(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_ce_created ON collection_events(created_at)",
    # Price history lives here, not in cards.db, because it accumulates over
    # time and CANNOT be rebuilt — Scryfall serves today's prices and nothing
    # else, so a day not captured is gone permanently. That makes it precious
    # by the same test as the collection itself, despite being derived data.
    # Captured for owned printings only; snapshotting all 107k daily would be
    # ~39M rows a year to chart cards nobody owns.
    """CREATE TABLE IF NOT EXISTS price_history (
        printing_id TEXT NOT NULL,
        captured_on TEXT NOT NULL,
        finish TEXT NOT NULL,
        price_usd REAL,
        source TEXT NOT NULL DEFAULT 'scryfall',
        PRIMARY KEY (printing_id, captured_on, finish)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_ph_printing ON price_history(printing_id, finish)",
    # ---- wishlist --------------------------------------------------------
    # DELIBERATELY NOT collection_items. Ownership is computed by queries
    # spread across six modules; a wishlist living in that table would need
    # every one of them to exclude it, and the first one missed would count
    # cards you do not own as owned. A separate table cannot be got wrong.
    """CREATE TABLE IF NOT EXISTS wishlist_items (
        wish_id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_name TEXT NOT NULL,
        oracle_id TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL DEFAULT 1,
        -- Which deck wants it, or '' for something added by hand. Two decks
        -- wanting one card each is a different situation from one deck
        -- wanting two, and collapsing them loses the answer to "why is this
        -- on my list".
        deck_id TEXT NOT NULL DEFAULT '',
        deck_name TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_wish_card_deck
       ON wishlist_items(card_name COLLATE NOCASE, deck_id)""",
    "CREATE INDEX IF NOT EXISTS idx_wish_deck ON wishlist_items(deck_id)",
    # ---- reseller layer -------------------------------------------------
    # An acquisition is a lot: "Mike's collection, $600, 14 Aug". Cards
    # scanned into it carry acquisition_id, and cost basis is allocated
    # across them proportionally to market value at the time of purchase —
    # hence the snapshot columns, which must NOT be recomputed later from
    # today's prices or the basis drifts with the market.
    """CREATE TABLE IF NOT EXISTS acquisitions (
        acquisition_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        purchased_on TEXT NOT NULL,
        purchase_price_usd REAL NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        basis_allocated_at TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sales (
        sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        printing_id TEXT NOT NULL,
        card_name TEXT NOT NULL,
        finish TEXT NOT NULL DEFAULT 'nonfoil',
        condition TEXT NOT NULL DEFAULT 'NM',
        quantity INTEGER NOT NULL DEFAULT 1,
        sale_price_usd REAL NOT NULL DEFAULT 0,
        fees_usd REAL NOT NULL DEFAULT 0,
        shipping_usd REAL NOT NULL DEFAULT 0,
        cost_basis_usd REAL,
        platform TEXT NOT NULL DEFAULT '',
        sold_on TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sales_sold ON sales(sold_on)",
    "CREATE INDEX IF NOT EXISTS idx_ci_acq ON collection_items(acquisition_id)",
    # ---- printing-level allocation (opt-in) ------------------------------
    # Ownership maths defaults to the oracle level, because a deck slot says
    # "Sol Ring" and the deck parser actively strips set codes — decks simply
    # cannot express which printing they want. That covers every question
    # users actually ask.
    #
    # This table is for the people who care WHICH copy is sleeved where: the
    # foil in the Commander deck, the beat-up one in the budget list. Binding
    # is explicit and always optional.
    #
    # Keyed on deck_id (stable) rather than version_id: decks are versioned
    # snapshots, and an allocation must survive the deck being re-saved.
    """CREATE TABLE IF NOT EXISTS deck_allocations (
        allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
        deck_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        card_name TEXT NOT NULL,
        zone TEXT NOT NULL DEFAULT 'mainboard',
        quantity INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_alloc_slot
        ON deck_allocations(deck_id, item_id, zone)""",
    "CREATE INDEX IF NOT EXISTS idx_alloc_deck ON deck_allocations(deck_id)",
    "CREATE INDEX IF NOT EXISTS idx_alloc_item ON deck_allocations(item_id)",
]

_ITEM_COLUMNS = (
    "item_id",
    "printing_id",
    "oracle_id",
    "card_name",
    "finish",
    "condition",
    "language",
    "quantity",
    "location",
    "notes",
    "acquired_at",
    "unit_cost_usd",
    "acquisition_id",
    # Which named collection the stack sits in. Read back with everything
    # else so callers can group and move without a second query.
    "collection_id",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class CollectionStore:
    """Wraps the SQLite file holding what you physically own."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else (Path.home() / ".densa-deck" / "collection.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._connect() as conn:
            # Both migrations run BEFORE the schema statements, because the
            # schema includes indexes over columns they add. A database
            # created by an earlier build has a `collections` table with no
            # `collection_uid`, and CREATE UNIQUE INDEX on that column fails
            # outright — the app will not open. Fresh databases never show
            # this, because their CREATE TABLE already has the column, which
            # is exactly why it survived the test suite and only appeared
            # against a real one.
            self._migrate_collections(conn)
            self._migrate_collection_uids(conn)
            for stmt in _SCHEMA:
                conn.execute(stmt)
            self._ensure_default_collection(conn)
            # After the schema, because it needs the membership table to
            # exist. Backfills where every existing stack is already filed, so
            # collections keep showing exactly what they showed before.
            self._migrate_membership(conn)
            conn.commit()

    def _migrate_membership(self, conn) -> None:
        """Every stack belongs to the collection it is filed in.

        Membership was added after the fact, so a database that predates it
        has none — and a collection whose contents suddenly read as empty
        would look like the cards had been lost. Backfilling from
        `collection_id` means the first launch after upgrading shows exactly
        what the last launch showed.

        Runs only when the table is empty. Once a user has moved things
        around, `collection_id` is no longer the whole story and re-running
        this would quietly undo their edits.
        """
        already = conn.execute(
            "SELECT COUNT(*) FROM collection_membership").fetchone()[0]
        if already:
            return
        conn.execute(
            """INSERT OR IGNORE INTO collection_membership
                   (item_id, collection_id, added_at)
               SELECT item_id, collection_id, ?
                 FROM collection_items
                WHERE collection_id IS NOT NULL""",
            (_now(),))

    def _migrate_collections(self, conn) -> None:
        """Bring an existing collection.db up to the named-collections model.

        Runs before the schema statements because it has to replace the stack
        uniqueness index: the old one keyed on location alone, so the same
        card in two collections would collide. Everything already owned lands
        in the default collection, which is what "it was all one collection
        before" means.
        """
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "collection_items" not in tables:
            return          # fresh database; the schema below creates it right

        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(collection_items)").fetchall()}
        if "collection_id" in columns:
            return          # already migrated

        conn.execute("ALTER TABLE collection_items "
                     "ADD COLUMN collection_id INTEGER NOT NULL DEFAULT 0")
        # The old index would reject the same printing in two collections.
        conn.execute("DROP INDEX IF EXISTS idx_ci_stack")

    def _migrate_collection_uids(self, conn) -> None:
        """Backfill UUIDs onto collections that predate syncing.

        Runs BEFORE the schema statements, because those include a unique
        index over `collection_uid`: a database created by an earlier build
        has no such column, and creating that index fails outright — the app
        will not open at all. Fresh databases never show this, because their
        CREATE TABLE already has the column, which is why it survived the test
        suite and only appeared against a real one.
        """
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "collections" not in tables:
            return

        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(collections)").fetchall()}
        if "collection_uid" not in columns:
            conn.execute("ALTER TABLE collections "
                         "ADD COLUMN collection_uid TEXT NOT NULL DEFAULT ''")

        # The default collection takes the WELL-KNOWN uid, whatever it had
        # before — including a random one handed out by an earlier build.
        # Both devices have to agree on what "unfiled" means without ever
        # having spoken: a random uid per device gives each its own unfiled
        # pile, so a removal made on one lands in a collection the other does
        # not have, and the card reappears on the next sync.
        conn.execute("UPDATE collections SET collection_uid = ? "
                     "WHERE is_default = 1 AND collection_uid != ?",
                     (DEFAULT_COLLECTION_UID, DEFAULT_COLLECTION_UID))

        # Everything else keeps an identity of its own.
        rows = conn.execute(
            "SELECT collection_id FROM collections "
            "WHERE collection_uid IS NULL OR collection_uid = ''").fetchall()
        for (collection_id,) in rows:
            conn.execute("UPDATE collections SET collection_uid = ? "
                         "WHERE collection_id = ?",
                         (str(uuid.uuid4()), collection_id))

    def _ensure_default_collection(self, conn) -> int:
        """The collection everything lands in when nothing else is chosen."""
        row = conn.execute(
            "SELECT collection_id FROM collections WHERE is_default = 1"
        ).fetchone()
        if row:
            default_id = row[0]
        else:
            now = _now()
            cur = conn.execute(
                """INSERT INTO collections (collection_uid, name, kind,
                                            is_default, created_at, updated_at)
                   VALUES (?, ?, 'collection', 1, ?, ?)""",
                (DEFAULT_COLLECTION_UID, DEFAULT_COLLECTION_NAME, now, now))
            default_id = cur.lastrowid
        # Anything migrated from before collections existed, or inserted with
        # no collection, belongs to the default one.
        conn.execute("UPDATE collection_items SET collection_id = ? "
                     "WHERE collection_id IS NULL OR collection_id = 0",
                     (default_id,))
        return default_id

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # --------------------------------------------------------- mutation

    # ------------------------------------------------- membership (filters)

    def add_to_collection(self, item_id: int, collection_id: int) -> bool:
        """Put a stack in a collection WITHOUT taking it out of any other.

        This is the whole point of the filter model: a card can be part of a
        set you are completing, a deck you have built, and the seventy-five
        you took to a tournament, all at once. It never moved; three lists
        just mention it.

        Returns whether anything changed, so a caller can tell "added" from
        "was already there" instead of reporting both as success.
        """
        with self._connect() as conn:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO collection_membership "
                "(item_id, collection_id, added_at) VALUES (?, ?, ?)",
                (int(item_id), int(collection_id), _now()))
            conn.commit()
            return conn.total_changes > before

    def remove_from_collection(self, item_id: int, collection_id: int) -> bool:
        """Take a stack out of one list. The card itself is untouched.

        Removing from a collection must never remove from the collection —
        the master list is the physical cards, and a filter cannot destroy
        what it filters.
        """
        with self._connect() as conn:
            before = conn.total_changes
            conn.execute(
                "DELETE FROM collection_membership "
                "WHERE item_id = ? AND collection_id = ?",
                (int(item_id), int(collection_id)))
            conn.commit()
            return conn.total_changes > before

    def move_to_collection(self, item_id: int, collection_id: int) -> None:
        """The old behaviour, kept for when you really mean "it lives here now".

        Replaces every membership rather than adding one. Used when a card
        physically changes box, as opposed to appearing in another list.
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM collection_membership WHERE item_id = ?",
                         (int(item_id),))
            conn.execute(
                "INSERT OR IGNORE INTO collection_membership "
                "(item_id, collection_id, added_at) VALUES (?, ?, ?)",
                (int(item_id), int(collection_id), _now()))
            # Filing follows the move: this is the one case where the card
            # really has gone somewhere else.
            conn.execute(
                "UPDATE collection_items SET collection_id = ?, updated_at = ? "
                "WHERE item_id = ?",
                (int(collection_id), _now(), int(item_id)))
            conn.commit()

    def collections_for_item(self, item_id: int) -> list[dict]:
        """Every list this stack appears in, default first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.collection_id, c.collection_uid, c.name, c.is_default
                     FROM collection_membership m
                     JOIN collections c ON c.collection_id = m.collection_id
                    WHERE m.item_id = ?
                    ORDER BY c.is_default DESC, c.name COLLATE NOCASE""",
                (int(item_id),)).fetchall()
        return [{"collection_id": r[0], "collection_uid": r[1], "name": r[2],
                 "is_default": bool(r[3])} for r in rows]

    def overlaps(self, min_collections: int = 2) -> list[dict]:
        """Stacks that appear in more than one list.

        Two quite different situations look the same in a plain collection
        view and are worth separating here:

          * **Deliberate.** A card is in "Ravnica set" and in a deck. Nothing
            is wrong; you simply own it and it is doing two jobs.
          * **Overcommitted.** Two decks each expect this card and you own
            one copy. You will find out at the table.

        `overcommitted` is the flag that distinguishes them: it is true when
        more lists claim the stack than there are physical copies of it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT i.item_id, i.printing_id, i.card_name, i.finish,
                          i.condition, i.language, i.quantity,
                          COUNT(m.collection_id) AS lists
                     FROM collection_items i
                     JOIN collection_membership m ON m.item_id = i.item_id
                    WHERE i.quantity > 0
                    GROUP BY i.item_id
                   HAVING lists >= ?
                    ORDER BY lists DESC, i.card_name COLLATE NOCASE""",
                (int(min_collections),)).fetchall()

            out = []
            for r in rows:
                names = conn.execute(
                    """SELECT c.name FROM collection_membership m
                         JOIN collections c ON c.collection_id = m.collection_id
                        WHERE m.item_id = ?
                        ORDER BY c.is_default DESC, c.name COLLATE NOCASE""",
                    (r[0],)).fetchall()
                out.append({
                    "item_id": r[0], "printing_id": r[1], "card_name": r[2],
                    "finish": r[3], "condition": r[4], "language": r[5],
                    "quantity": r[6], "collection_count": r[7],
                    "collections": [n[0] for n in names],
                    "overcommitted": r[7] > r[6],
                })
        return out

    def stack_quantity(
        self,
        printing_id: str,
        *,
        finish: Finish | str = Finish.NONFOIL,
        condition: Condition | str = Condition.NM,
        language: str = "en",
        collection_id: int | None = None,
    ) -> int:
        """How many of one exact stack are held, or zero.

        Exists for the first-sync baseline, which has to turn an absolute
        quantity into the delta that reaches it. Reads the same four-part
        natural key `add_copies` writes: anything less would set the wrong
        stack, and a foil is not a nonfoil.
        """
        finish = finish.value if isinstance(finish, Finish) else str(finish)
        condition = (condition.value if isinstance(condition, Condition)
                     else str(condition))
        with self._connect() as conn:
            # `None` means the default collection, exactly as it does for
            # add_copies. Treating it as a NULL collection_id instead would
            # read a stack that can never exist — rows are NOT NULL — and
            # quietly answer zero for cards that are plainly there.
            collection = (collection_id if collection_id is not None
                          else self._ensure_default_collection(conn))
            conn.commit()
            row = conn.execute(
                "SELECT quantity FROM collection_items "
                "WHERE printing_id = ? AND finish = ? AND condition = ? "
                "AND language = ? AND collection_id = ?",
                (printing_id, finish, condition, language, collection),
            ).fetchone()
        return int(row[0]) if row else 0

    def add_copies(
        self,
        printing_id: str,
        card_name: str,
        *,
        quantity: int = 1,
        oracle_id: str = "",
        finish: Finish | str = Finish.NONFOIL,
        condition: Condition | str = Condition.NM,
        language: str = "en",
        location: str = "",
        notes: str = "",
        unit_cost_usd: float | None = None,
        acquisition_id: int | None = None,
        collection_id: int | None = None,
        reason: str = "manual",
    ) -> CollectionItem:
        """Add (or with a negative quantity, remove) copies of a printing.

        Upserts onto the existing stack when one matches, so adding the same
        card twice increments rather than creating a duplicate row.

        A stack that reaches zero is deleted rather than kept at 0 — an empty
        stack is not a thing you own, and leaving them around would make
        "unique cards" wrong and clutter every listing.
        """
        if not printing_id:
            raise ValueError("printing_id is required")
        if not card_name:
            raise ValueError("card_name is required")
        fin = Finish(finish).value if not isinstance(finish, Finish) else finish.value
        cond = Condition(condition).value if not isinstance(condition, Condition) else condition.value
        now = _now()

        with self._connect() as conn:
            collection = (collection_id if collection_id is not None
                          else self._ensure_default_collection(conn))
            row = conn.execute(
                """SELECT item_id, quantity FROM collection_items
                   WHERE printing_id = ? AND finish = ? AND condition = ?
                     AND language = ? AND location = ? AND collection_id = ?""",
                (printing_id, fin, cond, language, location, collection),
            ).fetchone()

            if row:
                item_id, current = row
                new_qty = current + quantity
                if new_qty <= 0:
                    conn.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))
                    # Memberships would otherwise dangle, and the overlap
                    # view would keep reporting a card that is not here.
                    conn.execute(
                        "DELETE FROM collection_membership WHERE item_id = ?",
                        (item_id,))
                else:
                    conn.execute(
                        """UPDATE collection_items
                           SET quantity = ?, updated_at = ?,
                               notes = CASE WHEN ? != '' THEN ? ELSE notes END,
                               unit_cost_usd = COALESCE(?, unit_cost_usd),
                               acquisition_id = COALESCE(?, acquisition_id)
                           WHERE item_id = ?""",
                        (new_qty, now, notes, notes, unit_cost_usd, acquisition_id, item_id),
                    )
            else:
                if quantity <= 0:
                    # Removing from a stack that doesn't exist is a no-op, not
                    # an error — the caller's intent (own zero of these) holds.
                    return CollectionItem(
                        printing_id=printing_id, card_name=card_name, oracle_id=oracle_id,
                        finish=Finish(fin), condition=Condition(cond), language=language,
                        quantity=0, location=location, collection_id=collection,
                    )
                cur = conn.execute(
                    """INSERT INTO collection_items
                       (printing_id, oracle_id, card_name, finish, condition, language,
                        quantity, location, notes, acquired_at, unit_cost_usd,
                        acquisition_id, collection_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (printing_id, oracle_id, card_name, fin, cond, language, quantity,
                     location, notes, now, unit_cost_usd, acquisition_id, collection,
                     now, now),
                )
                item_id = cur.lastrowid
                new_qty = quantity

            # A stack belongs to the collection it is filed into. Without
            # this, a newly scanned card was in no list at all — so the
            # collection it was scanned into would not show it, which reads
            # as the scan having failed.
            if new_qty > 0:
                conn.execute(
                    "INSERT OR IGNORE INTO collection_membership "
                    "(item_id, collection_id, added_at) VALUES (?, ?, ?)",
                    (item_id, collection, now))

            conn.execute(
                """INSERT INTO collection_events
                   (item_id, printing_id, card_name, delta, reason, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (item_id, printing_id, card_name, quantity, reason, notes, now),
            )
            conn.commit()

        return CollectionItem(
            item_id=item_id, printing_id=printing_id, oracle_id=oracle_id,
            card_name=card_name, finish=Finish(fin), condition=Condition(cond),
            language=language, quantity=max(0, new_qty), location=location,
            notes=notes, unit_cost_usd=unit_cost_usd, acquisition_id=acquisition_id,
            collection_id=collection, created_at=now, updated_at=now,
        )


    # ------------------------------------------------------- collections

    def default_collection_id(self) -> int:
        with self._connect() as conn:
            collection_id = self._ensure_default_collection(conn)
            conn.commit()
            return collection_id

    def create_collection(self, name: str, *, kind: str = "collection",
                          notes: str = "", uid: str = "") -> dict:
        """A new named group. Names are unique, case-insensitively.

        Returns the existing collection when the name is already taken rather
        than raising: the scanner creates collections mid-run, and failing a
        scan because a box is already named "Bulk" would be absurd.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("A collection needs a name")
        now = _now()
        with self._connect() as conn:
            self._ensure_default_collection(conn)
            row = conn.execute(
                "SELECT collection_id FROM collections WHERE name = ? COLLATE NOCASE",
                (name,)).fetchone()
            if row:
                return self._collection_row(conn, row[0])
            cur = conn.execute(
                """INSERT INTO collections (collection_uid, name, kind, notes,
                                            created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uid or str(uuid.uuid4()), name, kind, notes, now, now))
            conn.commit()
            return self._collection_row(conn, cur.lastrowid)


    def collection_by_uid(self, uid: str) -> dict:
        """Find a collection by the identity that survives crossing devices."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT collection_id FROM collections WHERE collection_uid = ?",
                (uid,)).fetchone()
            return self._collection_row(conn, row[0]) if row else {}

    def collection_uid(self, collection_id: int) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT collection_uid FROM collections WHERE collection_id = ?",
                (collection_id,)).fetchone()
            return row[0] if row else ""

    def ensure_collection_uid(self, uid: str, *, name: str,
                              kind: str = "collection", notes: str = "") -> int:
        """The local id for a collection a peer told us about, creating it if new.

        Name collisions across devices are resolved by keeping BOTH — a
        suffix is added rather than merging — because two people (or one
        person twice) meaning different boxes by "Bulk" is likelier than
        meaning the same one, and merging is not reversible while renaming is.
        """
        existing = self.collection_by_uid(uid)
        if existing:
            return existing["collection_id"]
        candidate = (name or "Collection").strip()
        with self._connect() as conn:
            self._ensure_default_collection(conn)
            attempt, suffix = candidate, 2
            while conn.execute(
                    "SELECT 1 FROM collections WHERE name = ? COLLATE NOCASE",
                    (attempt,)).fetchone():
                attempt = f"{candidate} ({suffix})"
                suffix += 1
            now = _now()
            cur = conn.execute(
                """INSERT INTO collections (collection_uid, name, kind, notes,
                                            created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uid, attempt, kind, notes, now, now))
            conn.commit()
            return cur.lastrowid

    def rename_collection(self, collection_id: int, name: str) -> bool:
        name = (name or "").strip()
        if not name:
            raise ValueError("A collection needs a name")
        with self._connect() as conn:
            clash = conn.execute(
                """SELECT collection_id FROM collections
                   WHERE name = ? COLLATE NOCASE AND collection_id != ?""",
                (name, collection_id)).fetchone()
            if clash:
                raise ValueError(f"There is already a collection called {name!r}")
            cur = conn.execute(
                "UPDATE collections SET name = ?, updated_at = ? WHERE collection_id = ?",
                (name, _now(), collection_id))
            conn.commit()
            return cur.rowcount > 0

    def delete_collection(self, collection_id: int, *,
                          move_to: int | None = None,
                          discard_cards: bool = False) -> dict:
        """Remove a collection, in one of two genuinely different senses.

        `discard_cards=False` (the default) deletes the GROUPING only: every
        card inside moves to `move_to`, or to the default collection. Nothing
        leaves the master collection. This is what someone means by "I don't
        organise things that way any more".

        `discard_cards=True` also removes those copies from the master
        collection — the sense of "I sold the whole trade box". It is
        destructive and irreversible, so it is never the default and never
        inferred: the caller has to say so outright.

        Either way the removal is written to the event log, so the ledger and
        the cost-basis maths still describe what happened.
        """
        with self._connect() as conn:
            default_id = self._ensure_default_collection(conn)
            row = conn.execute(
                """SELECT is_default, name FROM collections
                   WHERE collection_id = ?""", (collection_id,)).fetchone()
            if not row:
                return {"deleted": False, "reason": "No such collection"}
            is_default, name = row
            if is_default and not discard_cards:
                return {"deleted": False,
                        "reason": "The default collection can't be deleted"}

            if discard_cards:
                discarded = self._discard_collection_cards(conn, collection_id, name)
                # Emptying the default collection is allowed — that is a
                # legitimate "clear everything out" — but the collection
                # itself has to stay, because cards need somewhere to land.
                if is_default:
                    conn.commit()
                    return {"deleted": False, "emptied": True,
                            "cards_removed": discarded["cards"],
                            "stacks_removed": discarded["stacks"]}
                conn.execute("DELETE FROM collections WHERE collection_id = ?",
                             (collection_id,))
                conn.commit()
                return {"deleted": True, "discarded_cards": True,
                        "cards_removed": discarded["cards"],
                        "stacks_removed": discarded["stacks"]}

            target = move_to if move_to is not None else default_id
            moved = self._merge_into(conn, collection_id, target)
            conn.execute("DELETE FROM collections WHERE collection_id = ?",
                         (collection_id,))
            conn.commit()
            return {"deleted": True, "moved_to": target, "cards_moved": moved}

    def _discard_collection_cards(self, conn, collection_id: int,
                                  name: str) -> dict:
        """Take every copy in a collection out of the master collection too.

        Written to the event log one stack at a time rather than deleted
        wholesale: the log is the ledger behind cost basis and P&L, and a
        silent mass deletion would leave both describing cards that are gone.
        """
        rows = conn.execute(
            """SELECT item_id, printing_id, card_name, quantity
               FROM collection_items WHERE collection_id = ?""",
            (collection_id,)).fetchall()
        now = _now()
        cards = 0
        for item_id, printing_id, card_name, quantity in rows:
            conn.execute(
                """INSERT INTO collection_events
                   (item_id, printing_id, card_name, delta, reason, note, created_at)
                   VALUES (?, ?, ?, ?, 'collection-deleted', ?, ?)""",
                (item_id, printing_id, card_name, -quantity,
                 f"Deleted with collection {name!r}", now))
            cards += quantity
        conn.execute("DELETE FROM collection_items WHERE collection_id = ?",
                     (collection_id,))
        return {"cards": cards, "stacks": len(rows)}

    def _merge_into(self, conn, source_id: int, target_id: int) -> int:
        """Move every stack from one collection to another, merging duplicates.

        Two stacks of the same printing/finish/condition/language/location
        cannot both exist in one collection — that is the stack key — so
        matching stacks are summed and the source row dropped.
        """
        rows = conn.execute(
            "SELECT item_id, quantity FROM collection_items WHERE collection_id = ?",
            (source_id,)).fetchall()
        for item_id, quantity in rows:
            self._move_stack(conn, item_id, target_id, quantity)
        return len(rows)

    def move_copies(self, item_id: int, to_collection_id: int, *,
                    quantity: int | None = None) -> dict:
        """Move copies of one stack into another collection.

        `quantity=None` moves the whole stack. Moving part of it splits the
        stack, which is what "put two of my four Sol Rings in the trade box"
        means. Ownership is untouched either way: the master collection has
        exactly the same cards before and after.
        """
        with self._connect() as conn:
            self._ensure_default_collection(conn)
            exists = conn.execute(
                "SELECT collection_id FROM collections WHERE collection_id = ?",
                (to_collection_id,)).fetchone()
            if not exists:
                return {"moved": 0, "reason": "No such collection"}
            row = conn.execute(
                "SELECT quantity, collection_id FROM collection_items WHERE item_id = ?",
                (item_id,)).fetchone()
            if not row:
                return {"moved": 0, "reason": "No such stack"}
            have, from_id = row
            if from_id == to_collection_id:
                return {"moved": 0, "reason": "Already in that collection"}
            take = have if quantity is None else max(0, min(int(quantity), have))
            if not take:
                return {"moved": 0, "reason": "Nothing to move"}
            self._move_stack(conn, item_id, to_collection_id, take)
            conn.commit()
            return {"moved": take, "from": from_id, "to": to_collection_id}

    def _move_stack(self, conn, item_id: int, to_collection_id: int,
                    quantity: int) -> None:
        row = conn.execute(
            """SELECT printing_id, oracle_id, card_name, finish, condition,
                      language, location, notes, quantity, unit_cost_usd,
                      acquisition_id, created_at
               FROM collection_items WHERE item_id = ?""", (item_id,)).fetchone()
        if not row:
            return
        (printing_id, oracle_id, card_name, fin, cond, language, location,
         notes, have, unit_cost, acquisition_id, created_at) = row
        now = _now()

        target = conn.execute(
            """SELECT item_id, quantity FROM collection_items
               WHERE printing_id = ? AND finish = ? AND condition = ?
                 AND language = ? AND location = ? AND collection_id = ?""",
            (printing_id, fin, cond, language, location, to_collection_id)
        ).fetchone()

        if target:
            conn.execute(
                "UPDATE collection_items SET quantity = ?, updated_at = ? "
                "WHERE item_id = ?", (target[1] + quantity, now, target[0]))
        elif quantity >= have:
            # The whole stack moves: relabel it rather than copying, so the
            # item_id and its history stay with the cards.
            conn.execute(
                "UPDATE collection_items SET collection_id = ?, updated_at = ? "
                "WHERE item_id = ?", (to_collection_id, now, item_id))
            return
        else:
            conn.execute(
                """INSERT INTO collection_items
                   (printing_id, oracle_id, card_name, finish, condition, language,
                    quantity, location, notes, acquired_at, unit_cost_usd,
                    acquisition_id, collection_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (printing_id, oracle_id, card_name, fin, cond, language, quantity,
                 location, notes, created_at, unit_cost, acquisition_id,
                 to_collection_id, now, now))

        remaining = have - quantity
        if remaining > 0:
            conn.execute(
                "UPDATE collection_items SET quantity = ?, updated_at = ? "
                "WHERE item_id = ?", (remaining, now, item_id))
        else:
            conn.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))

    def list_collections(self) -> list[dict]:
        """Every collection with what's in it, default first."""
        with self._connect() as conn:
            self._ensure_default_collection(conn)
            conn.commit()
            rows = conn.execute(
                """SELECT c.collection_id, c.name, c.kind, c.notes, c.is_default,
                          c.created_at, c.collection_uid,
                          COALESCE(SUM(i.quantity), 0) AS cards,
                          COUNT(DISTINCT i.printing_id) AS unique_printings
                   FROM collections c
                   LEFT JOIN collection_items i
                          ON i.collection_id = c.collection_id
                   GROUP BY c.collection_id
                   ORDER BY c.is_default DESC, c.name COLLATE NOCASE"""
            ).fetchall()
        return [{"collection_id": r[0], "name": r[1], "kind": r[2], "notes": r[3],
                 "is_default": bool(r[4]), "created_at": r[5],
                 "collection_uid": r[6], "cards": r[7],
                 "unique_printings": r[8]} for r in rows]

    def _collection_row(self, conn, collection_id: int) -> dict:
        r = conn.execute(
            """SELECT collection_id, name, kind, notes, is_default, created_at,
                      collection_uid
               FROM collections WHERE collection_id = ?""",
            (collection_id,)).fetchone()
        if not r:
            return {}
        return {"collection_id": r[0], "name": r[1], "kind": r[2], "notes": r[3],
                "is_default": bool(r[4]), "created_at": r[5],
                "collection_uid": r[6], "cards": 0, "unique_printings": 0}


    # --------------------------------------------------------- wishlist
    #
    # Cards you want but do not have. Kept apart from `collection_items` on
    # purpose — see the schema note. Nothing here can affect what you own,
    # what your collection is worth, or what a deck still needs.

    def wishlist_set(self, card_name: str, quantity: int, *,
                     deck_id: str = "", deck_name: str = "",
                     oracle_id: str = "", notes: str = "") -> dict:
        """Record that something is wanted. Quantity 0 removes the entry.

        An exact set rather than an increment: this is driven by "the deck is
        short three", which is a fact about the current decklist, not an event
        to accumulate. Adding would double the entry every time a deck was
        saved.
        """
        card_name = (card_name or "").strip()
        if not card_name:
            raise ValueError("A wishlist entry needs a card name")
        now = _now()
        with self._connect() as conn:
            if quantity <= 0:
                conn.execute(
                    """DELETE FROM wishlist_items
                       WHERE card_name = ? COLLATE NOCASE AND deck_id = ?""",
                    (card_name, deck_id))
                conn.commit()
                return {"card_name": card_name, "quantity": 0, "deck_id": deck_id}
            conn.execute(
                """INSERT INTO wishlist_items
                     (card_name, oracle_id, quantity, deck_id, deck_name,
                      notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(card_name COLLATE NOCASE, deck_id) DO UPDATE SET
                     quantity = excluded.quantity,
                     deck_name = excluded.deck_name,
                     notes = CASE WHEN excluded.notes != '' THEN excluded.notes
                                  ELSE wishlist_items.notes END,
                     updated_at = excluded.updated_at""",
                (card_name, oracle_id, int(quantity), deck_id, deck_name,
                 notes, now, now))
            conn.commit()
        return {"card_name": card_name, "quantity": int(quantity),
                "deck_id": deck_id}

    def wishlist_for_deck(self, deck_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT card_name, quantity, deck_name, notes
                   FROM wishlist_items WHERE deck_id = ?
                   ORDER BY card_name COLLATE NOCASE""", (deck_id,)).fetchall()
        return [{"card_name": r[0], "quantity": r[1], "deck_name": r[2],
                 "notes": r[3]} for r in rows]

    def wishlist(self) -> list[dict]:
        """Everything wanted, one row per card, with who wants it.

        Totals are the MAXIMUM any single deck needs, not the sum: two decks
        each wanting one Sol Ring need one Sol Ring between them unless both
        are built at once, and quoting two would send someone shopping for a
        card they do not need.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT card_name, MAX(quantity) AS most, SUM(quantity) AS across,
                          COUNT(*) AS decks, MAX(oracle_id) AS oracle_id
                   FROM wishlist_items
                   GROUP BY card_name COLLATE NOCASE
                   ORDER BY card_name COLLATE NOCASE""").fetchall()
            wanted = []
            for name, most, across, decks, oracle_id in rows:
                sources = conn.execute(
                    """SELECT deck_id, deck_name, quantity FROM wishlist_items
                       WHERE card_name = ? COLLATE NOCASE""", (name,)).fetchall()
                wanted.append({
                    "card_name": name,
                    "oracle_id": oracle_id or "",
                    # What one deck needs at once.
                    "quantity": most,
                    # What every deck would need if all were built together.
                    "quantity_across_decks": across,
                    "deck_count": decks,
                    "wanted_by": [{"deck_id": d, "deck_name": n, "quantity": q}
                                  for d, n, q in sources],
                })
        return wanted

    def wishlist_clear_deck(self, deck_id: str) -> int:
        """Forget what one deck wanted — used before writing its new shortfall."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM wishlist_items WHERE deck_id = ?",
                               (deck_id,))
            conn.commit()
            return cur.rowcount

    def wishlist_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT card_name COLLATE NOCASE) "
                "FROM wishlist_items").fetchone()
            return int(row[0])

    def remove_copies(self, printing_id: str, card_name: str, *, quantity: int = 1, **kwargs):
        """Convenience inverse of add_copies."""
        return self.add_copies(
            printing_id, card_name, quantity=-abs(quantity), reason="remove", **kwargs
        )

    def set_item_quantity(self, item_id: int, quantity: int) -> bool:
        """Force a stack to an exact count. Returns False if it doesn't exist."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT printing_id, card_name, quantity FROM collection_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                return False
            printing_id, card_name, current = row
            now = _now()
            if quantity <= 0:
                conn.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))
            else:
                conn.execute(
                    "UPDATE collection_items SET quantity = ?, updated_at = ? WHERE item_id = ?",
                    (quantity, now, item_id),
                )
            conn.execute(
                """INSERT INTO collection_events
                   (item_id, printing_id, card_name, delta, reason, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (item_id, printing_id, card_name, quantity - current, "set", "", now),
            )
            conn.commit()
        return True

    def update_item(self, item_id: int, **fields) -> bool:
        """Patch editable metadata on a stack (location, notes, condition...)."""
        editable = {"location", "notes", "condition", "finish", "language",
                    "acquired_at", "unit_cost_usd", "acquisition_id"}
        patch = {k: v for k, v in fields.items() if k in editable}
        if not patch:
            return False
        patch["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in patch)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE collection_items SET {sets} WHERE item_id = ?",
                (*patch.values(), item_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def delete_item(self, item_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM collection_items WHERE item_id = ?", (item_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear(self) -> int:
        """Wipe the collection. Returns rows removed."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM collection_items").fetchone()[0]
            conn.execute("DELETE FROM collection_items")
            conn.execute("DELETE FROM collection_events")
            conn.commit()
            return count

    # ------------------------------------------------------------ reads

    def get_item(self, item_id: int) -> CollectionItem | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_ITEM_COLUMNS)} FROM collection_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        return _row_to_item(row) if row else None

    def list_items(
        self,
        *,
        name_like: str | None = None,
        printing_id: str | None = None,
        oracle_id: str | None = None,
        finish: str | None = None,
        condition: str | None = None,
        location: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[CollectionItem], int]:
        """Filtered stacks plus the total matching count (for pagination)."""
        conditions: list[str] = ["quantity > 0"]
        params: list = []
        if name_like:
            conditions.append("card_name LIKE ? COLLATE NOCASE")
            params.append(f"%{name_like.strip()}%")
        if printing_id:
            conditions.append("printing_id = ?")
            params.append(printing_id)
        if oracle_id:
            conditions.append("oracle_id = ?")
            params.append(oracle_id)
        if finish:
            conditions.append("finish = ?")
            params.append(finish)
        if condition:
            conditions.append("condition = ?")
            params.append(condition)
        if location:
            conditions.append("location = ?")
            params.append(location)

        where = "WHERE " + " AND ".join(conditions)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM collection_items {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""SELECT {', '.join(_ITEM_COLUMNS)} FROM collection_items {where}
                    ORDER BY card_name COLLATE NOCASE, item_id
                    LIMIT ? OFFSET ?""",
                params + [int(limit), int(offset)],
            ).fetchall()
        return [_row_to_item(r) for r in rows], total

    def owned_by_oracle(self) -> dict[str, int]:
        """oracle_id -> total copies owned, across every printing and finish."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT oracle_id, SUM(quantity) FROM collection_items
                   WHERE quantity > 0 AND oracle_id != '' GROUP BY oracle_id"""
            ).fetchall()
        return {oid: int(qty) for oid, qty in rows}

    def owned_by_name(self) -> dict[str, int]:
        """Lowercased card name -> total copies owned.

        Name-keyed because that is what decks are made of — `DeckEntry` holds
        `card_name`, and the deck parser strips set codes outright. Ownership
        questions asked from a decklist have nothing but the name to join on.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT card_name, SUM(quantity) FROM collection_items
                   WHERE quantity > 0 GROUP BY card_name COLLATE NOCASE"""
            ).fetchall()
        out: dict[str, int] = {}
        for name, qty in rows:
            key = (name or "").lower()
            out[key] = out.get(key, 0) + int(qty)
        return out

    def owned_count(self, card_name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(quantity), 0) FROM collection_items
                   WHERE card_name = ? COLLATE NOCASE AND quantity > 0""",
                (card_name,),
            ).fetchone()
        return int(row[0]) if row else 0

    def locations(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT location FROM collection_items
                   WHERE quantity > 0 AND location != '' ORDER BY location"""
            ).fetchall()
        return [r[0] for r in rows]

    def recent_events(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT event_id, item_id, printing_id, card_name, delta,
                          reason, note, created_at
                   FROM collection_events ORDER BY event_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        keys = ("event_id", "item_id", "printing_id", "card_name",
                "delta", "reason", "note", "created_at")
        return [dict(zip(keys, r)) for r in rows]

    def summary(self) -> CollectionSummary:
        """Counts only — valuation is layered on in Phase 2.

        Kept price-free on purpose so the collection is fully usable before
        printings/prices have ever been downloaded.
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(quantity), 0),
                          COUNT(DISTINCT card_name COLLATE NOCASE),
                          COUNT(DISTINCT printing_id)
                   FROM collection_items WHERE quantity > 0"""
            ).fetchone()
            by_finish = dict(conn.execute(
                """SELECT finish, COALESCE(SUM(quantity), 0) FROM collection_items
                   WHERE quantity > 0 GROUP BY finish"""
            ).fetchall())
            by_condition = dict(conn.execute(
                """SELECT condition, COALESCE(SUM(quantity), 0) FROM collection_items
                   WHERE quantity > 0 GROUP BY condition"""
            ).fetchall())
        return CollectionSummary(
            total_cards=int(row[0]),
            unique_cards=int(row[1]),
            unique_printings=int(row[2]),
            by_finish={k: int(v) for k, v in by_finish.items()},
            by_condition={k: int(v) for k, v in by_condition.items()},
        )


def _row_to_item(row: tuple) -> CollectionItem:
    data = dict(zip(_ITEM_COLUMNS, row))
    return CollectionItem(**data)
