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

# What each sort orders by, and which way round it reads naturally.
#
# Direction is a SEPARATE control rather than part of the key. Baked into the
# name it has to be enumerated — `value_desc`, `value_asc` — which is why
# half these sorts only ever went one way: there was no `quantity_asc`, no
# `oldest`, and no way to read a set backwards. Splitting them means every
# sort reverses, including ones added later.
#
# The default direction is the one that answers the obvious question. Nobody
# opens a collection to see their cheapest card, so value starts high; a
# curve starts at one mana and counts up.
#
# Mapped here rather than accepting raw SQL from the caller — these strings
# are interpolated into a query.
SORT_COLUMNS = {
    "name": ("ci.card_name COLLATE NOCASE", "asc"),
    "value": ("stack_value", "desc"),
    "unit": ("unit_value", "desc"),
    "quantity": ("ci.quantity", "desc"),
    "added": ("ci.created_at", "desc"),
    "set": ("p.set_code", "asc"),
    "cmc": ("c.cmc", "asc"),
    # Alphabetical rarity is meaningless — "common" before "uncommon" before
    # "rare" is an accident of spelling. Ranked instead, so reversing it
    # gives you the mythics.
    "rarity": ("""CASE LOWER(COALESCE(p.rarity, ''))
                      WHEN 'common' THEN 1 WHEN 'uncommon' THEN 2
                      WHEN 'rare' THEN 3 WHEN 'mythic' THEN 4
                      WHEN 'special' THEN 5 WHEN 'bonus' THEN 6
                      ELSE 0 END""", "asc"),
}

# The spellings callers used before direction was separable. Kept working
# because they are stored in saved views and passed by the phone, and a sort
# that silently becomes name-order is worse than one that errors.
SORT_ALIASES = {
    "value_desc": ("value", "desc"), "value_asc": ("value", "asc"),
    "unit_desc": ("unit", "desc"), "unit_asc": ("unit", "asc"),
    "quantity_desc": ("quantity", "desc"), "newest": ("added", "desc"),
    "oldest": ("added", "asc"),
}


def resolve_order(sort: str, direction: str = "") -> str:
    """The ORDER BY for a sort key and a direction.

    `direction` wins when given; otherwise the sort's natural one is used.
    An unknown key falls back to name order rather than raising — this is
    reached from a dropdown, and a stale saved view should show cards.

    Two rules the reverse must NOT break:

    * Unknowns stay at the bottom. A card with no price is not the cheapest
      card, and a card whose CMC we cannot read is not a Black Lotus — put
      them first on reverse and the top of the list becomes the rows the
      database knows least about.
    * The tiebreaker never flips. Cards at equal cost stay alphabetical
      whichever way the list runs, so paging is stable and a reversed list
      is the same rows in the opposite order rather than a reshuffle.
    """
    key = (sort or "name").strip().lower()
    want = (direction or "").strip().lower()
    if key in SORT_ALIASES:
        key, natural = SORT_ALIASES[key]
        column = SORT_COLUMNS[key][0]
    elif key in SORT_COLUMNS:
        column, natural = SORT_COLUMNS[key]
    else:
        column, natural = SORT_COLUMNS["name"]
        key = "name"
    way = "DESC" if (want or natural) == "desc" else "ASC"

    order = f"{column} {way} NULLS LAST"
    if key == "set":
        # Within a set, collector number — reversed too, so the last card of
        # the last set is genuinely the other end of the list.
        order += f", CAST(p.collector_number AS INTEGER) {way} NULLS LAST"
    if key != "name":
        order += ", ci.card_name COLLATE NOCASE ASC"
    if key == "added":
        order += f", ci.item_id {way}"
    return order


# Kept for callers that only ever wanted the default direction.
SORT_OPTIONS = {key: resolve_order(key) for key in SORT_COLUMNS}

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
    collection_id: int | None = None,
    set_code: str | None = None,
    rarity: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    unpriced_only: bool = False,
    sort: str = "name",
    direction: str = "",
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
    # None means the MASTER collection — every stack, whatever grouping it is
    # in — which is the difference between "my Modern binder" and "my cards".
    #
    # Membership OR filing, and both halves are needed. `ci.collection_id`
    # says where a card physically LIVES, which is one place because a card is
    # in one box. `collection_membership` says which LISTS mention it, which
    # is any number, because collections are filters — that is the whole model
    # (see `storage.add_to_collection`).
    #
    # Filing alone was the condition here, so a card added to a list without
    # being moved was invisible in that list on the desktop. The phone has
    # always read both; this is the desktop catching up, and it is why a group
    # you tagged looked empty.
    if collection_id is not None:
        conditions.append(
            "(ci.collection_id = ? OR ci.item_id IN "
            "(SELECT item_id FROM collection_membership WHERE collection_id = ?))")
        params.extend([int(collection_id), int(collection_id)])
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
    order = resolve_order(sort, direction)
    cols = ", ".join(f"ci.{f}" for f in _ITEM_FIELDS)

    conn = _attached(store, card_db)
    try:
        total = conn.execute(
            f"""SELECT COUNT(*) FROM collection_items ci
                LEFT JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                LEFT JOIN cards.cards c ON c.oracle_id = p.oracle_id
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
                LEFT JOIN cards.cards c ON c.oracle_id = p.oracle_id
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
                LEFT JOIN cards.cards c ON c.oracle_id = p.oracle_id
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
