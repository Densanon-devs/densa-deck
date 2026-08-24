"""Price-aware collection browsing.

`CollectionStore.list_items` deliberately knows nothing about prices — the
collection has to work with no printing catalogue at all. But once the
catalogue is installed, the questions people actually ask are price-shaped:
"show me everything I own worth more than $10", "what are my most valuable
cards", "sort by what this stack is worth".

Those need a join across two SQLite files, so they live here rather than on
the store, and reuse the same ATTACH the valuation module uses.

Falls back cleanly: with no catalogue every price is NULL, price filters
match nothing, and price sorts degrade to name order.
"""

from __future__ import annotations

from densa_deck.collection.models import CollectionItem, Condition, Finish
from densa_deck.collection.prices import (
    _attached,
    _condition_case_sql,
    _finish_price_sql,
)

# Sort keys the UI offers. Mapped here rather than accepting raw SQL from the
# caller — this string is interpolated into a query.
SORT_OPTIONS = {
    "name": "ci.card_name COLLATE NOCASE ASC",
    "value_desc": "stack_value DESC NULLS LAST, ci.card_name COLLATE NOCASE",
    "value_asc": "stack_value ASC NULLS LAST, ci.card_name COLLATE NOCASE",
    "unit_desc": "unit_value DESC NULLS LAST, ci.card_name COLLATE NOCASE",
    "unit_asc": "unit_value ASC NULLS LAST, ci.card_name COLLATE NOCASE",
    "quantity_desc": "ci.quantity DESC, ci.card_name COLLATE NOCASE",
    "newest": "ci.created_at DESC, ci.item_id DESC",
    "set": "p.set_code ASC, CAST(p.collector_number AS INTEGER) ASC",
}

_ITEM_FIELDS = (
    "item_id", "printing_id", "oracle_id", "card_name", "finish", "condition",
    "language", "quantity", "location", "notes", "acquired_at",
    "unit_cost_usd", "acquisition_id", "created_at", "updated_at",
)


def search_collection(
    store,
    card_db,
    *,
    name_like: str | None = None,
    finish: str | None = None,
    condition: str | None = None,
    location: str | None = None,
    set_code: str | None = None,
    rarity: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    unpriced_only: bool = False,
    sort: str = "name",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[CollectionItem], int, dict]:
    """Filtered, sorted, price-annotated collection page.

    Returns (items, total_matching, page_totals).

    Price bounds intentionally EXCLUDE unpriced stacks. Elsewhere the rule is
    "NULL price never excludes" — that is right for card search, where an
    unpriced card might still be the one you want. Here the user has asked a
    question about money ("worth over $10"), and an unknown price is not an
    answer to it. `unpriced_only=True` is the way to go find them.
    """
    unit = _finish_price_sql()
    adj_unit = _condition_case_sql(f"({unit})")

    conditions = ["ci.quantity > 0"]
    params: list = []

    if name_like:
        conditions.append("ci.card_name LIKE ? COLLATE NOCASE")
        params.append(f"%{name_like.strip()}%")
    if finish:
        conditions.append("ci.finish = ?")
        params.append(Finish(finish).value)
    if condition:
        conditions.append("ci.condition = ?")
        params.append(Condition(condition).value)
    if location:
        conditions.append("ci.location = ?")
        params.append(location)
    if set_code:
        conditions.append("p.set_code = ? COLLATE NOCASE")
        params.append(set_code.strip().lower())
    if rarity:
        conditions.append("p.rarity = ? COLLATE NOCASE")
        params.append(rarity.strip().lower())
    if unpriced_only:
        conditions.append(f"({unit}) IS NULL")
    else:
        if min_price is not None:
            conditions.append(f"{adj_unit} >= ?")
            params.append(float(min_price))
        if max_price is not None:
            conditions.append(f"{adj_unit} <= ?")
            params.append(float(max_price))

    where = "WHERE " + " AND ".join(conditions)
    order = SORT_OPTIONS.get(sort, SORT_OPTIONS["name"])
    cols = ", ".join(f"ci.{f}" for f in _ITEM_FIELDS)

    conn = _attached(store, card_db)
    try:
        total = conn.execute(
            f"""SELECT COUNT(*) FROM collection_items ci
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                {where}""",
            params,
        ).fetchone()[0]

        totals_row = conn.execute(
            f"""SELECT
                    COALESCE(SUM(CASE WHEN ({unit}) IS NOT NULL
                                      THEN {adj_unit} * ci.quantity END), 0),
                    COALESCE(SUM(ci.quantity), 0),
                    COUNT(CASE WHEN ({unit}) IS NULL THEN 1 END)
                FROM collection_items ci
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                {where}""",
            params,
        ).fetchone()

        rows = conn.execute(
            f"""SELECT {cols},
                       p.set_code, p.set_name, p.collector_number, p.rarity,
                       ({unit}) AS unit_value,
                       {adj_unit} AS adj_value,
                       ({adj_unit} * ci.quantity) AS stack_value
                FROM collection_items ci
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                {where}
                ORDER BY {order}
                LIMIT ? OFFSET ?""",
            params + [int(limit), int(offset)],
        ).fetchall()
    finally:
        conn.close()

    items: list[CollectionItem] = []
    for r in rows:
        data = dict(zip(_ITEM_FIELDS, r[: len(_ITEM_FIELDS)]))
        extra = r[len(_ITEM_FIELDS):]
        item = CollectionItem(**data)
        item.set_code = extra[0] or ""
        item.set_name = extra[1] or ""
        item.collector_number = extra[2] or ""
        item.rarity = extra[3] or ""
        item.unit_price_usd = extra[4]
        items.append(item)

    page_totals = {
        "value_usd": round(totals_row[0] or 0.0, 2),
        "copies": int(totals_row[1]),
        "unpriced_stacks": int(totals_row[2]),
    }
    return items, total, page_totals


def collection_sets(store, card_db, limit: int = 100) -> list[dict]:
    """Sets represented in the collection, most-owned first."""
    conn = _attached(store, card_db)
    try:
        rows = conn.execute(
            f"""SELECT p.set_code, p.set_name,
                       SUM(ci.quantity) AS copies,
                       COALESCE(SUM({_condition_case_sql(f"({_finish_price_sql()})")}
                                    * ci.quantity), 0) AS value
                FROM collection_items ci
                JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                WHERE ci.quantity > 0
                GROUP BY p.set_code, p.set_name
                ORDER BY copies DESC
                LIMIT ?""",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"set_code": r[0], "set_name": r[1], "copies": int(r[2]),
         "value_usd": round(r[3] or 0.0, 2)}
        for r in rows
    ]
