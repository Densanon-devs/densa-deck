"""Binding specific physical copies to specific decks.

Ownership maths defaults to the oracle level (see `ownership.py`) because a
deck slot says "Sol Ring" and nothing more — the parser strips set codes, and
`DeckSnapshot.decklist` is `{name: quantity}`. That default answers every
question people actually ask: do I own it, how many are spare, what am I
missing.

This module is the opt-in refinement for the person who cares *which* copy is
sleeved where — the foil in the Commander deck, the beat-up one in the budget
list. Two rules keep it honest:

**Allocation never invents copies.** You cannot allocate more than you own,
and allocating a copy to a second deck requires freeing it from the first.
The whole point is to stop double-counting cardboard.

**Allocation is advisory, not authoritative.** A deck with no allocations
behaves exactly as before. Removing allocations never changes what a deck
contains — it only changes which physical object is earmarked for it.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def allocate(store, deck_id: str, item_id: int, *, quantity: int = 1,
             zone: str = "mainboard") -> dict:
    """Earmark copies from one stack for one deck.

    Refuses to over-allocate: the sum across all decks can never exceed the
    stack's quantity. Silently allowing it would recreate exactly the
    double-counting this feature exists to prevent.
    """
    quantity = int(quantity)
    if quantity <= 0:
        raise ValueError("quantity must be positive")

    with store._connect() as conn:
        row = conn.execute(
            "SELECT card_name, quantity FROM collection_items WHERE item_id = ?",
            (int(item_id),),
        ).fetchone()
        if not row:
            raise ValueError(f"no collection item {item_id}")
        card_name, owned = row

        already = conn.execute(
            """SELECT COALESCE(SUM(quantity), 0) FROM deck_allocations
               WHERE item_id = ? AND NOT (deck_id = ? AND zone = ?)""",
            (int(item_id), deck_id, zone),
        ).fetchone()[0]

        if already + quantity > owned:
            free = max(0, owned - already)
            raise ValueError(
                f"Only {free} of your {owned} '{card_name}' are unallocated; "
                f"free a copy from another deck first."
            )

        conn.execute(
            """INSERT INTO deck_allocations
                   (deck_id, item_id, card_name, zone, quantity, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(deck_id, item_id, zone)
               DO UPDATE SET quantity = excluded.quantity""",
            (deck_id, int(item_id), card_name, zone, quantity, _now()),
        )
        conn.commit()

    return {"deck_id": deck_id, "item_id": int(item_id), "card_name": card_name,
            "zone": zone, "quantity": quantity}


def deallocate(store, deck_id: str, item_id: int, zone: str = "mainboard") -> bool:
    with store._connect() as conn:
        cur = conn.execute(
            "DELETE FROM deck_allocations WHERE deck_id = ? AND item_id = ? AND zone = ?",
            (deck_id, int(item_id), zone),
        )
        conn.commit()
        return cur.rowcount > 0


def clear_deck_allocations(store, deck_id: str) -> int:
    """Free every copy earmarked for a deck — used when a deck is deleted."""
    with store._connect() as conn:
        cur = conn.execute("DELETE FROM deck_allocations WHERE deck_id = ?", (deck_id,))
        conn.commit()
        return cur.rowcount


def allocations_for_deck(store, card_db, deck_id: str) -> list[dict]:
    """Which physical copies are earmarked for this deck, with set + price."""
    from densa_deck.collection.prices import _attached, _finish_price_sql

    conn = _attached(store, card_db)
    try:
        rows = conn.execute(
            f"""SELECT a.allocation_id, a.item_id, a.card_name, a.zone, a.quantity,
                       ci.finish, ci.condition, ci.location,
                       p.set_code, p.set_name, p.collector_number,
                       ({_finish_price_sql()}) AS unit_price
                FROM deck_allocations a
                JOIN collection_items ci ON ci.item_id = a.item_id
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                WHERE a.deck_id = ?
                ORDER BY a.card_name COLLATE NOCASE""",
            (deck_id,),
        ).fetchall()
    finally:
        conn.close()

    keys = ("allocation_id", "item_id", "card_name", "zone", "quantity",
            "finish", "condition", "location", "set_code", "set_name",
            "collector_number", "unit_price_usd")
    return [dict(zip(keys, r)) for r in rows]


def allocation_map(store) -> dict[str, int]:
    """item_id -> copies currently earmarked across all decks."""
    with store._connect() as conn:
        rows = conn.execute(
            """SELECT item_id, COALESCE(SUM(quantity), 0) FROM deck_allocations
               GROUP BY item_id"""
        ).fetchall()
    return {str(item_id): int(qty) for item_id, qty in rows}


def unallocated_copies(store, item_id: int) -> int:
    """Copies of one stack not earmarked for any deck."""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT quantity FROM collection_items WHERE item_id = ?", (int(item_id),)
        ).fetchone()
        if not row:
            return 0
        allocated = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM deck_allocations WHERE item_id = ?",
            (int(item_id),),
        ).fetchone()[0]
    return max(0, int(row[0]) - int(allocated))


def reconcile(store) -> dict:
    """Drop allocations that no longer make sense.

    Stacks shrink (cards sold, traded, downgraded) and decks get deleted, and
    either can strand an allocation pointing at copies that no longer exist.
    Left alone those would understate availability forever — the card would
    look permanently spoken for.

    Returns what it removed and trimmed so the caller can tell the user
    rather than silently rewriting their data.
    """
    removed_missing = 0
    trimmed = []
    with store._connect() as conn:
        cur = conn.execute(
            """DELETE FROM deck_allocations
               WHERE item_id NOT IN (SELECT item_id FROM collection_items)"""
        )
        removed_missing = cur.rowcount

        rows = conn.execute(
            """SELECT a.allocation_id, a.card_name, a.quantity, ci.quantity
               FROM deck_allocations a
               JOIN collection_items ci ON ci.item_id = a.item_id"""
        ).fetchall()
        for alloc_id, card_name, alloc_qty, owned in rows:
            if alloc_qty > owned:
                conn.execute(
                    "UPDATE deck_allocations SET quantity = ? WHERE allocation_id = ?",
                    (owned, alloc_id),
                )
                trimmed.append({"card_name": card_name,
                                "was": alloc_qty, "now": owned})
        conn.execute("DELETE FROM deck_allocations WHERE quantity <= 0")
        conn.commit()

    return {"removed": removed_missing, "trimmed": trimmed}
