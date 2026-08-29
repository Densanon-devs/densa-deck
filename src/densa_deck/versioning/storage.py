"""SQLite storage for deck version snapshots.

Each saved deck gets a unique deck_id. Each save creates a new version
with a snapshot of the full decklist, analysis scores, and metadata.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from densa_deck.data.database import DEFAULT_DB_PATH

_VERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS decks (
    deck_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    format TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    saved_at TEXT NOT NULL,
    notes TEXT DEFAULT '',
    decklist_json TEXT NOT NULL,
    scores_json TEXT DEFAULT '{}',
    metrics_json TEXT DEFAULT '{}',
    FOREIGN KEY (deck_id) REFERENCES decks(deck_id)
);

CREATE INDEX IF NOT EXISTS idx_versions_deck ON deck_versions(deck_id, version_number);

-- Games played, and which version was on the table.
--
-- Deliberately NOT a foreign key to `deck_versions`. A record follows the
-- version in part and the DECK as a whole, and history is capped — so the
-- day a cap prunes v1..v9 is the day a FK would take nine versions' worth of
-- games with it and quietly restate a deck's lifetime record. Storing the
-- version NUMBER keeps every game attributable forever; the snapshot is what
-- expires, not the fact that you played it and won.
--
-- `version_number` 0 means a game logged against a deck with no saved
-- version yet, which is a real thing to do and must not be refused.
CREATE TABLE IF NOT EXISTS deck_games (
    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id TEXT NOT NULL,
    version_number INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL,
    opponent TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    played_at TEXT NOT NULL,
    -- An identity that means the same thing on both devices.
    --
    -- `game_id` is a local autoincrement and says nothing on the other
    -- phone: two devices logging a game offline each mint id 7, and a sync
    -- keyed on it would treat two different games as one. Same reason
    -- membership travels by natural key and collections by uid.
    game_uid TEXT
);

CREATE INDEX IF NOT EXISTS idx_games_deck ON deck_games(deck_id, version_number);

-- How much history to keep, per deck. A row only exists for a deck that was
-- given its own answer; everything else follows the default.
CREATE TABLE IF NOT EXISTS deck_history_caps (
    deck_id TEXT PRIMARY KEY,
    max_versions INTEGER NOT NULL
);

-- One row, id 1. The default cap and anything else that is a preference
-- rather than a fact about a deck.
CREATE TABLE IF NOT EXISTS versioning_settings (
    settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
    default_max_versions INTEGER NOT NULL
);
"""

# Keep this many versions per deck unless told otherwise.
#
# Not unlimited by default: a deck edited over a season accumulates hundreds
# of snapshots of a hundred cards each, and the history screen is the thing
# that gets slower. Not small either — the point of history is being able to
# look back at a deck two months ago, and a cap of five cannot do that.
DEFAULT_MAX_VERSIONS = 50

# What `max_versions` means when it says "keep everything".
UNCAPPED = 0

# The three outcomes a game can have. Draws are counted and are NOT half a
# win: a win rate that silently folds draws into losses tells you a deck is
# worse than it is, and one that folds them into wins tells you the opposite.
GAME_RESULTS = ("win", "loss", "draw")


@dataclass
class DeckSnapshot:
    """A saved snapshot of a deck at a point in time."""

    version_id: int = 0
    deck_id: str = ""
    # name/format live on the `decks` row, not on the version row, but every
    # consumer of a snapshot needs them: analyst compare, duel, and the CLI
    # deck loader all read snap.name / snap.format. Leaving them off made
    # those paths raise AttributeError, which @_safe swallowed into a generic
    # {ok: false} — so two shipped features failed for every user on every
    # call with no visible cause. Joined in by the getters below.
    name: str = ""
    format: str = ""
    version_number: int = 0
    saved_at: str = ""
    notes: str = ""
    decklist: dict[str, int] = field(default_factory=dict)  # card_name -> quantity
    zones: dict[str, list[str]] = field(default_factory=dict)  # zone -> [card_names]
    # Which exact printing a slot meant, for the slots that said.
    #
    # A SIDECAR, deliberately, rather than a change to `decklist`. Every
    # consumer of a snapshot — diff, trends, impact, the eleven combo-aware
    # layers, the analyst — reads `decklist` as {name: quantity} and is
    # correct to: legality, combos and goldfishing are facts about cards, not
    # about printings. Widening the name-keyed map would have rippled through
    # all of them to answer a question none of them asks.
    #
    # Empty for every deck that never named a printing, which is most of
    # them, and empty for every version saved before this existed.
    printings: list[dict] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class DeckDiff:
    """Difference between two deck versions."""

    deck_name: str = ""
    version_a: int = 0
    version_b: int = 0
    added: dict[str, int] = field(default_factory=dict)     # card_name -> qty added
    removed: dict[str, int] = field(default_factory=dict)    # card_name -> qty removed
    changed_qty: dict[str, tuple[int, int]] = field(default_factory=dict)  # card -> (old, new)
    total_added: int = 0
    total_removed: int = 0
    score_deltas: dict[str, float] = field(default_factory=dict)  # score_name -> delta
    metric_deltas: dict[str, float] = field(default_factory=dict)


class VersionStore:
    """SQLite-backed deck version storage."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            db_path = DEFAULT_DB_PATH.parent / "versions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Thread-local connections — the desktop app shares one VersionStore
        # across the dispatcher thread and any background worker threads,
        # and sqlite3.Connection is pinned to its creating thread by default.
        self._local = threading.local()
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            with self._schema_lock:
                if not self._schema_ready:
                    conn.executescript(_VERSION_SCHEMA)
                    self._migrate_game_uids(conn)
                    self._schema_ready = True
            self._local.conn = conn
        return conn

    def _migrate_game_uids(self, conn) -> None:
        """Give games already on disk an identity, and keep them unique.

        Rows logged before sync existed have no uid. They are local history
        and are perfectly real, so they get one minted here rather than being
        left unsyncable — a device that has been recording results for a
        month should not have to start again to sync them.

        The index is what makes applying a game idempotent: the same event
        arriving twice writes one row.
        """
        columns = {r[1] for r in conn.execute(
            "PRAGMA table_info(deck_games)").fetchall()}
        if "game_uid" not in columns:
            conn.execute("ALTER TABLE deck_games ADD COLUMN game_uid TEXT")
        conn.execute(
            """UPDATE deck_games
               SET game_uid = 'local-' || game_id
               WHERE game_uid IS NULL OR game_uid = ''""")
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_games_uid
               ON deck_games(game_uid)""")
        conn.commit()

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def save_version(
        self,
        deck_id: str,
        name: str,
        format: str | None,
        decklist: dict[str, int],
        zones: dict[str, list[str]],
        scores: dict[str, float] | None = None,
        metrics: dict[str, float] | None = None,
        notes: str = "",
        printings: list[dict] | None = None,
    ) -> DeckSnapshot:
        """Save a new version of a deck.

        `printings` is optional and additive: it records which exact card a
        slot meant for the slots that said, and changes nothing for the ones
        that did not. A version saved without it reads back with an empty
        list, so nothing that pre-dates it needs converting.
        """
        conn = self.connect()
        now = datetime.now().isoformat()

        # Ensure deck exists
        existing = conn.execute(
            "SELECT deck_id FROM decks WHERE deck_id = ?", (deck_id,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO decks (deck_id, name, format, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (deck_id, name, format, now, now),
            )
        else:
            conn.execute(
                "UPDATE decks SET name = ?, format = ?, updated_at = ? WHERE deck_id = ?",
                (name, format, now, deck_id),
            )

        # Get next version number
        row = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) FROM deck_versions WHERE deck_id = ?",
            (deck_id,),
        ).fetchone()
        version_number = row[0] + 1

        # Insert version
        conn.execute(
            """INSERT INTO deck_versions
               (deck_id, version_number, saved_at, notes, decklist_json, scores_json, metrics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                deck_id,
                version_number,
                now,
                notes,
                # A third key beside the two that were always there. Readers
                # take what they know and ignore the rest, so an older build
                # opening a newer version still gets the whole decklist.
                json.dumps({
                    "cards": decklist,
                    "zones": zones,
                    "printings": printings or [],
                }),
                json.dumps(scores or {}),
                json.dumps(metrics or {}),
            ),
        )
        conn.commit()

        version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        return DeckSnapshot(
            version_id=version_id,
            deck_id=deck_id,
            version_number=version_number,
            saved_at=now,
            notes=notes,
            decklist=decklist,
            zones=zones,
            printings=printings or [],
            scores=scores or {},
            metrics=metrics or {},
        )

    def get_version(self, deck_id: str, version_number: int) -> DeckSnapshot | None:
        """Load a specific version of a deck."""
        conn = self.connect()
        row = conn.execute(
            """SELECT v.version_id, v.version_number, v.saved_at, v.notes,
                      v.decklist_json, v.scores_json, v.metrics_json,
                      d.name, d.format
               FROM deck_versions v
               LEFT JOIN decks d ON d.deck_id = v.deck_id
               WHERE v.deck_id = ? AND v.version_number = ?""",
            (deck_id, version_number),
        ).fetchone()
        if not row:
            return None
        return _row_to_snapshot(deck_id, row)

    def get_latest(self, deck_id: str) -> DeckSnapshot | None:
        """Load the most recent version of a deck."""
        conn = self.connect()
        row = conn.execute(
            """SELECT v.version_id, v.version_number, v.saved_at, v.notes,
                      v.decklist_json, v.scores_json, v.metrics_json,
                      d.name, d.format
               FROM deck_versions v
               LEFT JOIN decks d ON d.deck_id = v.deck_id
               WHERE v.deck_id = ?
               ORDER BY v.version_number DESC LIMIT 1""",
            (deck_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_snapshot(deck_id, row)

    def get_all_versions(self, deck_id: str) -> list[DeckSnapshot]:
        """Load all versions of a deck."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT v.version_id, v.version_number, v.saved_at, v.notes,
                      v.decklist_json, v.scores_json, v.metrics_json,
                      d.name, d.format
               FROM deck_versions v
               LEFT JOIN decks d ON d.deck_id = v.deck_id
               WHERE v.deck_id = ?
               ORDER BY v.version_number ASC""",
            (deck_id,),
        ).fetchall()
        return [_row_to_snapshot(deck_id, r) for r in rows]

    def list_decks(self) -> list[dict]:
        """List all saved decks with their latest version info."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT d.deck_id, d.name, d.format, d.created_at, d.updated_at,
                      (SELECT COUNT(*) FROM deck_versions v WHERE v.deck_id = d.deck_id) as versions
               FROM decks d ORDER BY d.updated_at DESC""",
        ).fetchall()
        return [
            {
                "deck_id": r[0],
                "name": r[1],
                "format": r[2],
                "created_at": r[3],
                "updated_at": r[4],
                "versions": r[5],
            }
            for r in rows
        ]

    def delete_deck(self, deck_id: str):
        """Delete a deck, its versions, its games and its history setting.

        All four, because a deck id can be reused — someone deletes "Atraxa"
        and builds another — and a surviving game row would credit the new
        deck with the old one's record. Rows that outlive the thing they
        describe are the same bug wherever they appear.
        """
        conn = self.connect()
        conn.execute("DELETE FROM deck_versions WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM deck_games WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM deck_history_caps WHERE deck_id = ?", (deck_id,))
        conn.execute("DELETE FROM decks WHERE deck_id = ?", (deck_id,))
        conn.commit()

    # ------------------------------------------------------------ from a peer

    def deck_updated_at(self, deck_id: str) -> str:
        """When this device last changed the deck. "" if it has never seen it."""
        row = self.connect().execute(
            "SELECT updated_at FROM decks WHERE deck_id = ?",
            (deck_id,)).fetchone()
        return (row[0] or "") if row else ""

    def upsert_from_sync(self, *, deck_id: str, name: str, format_: str,
                         decklist: dict, updated_at: str, notes: str = "",
                         zones: dict | None = None,
                         printings: list | None = None) -> dict:
        """A deck as the other device has it.

        LAST WRITE WINS, and it has to actually check. A deck is a document —
        a half-merged decklist is worse than a lost edit — but "last" means
        the newer TIMESTAMP, not "whichever event happened to arrive second".
        Events can be replayed, delivered out of order, or handed over in a
        baseline long after they were made; applying them blindly would let a
        stale copy overwrite an edit made since, and the user would watch
        their afternoon's work revert on a pull.

        Older-or-equal is skipped rather than applied. Equal too: a deck that
        matches what is already here has nothing to add, and writing it would
        mint a version out of the sync itself.
        """
        incoming = (updated_at or "").strip()
        mine = self.deck_updated_at(deck_id)
        if mine and incoming and incoming <= mine:
            return {"applied": False, "reason": "ours is newer or the same",
                    "ours": mine, "theirs": incoming}

        snapshot, created = self.save_version_if_changed(
            deck_id=deck_id, name=name or "Untitled",
            format=format_ or None,
            decklist=decklist or {}, zones=zones or {},
            notes=notes, printings=printings or [])

        # The deck row carries THEIR timestamp, not now(). Stamping it with
        # the local clock would make every deck look freshly edited on the
        # device that received it, and the next comparison would go the wrong
        # way — the receiver would start winning every tie and pushing the
        # sender's own deck back at it.
        if incoming:
            conn = self.connect()
            conn.execute("UPDATE decks SET updated_at = ? WHERE deck_id = ?",
                         (incoming, deck_id))
            conn.commit()
        return {"applied": True, "created_version": created,
                "version_number": snapshot.version_number}

    def set_format(self, deck_id: str, format_: str) -> bool:
        """Change what rules this deck is judged by. False if no such deck.

        Its own method because changing the format is not an edit to the
        deck: the cards are untouched, so there is no version to mint and no
        diff worth showing. What changes is which size limit, copy limit and
        colour rule the same ninety-nine are measured against.
        """
        conn = self.connect()
        cursor = conn.execute(
            "UPDATE decks SET format = ?, updated_at = ? WHERE deck_id = ?",
            (str(format_ or ""), datetime.now().isoformat(), deck_id))
        conn.commit()
        return cursor.rowcount > 0

    def duplicate(self, deck_id: str, new_deck_id: str, new_name: str) -> dict:
        """Copy a deck's LATEST version into a new deck.

        The copy starts at v1 with no history and no games, which is the
        honest reading of "duplicate": you wanted these hundred cards as a
        starting point, not somebody else's record of playing them. The
        original is untouched.
        """
        latest = self.get_latest(deck_id)
        if latest is None:
            return {"copied": False, "reason": "That deck has no saved version."}
        if self.get_latest(new_deck_id) is not None:
            return {"copied": False, "reason": f"{new_deck_id!r} already exists."}

        snapshot = self.save_version(
            deck_id=new_deck_id, name=new_name or f"{latest.name} (copy)",
            format=latest.format or None,
            decklist=dict(latest.decklist), zones=dict(latest.zones),
            scores=dict(latest.scores), metrics=dict(latest.metrics),
            notes=f"Copied from {latest.name or deck_id}",
            printings=list(latest.printings))
        return {"copied": True, "deck_id": new_deck_id,
                "version_number": snapshot.version_number,
                "cards": sum(latest.decklist.values())}

    def decks_for_sync(self) -> list[dict]:
        """Every deck as its latest version — for a device starting fresh."""
        out = []
        for row in self.list_decks():
            deck_id = row.get("deck_id", "")
            latest = self.get_latest(deck_id)
            if latest is None:
                continue
            out.append({
                "deck_id": deck_id,
                "name": row.get("name", "") or latest.name,
                "format": row.get("format", "") or latest.format,
                "notes": latest.notes,
                "decklist": latest.decklist,
                "zones": latest.zones,
                "printings": latest.printings,
                "updated_at": row.get("updated_at", "") or latest.saved_at,
            })
        return out

    # ------------------------------------------------- versions worth saving

    def save_version_if_changed(
        self,
        deck_id: str,
        name: str,
        format: str | None,
        decklist: dict[str, int],
        zones: dict[str, list[str]],
        scores: dict[str, float] | None = None,
        metrics: dict[str, float] | None = None,
        notes: str = "",
        printings: list[dict] | None = None,
    ) -> tuple[DeckSnapshot, bool]:
        """Save a version, but only if this deck actually differs.

        Returns `(snapshot, created)`. When nothing changed the LATEST
        version comes back with `created=False` and no row is written.

        This is what makes automatic versioning bearable. Saving on every
        edit produces forty snapshots of an afternoon's tinkering and buries
        the three that meant something; saving only on a real difference
        means the history reads as the deck's actual development, which is
        the entire reason to keep one.

        "Differs" means the cards, the zones, or the printings — not just the
        card list. A commander moved to the maindeck and a swap to a
        different printing of the same card are both edits a save persists,
        and a version that ignored them would read back as something the user
        never had.

        Scores and metrics are deliberately NOT part of the comparison. They
        are derived from the decklist, so they cannot differ on their own —
        and they move when the card database is updated, which would
        otherwise mint a version for every deck the day after an ingest.
        """
        latest = self.get_latest(deck_id)
        if latest is not None and self._same_content(
                latest, decklist, zones, printings or []):
            # Same deck — but possibly a new note about it. Writing the note
            # onto the version already there is the only way to keep it: a
            # note is not a card, so it must not mint a version, and dropping
            # it would mean someone typed something and watched it vanish.
            if notes and notes != latest.notes:
                conn = self.connect()
                conn.execute(
                    """UPDATE deck_versions SET notes = ?
                       WHERE deck_id = ? AND version_number = ?""",
                    (notes, deck_id, latest.version_number))
                conn.commit()
                latest.notes = notes
            # The FORMAT is not a card either, and it has to stick.
            #
            # Deciding a pile of cards is Modern rather than Commander
            # changes what is legal about it and nothing about the deck, so
            # it must not mint a version — but it also must not be quietly
            # discarded, which is what happened when an unchanged deck
            # returned early: the dropdown moved and the deck went on being
            # judged by the old rules.
            if format and str(format) != (latest.format or ""):
                conn = self.connect()
                conn.execute("UPDATE decks SET format = ? WHERE deck_id = ?",
                             (str(format), deck_id))
                conn.commit()
                latest.format = str(format)
            return latest, False

        snapshot = self.save_version(
            deck_id, name, format, decklist, zones,
            scores=scores, metrics=metrics, notes=notes, printings=printings)
        self.prune_history(deck_id)
        return snapshot, True

    @staticmethod
    def _same_content(snapshot: DeckSnapshot, decklist: dict[str, int],
                      zones: dict[str, list[str]],
                      printings: list[dict]) -> bool:
        """Is this the same deck as that snapshot?

        Compared as normalised data rather than as text: dict ordering and
        the order cards arrive in a zone are artefacts of parsing, not
        decisions the user made, and treating them as differences would mint
        a version every time someone re-saved without touching anything.
        """
        def zone_key(z):
            return {k: sorted(v or []) for k, v in sorted((z or {}).items())}

        def printing_key(rows):
            # Only the fields that say WHICH card. A printing row carries
            # display extras that vary between the parser and a reload.
            #
            # `card_name` OR `name`: the desktop save path writes the former
            # and callers that build rows by hand tend to write the latter.
            # Reading only one would compare every row's name as "" and let a
            # genuine card swap past as "no change".
            return sorted(
                (str(r.get("card_name") or r.get("name") or "").strip().lower(),
                 str(r.get("printing_id", "")).strip().lower(),
                 str(r.get("set_code", "")).strip().lower(),
                 str(r.get("collector_number", "")).strip().lower())
                for r in (rows or []))

        return (
            {k: int(v) for k, v in (snapshot.decklist or {}).items()}
            == {k: int(v) for k, v in (decklist or {}).items()}
            and zone_key(snapshot.zones) == zone_key(zones)
            and printing_key(snapshot.printings) == printing_key(printings)
        )

    # ------------------------------------------------------- how much to keep

    def default_history_limit(self) -> int:
        """Versions kept per deck when a deck has no answer of its own."""
        conn = self.connect()
        row = conn.execute(
            "SELECT default_max_versions FROM versioning_settings WHERE settings_id = 1"
        ).fetchone()
        return int(row[0]) if row else DEFAULT_MAX_VERSIONS

    def set_default_history_limit(self, max_versions: int) -> int:
        """Set the default. 0 means keep everything."""
        value = max(0, int(max_versions))
        conn = self.connect()
        conn.execute(
            """INSERT INTO versioning_settings (settings_id, default_max_versions)
               VALUES (1, ?)
               ON CONFLICT(settings_id) DO UPDATE SET default_max_versions = ?""",
            (value, value))
        conn.commit()
        return value

    def history_limit(self, deck_id: str) -> int:
        """This deck's cap: its own if it has one, otherwise the default."""
        conn = self.connect()
        row = conn.execute(
            "SELECT max_versions FROM deck_history_caps WHERE deck_id = ?",
            (deck_id,)).fetchone()
        return int(row[0]) if row else self.default_history_limit()

    def set_history_limit(self, deck_id: str, max_versions: int | None) -> int:
        """Give one deck its own cap, or `None` to put it back on the default.

        Setting a cap does NOT prune on the spot. Pruning is destructive and
        belongs to the moment a version is added, where it is the direct
        consequence of an action the user just took — not to the moment they
        change a number in settings and watch history disappear behind them.
        """
        conn = self.connect()
        if max_versions is None:
            conn.execute("DELETE FROM deck_history_caps WHERE deck_id = ?",
                         (deck_id,))
            conn.commit()
            return self.default_history_limit()
        value = max(0, int(max_versions))
        conn.execute(
            """INSERT INTO deck_history_caps (deck_id, max_versions)
               VALUES (?, ?)
               ON CONFLICT(deck_id) DO UPDATE SET max_versions = ?""",
            (deck_id, value, value))
        conn.commit()
        return value

    def prune_history(self, deck_id: str) -> int:
        """Drop the oldest snapshots past the cap. Returns how many went.

        The GAMES are not touched, and that is the whole point of storing
        them against a version number rather than a version row: a deck's
        lifetime record has to survive its history being trimmed, or the cap
        silently rewrites how good the deck has been.
        """
        cap = self.history_limit(deck_id)
        if cap <= UNCAPPED:
            return 0
        conn = self.connect()
        rows = conn.execute(
            """SELECT version_number FROM deck_versions
               WHERE deck_id = ? ORDER BY version_number DESC""",
            (deck_id,)).fetchall()
        if len(rows) <= cap:
            return 0
        doomed = [r[0] for r in rows[cap:]]
        conn.executemany(
            "DELETE FROM deck_versions WHERE deck_id = ? AND version_number = ?",
            [(deck_id, n) for n in doomed])
        conn.commit()
        return len(doomed)

    # -------------------------------------------------------------- the record

    def record_game(self, deck_id: str, result: str, *,
                    version_number: int | None = None,
                    opponent: str = "", notes: str = "",
                    game_uid: str = "", played_at: str = "") -> dict:
        """Log one game. Returns the deck's record after it.

        `version_number` omitted means the version currently on top, which is
        what "I just played this deck" means. Passing one explicitly is for
        entering results after the fact.
        """
        outcome = (result or "").strip().lower()
        if outcome not in GAME_RESULTS:
            raise ValueError(
                f"A game is a {', '.join(GAME_RESULTS)} — not {result!r}.")

        if version_number is None:
            latest = self.get_latest(deck_id)
            version_number = latest.version_number if latest else 0

        conn = self.connect()
        # OR IGNORE, not OR REPLACE: the uid is the identity, so a game that
        # is already here is already here. Replacing would rewrite a row the
        # user may have since edited on this device for no gain.
        conn.execute(
            """INSERT OR IGNORE INTO deck_games
                   (deck_id, version_number, result, opponent, notes,
                    played_at, game_uid)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (deck_id, int(version_number), outcome, opponent, notes,
             played_at or datetime.now().isoformat(),
             game_uid or str(uuid.uuid4())))
        conn.commit()
        return self.deck_record(deck_id)

    def forget_game(self, game_id: int) -> bool:
        """Remove one logged game. Returns False if it was not there."""
        conn = self.connect()
        cursor = conn.execute("DELETE FROM deck_games WHERE game_id = ?",
                              (int(game_id),))
        conn.commit()
        return cursor.rowcount > 0

    def game_uid_of(self, game_id: int) -> str:
        """The travelling identity of a local row, for telling the peer."""
        row = self.connect().execute(
            "SELECT game_uid FROM deck_games WHERE game_id = ?",
            (int(game_id),)).fetchone()
        return (row[0] or "") if row else ""

    def forget_game_by_uid(self, game_uid: str) -> bool:
        """Remove a game the OTHER device took back."""
        if not (game_uid or "").strip():
            return False
        conn = self.connect()
        cursor = conn.execute("DELETE FROM deck_games WHERE game_uid = ?",
                              (game_uid.strip(),))
        conn.commit()
        return cursor.rowcount > 0

    def games_for_sync(self, deck_id: str = "") -> list[dict]:
        """Every game, with the identity that travels — for the baseline."""
        conn = self.connect()
        sql = ("SELECT deck_id, game_uid, version_number, result, opponent, "
               "notes, played_at FROM deck_games")
        params: list = []
        if deck_id:
            sql += " WHERE deck_id = ?"
            params.append(deck_id)
        return [
            {"deck_id": r[0], "game_uid": r[1], "version_number": r[2],
             "result": r[3], "opponent": r[4], "notes": r[5], "played_at": r[6]}
            for r in conn.execute(sql, params).fetchall()
        ]

    def deck_record(self, deck_id: str) -> dict:
        """Wins, losses, draws and win rate across every version ever."""
        conn = self.connect()
        rows = conn.execute(
            "SELECT result, COUNT(*) FROM deck_games WHERE deck_id = ? GROUP BY result",
            (deck_id,)).fetchall()
        return _record_from_counts({r[0]: r[1] for r in rows})

    def version_record(self, deck_id: str, version_number: int) -> dict:
        """The same, for the games played on one version of the deck."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT result, COUNT(*) FROM deck_games
               WHERE deck_id = ? AND version_number = ? GROUP BY result""",
            (deck_id, int(version_number))).fetchall()
        return _record_from_counts({r[0]: r[1] for r in rows})

    def records_by_version(self, deck_id: str) -> dict[int, dict]:
        """Every version that has games, and how it did.

        Includes version numbers whose snapshot has been pruned. That is not
        a leak — it is the honest answer to "how has this deck done", and
        hiding those games would make a capped history look like a better
        deck than an uncapped one.
        """
        conn = self.connect()
        rows = conn.execute(
            """SELECT version_number, result, COUNT(*) FROM deck_games
               WHERE deck_id = ? GROUP BY version_number, result""",
            (deck_id,)).fetchall()
        counts: dict[int, dict] = {}
        for version_number, result, count in rows:
            counts.setdefault(int(version_number), {})[result] = count
        return {v: _record_from_counts(c) for v, c in sorted(counts.items())}

    def games_for_deck(self, deck_id: str, limit: int = 200) -> list[dict]:
        """The individual games, newest first."""
        conn = self.connect()
        rows = conn.execute(
            """SELECT game_id, version_number, result, opponent, notes, played_at
               FROM deck_games WHERE deck_id = ?
               ORDER BY game_id DESC LIMIT ?""",
            (deck_id, int(limit))).fetchall()
        return [
            {"game_id": r[0], "version_number": r[1], "result": r[2],
             "opponent": r[3], "notes": r[4], "played_at": r[5]}
            for r in rows
        ]


def _record_from_counts(counts: dict) -> dict:
    """Turn {result: n} into a record, with the rate spelled out.

    `win_rate` counts draws in the denominator, because they were games. A
    deck that draws half its matches has not won half of them, and a rate
    that says so would be flattering rather than useful. `decisive_win_rate`
    is offered beside it for the people who think in wins-per-decision.
    """
    wins = int(counts.get("win", 0) or 0)
    losses = int(counts.get("loss", 0) or 0)
    draws = int(counts.get("draw", 0) or 0)
    played = wins + losses + draws
    decisive = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "games": played,
        "win_rate": round(wins / played, 4) if played else None,
        "decisive_win_rate": round(wins / decisive, 4) if decisive else None,
        # "3-1-1" reads faster than five fields and is how people say it.
        "record": f"{wins}-{losses}" + (f"-{draws}" if draws else ""),
    }


def diff_versions(a: DeckSnapshot, b: DeckSnapshot) -> DeckDiff:
    """Compute the difference between two deck snapshots."""
    d = DeckDiff(
        deck_name=a.deck_id,
        version_a=a.version_number,
        version_b=b.version_number,
    )

    all_cards = set(a.decklist.keys()) | set(b.decklist.keys())
    for card in all_cards:
        qty_a = a.decklist.get(card, 0)
        qty_b = b.decklist.get(card, 0)

        if qty_a == 0 and qty_b > 0:
            d.added[card] = qty_b
            d.total_added += qty_b
        elif qty_b == 0 and qty_a > 0:
            d.removed[card] = qty_a
            d.total_removed += qty_a
        elif qty_a != qty_b:
            d.changed_qty[card] = (qty_a, qty_b)
            if qty_b > qty_a:
                d.total_added += qty_b - qty_a
            else:
                d.total_removed += qty_a - qty_b

    # Score deltas
    all_scores = set(a.scores.keys()) | set(b.scores.keys())
    for key in all_scores:
        sa = a.scores.get(key, 0.0)
        sb = b.scores.get(key, 0.0)
        d.score_deltas[key] = round(sb - sa, 2)

    # Metric deltas
    all_metrics = set(a.metrics.keys()) | set(b.metrics.keys())
    for key in all_metrics:
        ma = a.metrics.get(key, 0.0)
        mb = b.metrics.get(key, 0.0)
        d.metric_deltas[key] = round(mb - ma, 2)

    return d


def _row_to_snapshot(deck_id: str, row: tuple) -> DeckSnapshot:
    dl = json.loads(row[4])
    # Rows from the joined getters carry name/format at 7/8. Tolerate shorter
    # rows so any other caller degrades to empty strings rather than blowing
    # up on an index — the old failure mode was invisible enough already.
    return DeckSnapshot(
        version_id=row[0],
        deck_id=deck_id,
        name=(row[7] if len(row) > 7 and row[7] else ""),
        format=(row[8] if len(row) > 8 and row[8] else ""),
        version_number=row[1],
        saved_at=row[2],
        notes=row[3],
        decklist=dl.get("cards", {}),
        zones=dl.get("zones", {}),
        # Absent in every version saved before printings existed, and absent
        # in every deck that never named one. Both mean the same thing: every
        # slot takes any printing.
        printings=dl.get("printings", []) or [],
        scores=json.loads(row[5]),
        metrics=json.loads(row[6]),
    )
