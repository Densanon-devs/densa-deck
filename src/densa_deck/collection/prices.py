"""Price sourcing and collection valuation.

Two things live here:

**The provider seam.** Every price read goes through `PriceProvider`.
Scryfall's bulk feed is the only implementation today and it is the right
default — it ships with the printing catalogue, costs nothing, and covers
~80% of paper printings. But Scryfall's own bulk documentation is explicit
that it is not fit for trading:

    prices should be considered dangerously stale after 24 hours. Only use
    bulk price information to track trends or provide a general estimate of
    card value. Prices are not updated frequently enough to power a
    storefront or sales system.

Collection valuation is squarely "a general estimate of card value". The
reseller phases are not, so the seam exists from the start: a real market
feed can be dropped in behind `PriceProvider` without touching a single call
site. `card_printings.tcgplayer_id` is already captured for exactly that.

**Valuation.** Collection totals join owned stacks against printing prices.
The two live in different SQLite files, so the join runs over an ATTACH
rather than by pulling every printing into Python — a 20,000-card collection
would otherwise mean a very large IN clause and a lot of round trips.

Three rules hold everywhere in this module:

  * NULL price means *unknown*, never *free*. Unpriced cards are counted and
    reported separately, never summed as zero.
  * Foil is valued as foil. The `cards.price_usd` column collapses
    usd/usd_foil/usd_etched through a fallback chain; here they stay apart.
  * Condition discounts the estimate. A damaged card is not worth NM money.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

from densa_deck.collection.models import CONDITION_MULTIPLIERS, Condition

# Scryfall regenerates bulk prices once a day; past this they call the data
# "dangerously stale". We cannot make it fresher, but we can refuse to
# present an old number as though it were current.
STALE_AFTER_HOURS = 24.0

PRICE_ATTRIBUTION = (
    "Price estimates via Scryfall bulk data. Prices update once daily and are "
    "an estimate of card value, not a trading quote."
)

# finish -> the card_printings column holding that finish's price.
_FINISH_PRICE_COLUMN = {
    "nonfoil": "price_usd",
    "foil": "price_usd_foil",
    "etched": "price_usd_etched",
}


class PriceProvider(Protocol):
    """Where prices come from.

    Implementations must return None for "unknown" rather than 0.0, and must
    report their own freshness so the UI can disclose it.
    """

    name: str

    def price_for(self, printing: dict, finish: str) -> float | None:
        """Unit price for one printing in one finish, or None if unknown."""
        ...

    def synced_at(self) -> str:
        """ISO timestamp of the price data, or '' when never synced."""
        ...


class ScryfallBulkProvider:
    """Prices as shipped in the `default_cards` bulk file.

    Free, offline after download, and refreshed by re-running the printings
    ingest. Covers roughly 80% of paper printings for non-foil and 55% for
    foil — the rest genuinely have no market price, which is a fact to
    surface rather than paper over.
    """

    name = "scryfall"

    def __init__(self, card_db):
        self._db = card_db

    def price_for(self, printing: dict, finish: str) -> float | None:
        if not printing:
            return None
        return printing.get(_FINISH_PRICE_COLUMN.get(finish, "price_usd"))

    def synced_at(self) -> str:
        from densa_deck.data.printings import META_SYNCED_AT
        return self._db.get_metadata(META_SYNCED_AT) or ""


def price_age_hours(iso_ts: str) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - then).total_seconds() / 3600.0


def is_stale(iso_ts: str) -> bool:
    age = price_age_hours(iso_ts)
    return age is None or age > STALE_AFTER_HOURS


def _condition_case_sql(column: str) -> str:
    """SQL CASE applying the condition multiplier to a price column."""
    whens = " ".join(
        f"WHEN '{c.value}' THEN {column} * {CONDITION_MULTIPLIERS[c]}"
        for c in Condition
    )
    return f"(CASE ci.condition {whens} ELSE {column} END)"


def _finish_price_sql() -> str:
    """SQL picking the price column matching each stack's finish."""
    return (
        "CASE ci.finish "
        "WHEN 'foil' THEN p.price_usd_foil "
        "WHEN 'etched' THEN p.price_usd_etched "
        "ELSE p.price_usd END"
    )


def _attached(store, card_db):
    """Connection to collection.db with cards.db attached as `cards`.

    Read-only joins across the two files. Kept here rather than on
    CollectionStore so the store itself stays ignorant of card data — the
    collection must remain usable with no catalogue at all.

    `card_db.connect()` first, deliberately: ATTACH on a path that doesn't
    exist yet happily creates an empty database with no tables, and every
    subsequent join then fails with "no such table". Connecting runs the
    CREATE TABLE IF NOT EXISTS schema, which both fixes first-run and doubles
    as the upgrade path for installs predating card_printings.
    """
    card_db.connect()
    conn = sqlite3.connect(store.db_path)
    conn.execute("ATTACH DATABASE ? AS cards", (str(card_db.db_path),))
    return conn


def value_collection(store, card_db, *, condition_adjusted: bool = True) -> dict:
    """Total estimated value of everything owned.

    `unpriced_*` counts are first-class output, not an afterthought: 8.6% of
    paper printings carry no price at all, and on a large collection that is
    a real hole in the number. Reporting a total without saying how much of
    it is unknown would be the single most misleading thing this could do.
    """
    unit = _finish_price_sql()
    priced_unit = _condition_case_sql(f"({unit})") if condition_adjusted else f"({unit})"

    conn = _attached(store, card_db)
    try:
        row = conn.execute(
            f"""
            SELECT
                COALESCE(SUM(CASE WHEN ({unit}) IS NOT NULL
                                  THEN {priced_unit} * ci.quantity END), 0),
                COALESCE(SUM(CASE WHEN ({unit}) IS NULL THEN ci.quantity END), 0),
                COUNT(CASE WHEN ({unit}) IS NULL THEN 1 END),
                COALESCE(SUM(ci.quantity), 0),
                COUNT(*)
            FROM collection_items ci
            LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0
            """
        ).fetchone()

        top = conn.execute(
            f"""
            SELECT ci.card_name, ci.finish, ci.condition, ci.quantity,
                   p.set_code, p.collector_number, {priced_unit} AS unit_value
            FROM collection_items ci
            LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0 AND ({unit}) IS NOT NULL
            ORDER BY unit_value * ci.quantity DESC
            LIMIT 10
            """
        ).fetchall()
    finally:
        conn.close()

    total, unpriced_copies, unpriced_stacks, total_copies, total_stacks = row
    provider = ScryfallBulkProvider(card_db)
    synced = provider.synced_at()

    return {
        "total_value_usd": round(total or 0.0, 2),
        "total_copies": int(total_copies),
        "total_stacks": int(total_stacks),
        "unpriced_copies": int(unpriced_copies),
        "unpriced_stacks": int(unpriced_stacks),
        "condition_adjusted": condition_adjusted,
        "price_source": provider.name,
        "prices_synced_at": synced,
        "price_age_hours": price_age_hours(synced),
        "prices_stale": is_stale(synced),
        "attribution": PRICE_ATTRIBUTION,
        "most_valuable": [
            {
                "card_name": r[0], "finish": r[1], "condition": r[2],
                "quantity": r[3],
                "set_code": r[4] or "", "collector_number": r[5] or "",
                "unit_value_usd": round(r[6], 2) if r[6] is not None else None,
                "stack_value_usd": round((r[6] or 0) * r[3], 2),
            }
            for r in top
        ],
    }


def capture_price_snapshot(store, card_db, *, on_date: str | None = None) -> int:
    """Record today's prices for owned printings. Returns rows written.

    Owned printings only — snapshotting all 107,353 daily would be ~39M rows
    a year to chart a few hundred cards nobody asked about. This is also the
    only data in the system that genuinely cannot be rebuilt: Scryfall serves
    today's prices and nothing else, so a day not captured is a day gone.

    Idempotent per day. Re-running replaces the day's rows rather than
    doubling them, so a user who opens the app five times gets one snapshot.
    """
    on_date = on_date or date.today().isoformat()
    conn = _attached(store, card_db)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                printing_id TEXT NOT NULL,
                captured_on TEXT NOT NULL,
                finish TEXT NOT NULL,
                price_usd REAL,
                source TEXT NOT NULL DEFAULT 'scryfall',
                PRIMARY KEY (printing_id, captured_on, finish)
            )
            """
        )
        cur = conn.execute(
            f"""
            INSERT OR REPLACE INTO price_history
                (printing_id, captured_on, finish, price_usd, source)
            SELECT DISTINCT ci.printing_id, ?, ci.finish, ({_finish_price_sql()}), 'scryfall'
            FROM collection_items ci
            JOIN cards.card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0 AND ({_finish_price_sql()}) IS NOT NULL
            """,
            (on_date,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def value_deltas(store, card_db, *, windows=(1, 7, 30)) -> dict:
    """Change in value over time, holding today's holdings fixed.

    Deliberately values *current* holdings at *past* prices rather than
    comparing two historical totals. Otherwise buying a card would show up as
    the collection "going up", which tells you nothing about the market. This
    isolates price movement from acquisition.

    A window with no snapshot returns None, not 0.0 — "we weren't tracking
    yet" and "nothing moved" are different answers.
    """
    unit = _finish_price_sql()
    priced_unit = _condition_case_sql(f"({unit})")
    conn = _attached(store, card_db)
    try:
        has_history = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_history'"
        ).fetchone()
        if not has_history:
            return {"available": False, "deltas": {}}

        current = conn.execute(
            f"""
            SELECT COALESCE(SUM({priced_unit} * ci.quantity), 0)
            FROM collection_items ci
            LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0 AND ({unit}) IS NOT NULL
            """
        ).fetchone()[0]

        out: dict[str, dict | None] = {}
        for days in windows:
            target = (date.today() - timedelta(days=days)).isoformat()
            # Nearest snapshot at or before the target date, per printing +
            # finish — prices are not captured every single day.
            hist_unit = _condition_case_sql("h.price_usd")
            row = conn.execute(
                f"""
                SELECT COALESCE(SUM({hist_unit} * ci.quantity), 0), COUNT(*)
                FROM collection_items ci
                JOIN price_history h
                  ON h.printing_id = ci.printing_id
                 AND h.finish = ci.finish
                 AND h.captured_on = (
                        SELECT MAX(h2.captured_on) FROM price_history h2
                        WHERE h2.printing_id = ci.printing_id
                          AND h2.finish = ci.finish
                          AND h2.captured_on <= ?
                     )
                WHERE ci.quantity > 0
                """,
                (target,),
            ).fetchone()
            past, matched = row[0], row[1]
            if not matched:
                out[f"{days}d"] = None
                continue
            out[f"{days}d"] = {
                "then_usd": round(past, 2),
                "now_usd": round(current, 2),
                "delta_usd": round(current - past, 2),
                "pct": round(((current - past) / past * 100), 2) if past else None,
                "cards_matched": matched,
            }
        return {"available": True, "deltas": out}
    finally:
        conn.close()


def price_history_for_printing(store, printing_id: str, finish: str = "nonfoil",
                               limit: int = 365) -> list[dict]:
    """Captured price points for one printing, oldest first."""
    conn = sqlite3.connect(store.db_path)
    try:
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='price_history'"
        ).fetchone()
        if not has:
            return []
        rows = conn.execute(
            """SELECT captured_on, price_usd FROM price_history
               WHERE printing_id = ? AND finish = ?
               ORDER BY captured_on DESC LIMIT ?""",
            (printing_id, finish, limit),
        ).fetchall()
    finally:
        conn.close()
    return [{"captured_on": r[0], "price_usd": r[1]} for r in reversed(rows)]
