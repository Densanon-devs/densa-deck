"""SQLite storage layer for card data."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from densa_deck.models import Card, CardFace, CardLayout, CardTag, Color, Legality

DEFAULT_DB_PATH = Path.home() / ".densa-deck" / "cards.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    scryfall_id TEXT PRIMARY KEY,
    oracle_id TEXT NOT NULL,
    name TEXT NOT NULL,
    layout TEXT NOT NULL,
    cmc REAL DEFAULT 0,
    mana_cost TEXT DEFAULT '',
    type_line TEXT DEFAULT '',
    oracle_text TEXT DEFAULT '',
    colors TEXT DEFAULT '[]',
    color_identity TEXT DEFAULT '[]',
    produced_mana TEXT DEFAULT '[]',
    keywords TEXT DEFAULT '[]',
    legalities TEXT DEFAULT '{}',
    faces TEXT DEFAULT '[]',
    power TEXT,
    toughness TEXT,
    loyalty TEXT,
    rarity TEXT DEFAULT '',
    set_code TEXT DEFAULT '',
    price_usd REAL,
    data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_oracle_id ON cards(oracle_id);
CREATE INDEX IF NOT EXISTS idx_cards_name_lower ON cards(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- card_aliases maps non-canonical names (Scryfall flavor_name for
-- Universes Within reprints like Innistrad Crimson Vow's "Dracula, Blood
-- Immortal" -> "Falkenrath Forebear") to the Oracle card's canonical name.
-- Populated lazily by the resolver the first time a deck import hits an
-- unresolved card; once cached, future imports resolve instantly with
-- no network call.
CREATE TABLE IF NOT EXISTS card_aliases (
    alias_lower TEXT PRIMARY KEY,
    oracle_name TEXT NOT NULL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# Lightweight migrations for schemas that pre-date a column. Run idempotently
# on every connect — SQLite errors when ADD COLUMN hits an existing column
# and when CREATE INDEX hits an existing index; we swallow both cases.
# Order matters: ADD COLUMN must run before CREATE INDEX that references it.
_MIGRATIONS = [
    # price_usd added when Scryfall price integration shipped (phase 5)
    "ALTER TABLE cards ADD COLUMN price_usd REAL",
    "CREATE INDEX IF NOT EXISTS idx_cards_price ON cards(price_usd)",
]


def _apply_migrations(conn: sqlite3.Connection):
    """Apply idempotent schema migrations on every connect.

    ALTER TABLE ADD COLUMN and CREATE INDEX are both expected to fail with
    `OperationalError` when the target already exists — that's the happy path
    for migrations that have already run. We swallow *only* those expected
    "already exists" / "duplicate column" errors so that unrelated failures
    (locked database, permissions, corrupt schema) surface loudly instead of
    leaving the schema half-migrated with silent downstream SQL errors.
    """
    expected_fragments = ("duplicate column", "already exists")
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if not any(frag in msg for frag in expected_fragments):
                raise
    conn.commit()


class CardDatabase:
    """SQLite-backed card storage with fast name lookups."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # sqlite3 Connection objects aren't safe to share across threads.
        # The desktop app shares one CardDatabase between the pywebview
        # dispatcher thread and the background ingest thread, so hand each
        # thread its own connection. WAL mode (set below) lets concurrent
        # readers coexist with a single writer at the SQLite level.
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
                    _apply_migrations(conn)
                    self._schema_ready = True
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def get_metadata(self, key: str) -> str | None:
        conn = self.connect()
        row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str):
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()

    def card_count(self) -> int:
        conn = self.connect()
        row = conn.execute("SELECT COUNT(*) FROM cards").fetchone()
        return row[0] if row else 0

    def upsert_cards(self, cards: list[Card], batch_size: int = 5000):
        conn = self.connect()
        for i in range(0, len(cards), batch_size):
            batch = cards[i : i + batch_size]
            conn.executemany(
                """INSERT OR REPLACE INTO cards
                   (scryfall_id, oracle_id, name, layout, cmc, mana_cost,
                    type_line, oracle_text, colors, color_identity, produced_mana,
                    keywords, legalities, faces, power, toughness, loyalty,
                    rarity, set_code, price_usd, data_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [_card_to_row(c) for c in batch],
            )
            conn.commit()

    def lookup_by_name(self, name: str) -> Card | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT data_json FROM cards WHERE name = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        if row:
            return _card_from_json(row[0])
        # Try partial match for split/DFC names like "Fire // Ice"
        row = conn.execute(
            "SELECT data_json FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"{name} //%",),
        ).fetchone()
        if row:
            return _card_from_json(row[0])
        # Try as a face name
        row = conn.execute(
            "SELECT data_json FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT 1",
            (f"% // {name}",),
        ).fetchone()
        if row:
            return _card_from_json(row[0])
        return None

    def lookup_many(self, names: list[str]) -> dict[str, Card | None]:
        results: dict[str, Card | None] = {}
        for name in names:
            results[name] = self.lookup_by_name(name)
        return results

    def search(self, query: str, limit: int = 50) -> list[Card]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT data_json FROM cards WHERE name LIKE ? COLLATE NOCASE LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [_card_from_json(r[0]) for r in rows]

    def lookup_alias(self, name: str) -> Card | None:
        """Resolve a card via the card_aliases cache.

        Used by the deck resolver as a second pass after the canonical-name
        lookup misses. Populated by `add_alias` when the online Scryfall
        fallback finds a flavor-name -> oracle-name mapping.
        """
        conn = self.connect()
        row = conn.execute(
            "SELECT oracle_name FROM card_aliases WHERE alias_lower = ? LIMIT 1",
            (name.lower(),),
        ).fetchone()
        if not row:
            return None
        return self.lookup_by_name(row[0])

    def add_alias(self, alias: str, oracle_name: str) -> None:
        """Cache a flavor-name / alt-name -> oracle-name mapping so
        future lookups resolve locally without hitting Scryfall."""
        conn = self.connect()
        conn.execute(
            "INSERT OR REPLACE INTO card_aliases (alias_lower, oracle_name) VALUES (?, ?)",
            (alias.lower(), oracle_name),
        )
        conn.commit()

    def search_structured(
        self,
        *,
        name: str | None = None,
        colors: list[str] | None = None,
        color_match: str = "identity",
        cmc_min: float | None = None,
        cmc_max: float | None = None,
        types: list[str] | None = None,
        format_legal: str | None = None,
        rarity: str | None = None,
        max_price: float | None = None,
        set_code: str | None = None,
        limit: int = 60,
        offset: int = 0,
    ) -> tuple[list[Card], int]:
        """SQL-filtered card search powering the deckbuilder's left column.

        Parameters map to the JSON body the frontend POSTs:
          name          — substring, case-insensitive (SQL LIKE %name%).
          colors        — subset of {"W","U","B","R","G","C"}. "C" means
                          colorless (empty color_identity).
          color_match   — "identity" (card's color_identity is a subset
                          of `colors` — commander rule) or "any" (card
                          has at least one of the selected colors).
          cmc_min/max   — inclusive bounds.
          types         — any-of list of type-line substrings
                          (creature / instant / sorcery / artifact /
                          enchantment / planeswalker / land / battle).
          format_legal  — returns only cards whose legalities[format]
                          is "legal" (not "restricted" / "banned").
          rarity        — common / uncommon / rare / mythic.
          max_price     — USD ceiling. NULL price_usd rows are NOT
                          excluded (unknown price passes the filter).
          set_code      — 3-letter set code, exact match.
          limit, offset — pagination.

        Returns (cards, total_matching_count). total_count is computed
        against the SAME filter set so the frontend can paginate /
        "Load more" without re-querying the filter shape.

        Rationale: dedicated SQL path rather than post-filtering
        `search()` — over a 35k-row table, a LIKE + JSON-contains chain
        with proper indexes returns in tens of ms; a Python-side filter
        after loading all cards is seconds.
        """
        # The JSON `legalities` and `color_identity` columns don't have
        # dedicated indexes — SQLite's LIKE on small TEXT fields is fast
        # enough at 35k rows that optimising those is premature. name
        # LIKE uses idx_cards_name_lower, cmc BETWEEN uses a table scan,
        # price uses idx_cards_price.
        conditions: list[str] = []
        params: list = []

        if name and name.strip():
            conditions.append("name LIKE ? COLLATE NOCASE")
            params.append(f"%{name.strip()}%")

        if cmc_min is not None:
            conditions.append("cmc >= ?")
            params.append(float(cmc_min))
        if cmc_max is not None:
            conditions.append("cmc <= ?")
            params.append(float(cmc_max))

        if set_code and set_code.strip():
            conditions.append("set_code = ? COLLATE NOCASE")
            params.append(set_code.strip().lower())

        if rarity and rarity.strip():
            conditions.append("rarity = ? COLLATE NOCASE")
            params.append(rarity.strip().lower())

        if max_price is not None:
            # Cards with NULL price are kept — we treat "unknown" as
            # "don't exclude" rather than "expensive", so budget queries
            # still surface new cards Scryfall hasn't priced yet.
            conditions.append("(price_usd IS NULL OR price_usd <= ?)")
            params.append(float(max_price))

        # Types: each token matches the PRIMARY type portion of type_line
        # only — i.e. the substring before the em-dash subtype delimiter.
        # Without this guard, type_line LIKE '%land%' matches Mistform
        # Island ("Creature - Illusion Island") because "Island" appears
        # in the subtype. Scryfall always uses the unicode em-dash
        # U+2014 ' - ' to separate types from subtypes, so we anchor the
        # LIKE on either "tok " or "tok\n" / start-of-string and exclude
        # rows whose tok-occurrence is only after the em-dash.
        if types:
            type_fragments = []
            for t in types:
                tok = (t or "").strip().lower()
                if not tok:
                    continue
                # Substring to the left of " - "; on a row with no em-dash
                # the entire type_line is the primary types portion.
                # SUBSTR(type_line, 1, INSTR(type_line, " - ") - 1) when
                # INSTR returns 0 yields an empty string, breaking the
                # match — so we fall back to the full type_line in that
                # case via a CASE expression.
                type_fragments.append(
                    "(CASE WHEN INSTR(type_line, ' — ') > 0 "
                    "      THEN SUBSTR(type_line, 1, INSTR(type_line, ' — ') - 1) "
                    "      ELSE type_line END) LIKE ? COLLATE NOCASE"
                )
                params.append(f"%{tok}%")
            if type_fragments:
                conditions.append("(" + " OR ".join(type_fragments) + ")")

        # Format legality: legalities JSON literally contains `"commander":"legal"`.
        # This is a substring match on the serialized JSON column. Scryfall
        # statuses like "restricted" / "banned" are NOT matched so the
        # deckbuilder only surfaces playable cards for the picked format.
        if format_legal and format_legal.strip():
            fmt = format_legal.strip().lower()
            conditions.append("legalities LIKE ?")
            params.append(f'%"{fmt}": "legal"%')

        # Colors: this filter is the expensive one because color_identity
        # is a JSON array in SQLite. We split into two modes:
        #   - "identity": card.color_identity ⊆ selected — the commander
        #     rule. Implemented by requiring the card has ZERO colors
        #     NOT in the selected set.
        #   - "any": card has at least one of the selected colors.
        # Colorless ("C") is special: identity=[] means the card fits
        # any color-identity filter, and in "any" mode we OR in a
        # "empty array" check so the user can explicitly pull colorless
        # cards alongside W/U/B.
        if colors:
            selected = [c.strip().upper() for c in colors if c and c.strip()]
            colorful = [c for c in selected if c in {"W", "U", "B", "R", "G"}]
            wants_colorless = "C" in selected
            if color_match == "any":
                any_parts = []
                for c in colorful:
                    any_parts.append('color_identity LIKE ?')
                    params.append(f'%"{c}"%')
                if wants_colorless:
                    any_parts.append("color_identity = '[]'")
                if any_parts:
                    conditions.append("(" + " OR ".join(any_parts) + ")")
            else:
                # identity mode: allow only cards whose color identity is a
                # subset of `selected`. We express this by requiring the card
                # NOT contain any color NOT in the selection.
                excluded = [c for c in ("W", "U", "B", "R", "G") if c not in colorful]
                for c in excluded:
                    conditions.append('color_identity NOT LIKE ?')
                    params.append(f'%"{c}"%')

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        conn = self.connect()

        # Total count — runs against the same filter set so pagination
        # "page X of Y" and the "Load more" button's enabled/disabled
        # state are coherent.
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM cards {where_clause}",
            params,
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        # Results — sorted by name so pagination is stable and the user
        # sees the same ordering on repeat queries.
        page_rows = conn.execute(
            f"""SELECT data_json FROM cards {where_clause}
                ORDER BY name COLLATE NOCASE
                LIMIT ? OFFSET ?""",
            params + [int(limit), int(offset)],
        ).fetchall()
        cards = [_card_from_json(r[0]) for r in page_rows]
        return cards, total

    def snapshot_oracle_identities(self) -> dict[str, str]:
        """Return a {oracle_id: identity_hash} map of the current card set.

        The identity_hash concatenates oracle-level fields that could
        change when Scryfall reissues the bulk (name, oracle_text, type,
        legalities, mana_cost). Two snapshots taken around an ingest can
        be diffed to produce added / removed / updated card lists for
        the "what changed since your last sync" modal.

        Cheap: returns ~35k rows (one per oracle card), each row is two
        short strings. Memory footprint ~3-4 MB peak, done twice per
        update ingest.
        """
        conn = self.connect()
        rows = conn.execute(
            "SELECT oracle_id, name, oracle_text, type_line, legalities, mana_cost FROM cards"
        ).fetchall()
        out: dict[str, str] = {}
        for oid, name, text, tl, legs, cost in rows:
            # Cheap hash: newline-joined; we only compare equality, not
            # semantic content, so collision risk is a non-issue.
            out[oid] = f"{name}\n{text or ''}\n{tl or ''}\n{legs or ''}\n{cost or ''}"
        return out


def _card_to_row(card: Card) -> tuple:
    data = card.model_dump(mode="json")
    # Price sourced from the `prices.usd` field if present on the Card object
    # (set by the Scryfall ingest). Falls back to None — the DB column is
    # nullable and the filter treats NULL as "unknown price" (not excluded).
    price_usd = getattr(card, "price_usd", None)
    return (
        card.scryfall_id,
        card.oracle_id,
        card.name,
        card.layout.value,
        card.cmc,
        card.mana_cost,
        card.type_line,
        card.oracle_text,
        json.dumps([c.value for c in card.colors]),
        json.dumps([c.value for c in card.color_identity]),
        json.dumps(card.produced_mana),
        json.dumps(card.keywords),
        json.dumps({k: v.value for k, v in card.legalities.items()}),
        json.dumps([f.model_dump(mode="json") for f in card.faces]),
        card.power,
        card.toughness,
        card.loyalty,
        card.rarity,
        card.set_code,
        price_usd,
        json.dumps(data),
    )


def _card_from_json(data_json: str) -> Card:
    data = json.loads(data_json)
    # Reconstruct enums
    data["layout"] = CardLayout(data["layout"])
    data["colors"] = [Color(c) for c in data.get("colors", [])]
    data["color_identity"] = [Color(c) for c in data.get("color_identity", [])]
    data["legalities"] = {k: Legality(v) for k, v in data.get("legalities", {}).items()}
    data["tags"] = [CardTag(t) for t in data.get("tags", [])]
    faces = []
    for f in data.get("faces", []):
        f["colors"] = [Color(c) for c in f.get("colors", [])]
        f["color_indicator"] = [Color(c) for c in f.get("color_indicator", [])]
        faces.append(CardFace(**f))
    data["faces"] = faces
    return Card(**data)
