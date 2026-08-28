"""What a collection is made of, and how far through a set you are.

A deck has had a breakdown since the beginning — colours, curve, types, what
it is worth. A collection had a card count and a total, which answers "how
much stuff" and none of the questions anyone actually has about a box: what
colours am I deep in, is this shelf all two-drops, how much of it is rares,
which sets am I close to finishing.

The same shape as the deck breakdown on purpose. A collection and a deck are
both piles of cards, and describing them in two different vocabularies means
comparing them is work the reader has to do.

Scoped by collection, so it answers for a GROUP as readily as for everything
owned — which is what makes it useful when assembling a bundle, where "what
is in this and what is it worth" is the whole question.

Counted in SQL against the attached collection database rather than by
pulling several thousand rows into Python. A real collection is tens of
thousands of cards and this is drawn on a panel that has to feel instant.
"""

from __future__ import annotations

# Cards are grouped by the FIRST type that matters, not by every type they
# have. An "Artifact Creature" belongs under creatures in every deck list
# anyone has ever written, and counting it twice makes the buckets sum to
# more than the collection.
_TYPE_ORDER = [
    ("Land", "Lands"),
    ("Creature", "Creatures"),
    ("Planeswalker", "Planeswalkers"),
    ("Instant", "Instants"),
    ("Sorcery", "Sorceries"),
    ("Artifact", "Artifacts"),
    ("Enchantment", "Enchantments"),
    ("Battle", "Battles"),
]

_COLOR_NAMES = {
    "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green",
}


def _scope(collection_id):
    """The WHERE fragment and params that limit this to one group.

    Membership OR filing, as everywhere else in this app: a card belongs to a
    list either because it was put there or because that is where it lives,
    and a breakdown that knew only one of those would describe a different
    pile than the one on screen.
    """
    if collection_id is None:
        return "", []
    return (
        " AND (ci.collection_id = ? OR ci.item_id IN "
        "(SELECT item_id FROM collection.collection_membership "
        "WHERE collection_id = ?))",
        [int(collection_id), int(collection_id)],
    )


def _attached(store, card_db):
    """Attach the collection database, or say it could not be."""
    try:
        return card_db.attach_collection(store.db_path)
    except Exception:
        return False


def breakdown(store, card_db, collection_id: int | None = None) -> dict:
    """Colours, curve, types, rarity, sets and value for a pile of cards.

    Every section counts COPIES rather than distinct cards. Someone with
    forty Forests has forty green cards, and a breakdown that reported one
    would be describing a list rather than a box.
    """
    out = {
        "colors": [], "curve": [], "types": [], "rarities": [], "sets": [],
        "total_cards": 0, "distinct_cards": 0, "value_usd": 0.0,
        "unpriced_cards": 0, "ready": False,
    }
    if not _attached(store, card_db):
        return out
    conn = card_db.connect()
    where, params = _scope(collection_id)
    out["ready"] = True

    # --- the totals ------------------------------------------------------
    row = conn.execute(
        f"""SELECT COALESCE(SUM(ci.quantity), 0),
                   COUNT(DISTINCT ci.card_name COLLATE NOCASE)
            FROM collection.collection_items ci
            WHERE ci.quantity > 0{where}""", params).fetchone()
    out["total_cards"] = int(row[0] or 0)
    out["distinct_cards"] = int(row[1] or 0)
    if not out["total_cards"]:
        return out

    # --- what it is worth ------------------------------------------------
    #
    # Priced off the PRINTING, because that is what a copy is: a foil Sol
    # Ring from Commander Masters and a nonfoil from a starter deck are not
    # interchangeable amounts of money. Falls back to the card only when the
    # stack never named a printing.
    value_row = conn.execute(
        f"""SELECT COALESCE(SUM(ci.quantity * COALESCE(
                       CASE WHEN ci.finish = 'foil' THEN p.price_usd_foil END,
                       p.price_usd, c.price_usd)), 0.0),
                   COALESCE(SUM(CASE WHEN COALESCE(
                       CASE WHEN ci.finish = 'foil' THEN p.price_usd_foil END,
                       p.price_usd, c.price_usd) IS NULL
                       THEN ci.quantity ELSE 0 END), 0)
            FROM collection.collection_items ci
            LEFT JOIN card_printings p ON p.printing_id = ci.printing_id
            LEFT JOIN cards c ON c.name = ci.card_name COLLATE NOCASE
            WHERE ci.quantity > 0{where}""", params).fetchone()
    out["value_usd"] = round(float(value_row[0] or 0.0), 2)
    out["unpriced_cards"] = int(value_row[1] or 0)

    # --- colours ---------------------------------------------------------
    #
    # Read off `color_identity`, which is the JSON array the card table
    # stores. Counted per colour rather than per combination: "how much black
    # do I own" is the question, and a Dimir card is black and blue.
    rows = conn.execute(
        f"""SELECT c.color_identity, SUM(ci.quantity)
            FROM collection.collection_items ci
            JOIN cards c ON c.name = ci.card_name COLLATE NOCASE
            WHERE ci.quantity > 0{where}
            GROUP BY c.color_identity""", params).fetchall()
    per_colour: dict[str, int] = {}
    colourless = 0
    for identity, count in rows:
        count = int(count or 0)
        letters = [ch for ch in (identity or "") if ch in _COLOR_NAMES]
        if not letters:
            colourless += count
            continue
        for letter in set(letters):
            per_colour[letter] = per_colour.get(letter, 0) + count
    out["colors"] = [
        {"color": letter, "name": _COLOR_NAMES[letter], "cards": per_colour[letter]}
        for letter in ("W", "U", "B", "R", "G") if per_colour.get(letter)
    ]
    if colourless:
        out["colors"].append(
            {"color": "C", "name": "Colorless", "cards": colourless})

    # --- mana curve ------------------------------------------------------
    #
    # Lands excluded, and that is not a detail: a collection is mostly lands
    # by volume, they all cost nothing, and leaving them in produces a curve
    # that is one enormous bar at zero and says nothing about the spells.
    rows = conn.execute(
        f"""SELECT CAST(MIN(c.cmc, 7) AS INTEGER), SUM(ci.quantity)
            FROM collection.collection_items ci
            JOIN cards c ON c.name = ci.card_name COLLATE NOCASE
            WHERE ci.quantity > 0 AND c.type_line NOT LIKE '%Land%'{where}
            GROUP BY CAST(MIN(c.cmc, 7) AS INTEGER)
            ORDER BY 1""", params).fetchall()
    curve = {int(r[0] or 0): int(r[1] or 0) for r in rows}
    out["curve"] = [
        {"cmc": n, "label": "7+" if n >= 7 else str(n), "cards": curve.get(n, 0)}
        for n in range(0, 8)
    ]

    # --- types -----------------------------------------------------------
    rows = conn.execute(
        f"""SELECT c.type_line, SUM(ci.quantity)
            FROM collection.collection_items ci
            JOIN cards c ON c.name = ci.card_name COLLATE NOCASE
            WHERE ci.quantity > 0{where}
            GROUP BY c.type_line""", params).fetchall()
    per_type: dict[str, int] = {}
    for type_line, count in rows:
        # Only the part before the em-dash: "Creature — Human Wizard" is a
        # creature, and matching on the whole line would file every Island
        # under whatever its subtype happens to spell.
        primary = (type_line or "").split("—")[0]
        label = "Other"
        for needle, name in _TYPE_ORDER:
            if needle.lower() in primary.lower():
                label = name
                break
        per_type[label] = per_type.get(label, 0) + int(count or 0)
    ordered = [name for _needle, name in _TYPE_ORDER] + ["Other"]
    out["types"] = [{"type": name, "cards": per_type[name]}
                    for name in ordered if per_type.get(name)]

    # --- rarity ----------------------------------------------------------
    rows = conn.execute(
        f"""SELECT LOWER(COALESCE(p.rarity, '')), SUM(ci.quantity)
            FROM collection.collection_items ci
            LEFT JOIN card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0{where}
            GROUP BY LOWER(COALESCE(p.rarity, ''))""", params).fetchall()
    order = {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3, "special": 4,
             "bonus": 5, "": 6}
    out["rarities"] = sorted(
        [{"rarity": r[0] or "unknown", "cards": int(r[1] or 0)} for r in rows],
        key=lambda x: order.get(
            "" if x["rarity"] == "unknown" else x["rarity"], 9))

    # --- sets ------------------------------------------------------------
    rows = conn.execute(
        f"""SELECT UPPER(COALESCE(p.set_code, '')),
                   COALESCE(p.set_name, ''), SUM(ci.quantity)
            FROM collection.collection_items ci
            JOIN card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0{where}
            GROUP BY UPPER(COALESCE(p.set_code, '')), COALESCE(p.set_name, '')
            ORDER BY 3 DESC""", params).fetchall()
    out["sets"] = [{"set_code": r[0], "set_name": r[1], "cards": int(r[2] or 0)}
                   for r in rows if r[0]]
    return out


def set_completion(store, card_db, *, collection_id: int | None = None,
                   limit: int = 60, min_owned: int = 1) -> dict:
    """How far through each set you are.

    Counted against DISTINCT collector numbers rather than printings. A set
    has one Lightning Bolt at #161, and the alternate-art, borderless and
    promo versions of it are the same slot in the set — counting them as
    separate cards puts the denominator above what anyone means by "how many
    cards are in this set", and lets someone exceed 100%.

    English only, for the same reason: a Japanese Lightning Bolt is the same
    slot, and every set would otherwise report a denominator ten times too
    large.

    Needs the printing catalogue, which is an opt-in download. Without it
    `ready` is False and the caller says so rather than showing zeroes that
    look like an empty collection.
    """
    out = {"sets": [], "ready": False, "catalogue_ready": False}
    if not _attached(store, card_db):
        return out
    conn = card_db.connect()
    out["ready"] = True

    total = conn.execute("SELECT COUNT(*) FROM card_printings").fetchone()[0]
    if not total:
        return out
    out["catalogue_ready"] = True

    where, params = _scope(collection_id)
    # Owned slots per set: distinct collector numbers, so four copies of one
    # card and four printings of it both count once.
    rows = conn.execute(
        f"""SELECT UPPER(p.set_code),
                   COUNT(DISTINCT LOWER(p.collector_number))
            FROM collection.collection_items ci
            JOIN card_printings p ON p.printing_id = ci.printing_id
            WHERE ci.quantity > 0 AND p.set_code != ''{where}
            GROUP BY UPPER(p.set_code)""", params).fetchall()
    owned = {r[0]: int(r[1] or 0) for r in rows}
    if not owned:
        return out

    placeholders = ", ".join("?" * len(owned))
    totals = conn.execute(
        f"""SELECT UPPER(set_code), MAX(set_name),
                   COUNT(DISTINCT LOWER(collector_number))
            FROM card_printings
            WHERE UPPER(set_code) IN ({placeholders}) AND lang = 'en'
            GROUP BY UPPER(set_code)""", list(owned)).fetchall()

    made = []
    for set_code, set_name, in_set in totals:
        have = owned.get(set_code, 0)
        if have < min_owned:
            continue
        in_set = int(in_set or 0)
        made.append({
            "set_code": set_code,
            "set_name": set_name or set_code,
            "owned": have,
            "in_set": in_set,
            # Capped at 100. Promos and variants can still put a collection
            # above the slot count in edge cases, and "104% complete" reads
            # as a bug rather than as a full set.
            "percent": round(min(100.0, 100.0 * have / in_set), 1)
            if in_set else None,
            "complete": bool(in_set and have >= in_set),
        })
    # Closest to finished first — that is the actionable end of the list.
    made.sort(key=lambda s: (-(s["percent"] or 0), -s["owned"]))
    out["sets"] = made[:limit]
    return out
