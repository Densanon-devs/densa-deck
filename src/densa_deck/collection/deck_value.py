"""What a deck is worth, and what finishing it costs.

Three different numbers people conflate, kept apart here because they answer
genuinely different questions:

**Deck value** — what the cards in this deck are worth *as you own them*.
Your foil Secret Lair copy counts at foil Secret Lair money. This is the
insurance number: what walks out of the door if the bag goes missing.

**Build value** — what it would cost someone to assemble the same 100 cards
buying the cheapest legal printing of each. Your $652 foil Skithiryx is a
$12 Scars of Mirrodin card to them. This is the honest "is this deck
expensive?" number, and it is the one worth quoting to a playgroup.

**Cost to complete** — cheapest printings of only the cards you're missing.
This is the shopping list, and it is the number people actually act on.

For cards you own, deck value uses the *cheapest copy you own* rather than
your best one. If you own a $1.50 Sol Ring and a $652 foil, the deck is
almost certainly running the cheap one; assuming otherwise inflates the
number and flatters the user, which is the wrong direction for a tool people
make money decisions with. Phase 3's opt-in allocation is how someone who
really has sleeved the foil says so.
"""

from __future__ import annotations

from densa_deck.collection.models import CONDITION_MULTIPLIERS, Condition
from densa_deck.collection.prices import _attached, _finish_price_sql


def _owned_unit_values(store, card_db) -> dict[str, float]:
    """Lowercased card name -> cheapest condition-adjusted unit value owned.

    MIN rather than MAX deliberately: see module docstring.
    """
    conn = _attached(store, card_db)
    try:
        whens = " ".join(
            f"WHEN '{c.value}' THEN ({_finish_price_sql()}) * {CONDITION_MULTIPLIERS[c]}"
            for c in Condition
        )
        rows = conn.execute(
            f"""SELECT LOWER(ci.card_name),
                       MIN(CASE ci.condition {whens}
                           ELSE ({_finish_price_sql()}) END)
                FROM collection_items ci
                JOIN cards.card_printings p ON p.printing_id = ci.printing_id
                WHERE ci.quantity > 0 AND ({_finish_price_sql()}) IS NOT NULL
                GROUP BY LOWER(ci.card_name)"""
        ).fetchall()
    finally:
        conn.close()
    return {name: value for name, value in rows if value is not None}


def value_deck(deck, store, card_db, version_store=None, *, deck_id: str | None = None) -> dict:
    """Deck value, build value, and cost to complete, with ownership context.

    Every money figure carries an `unpriced` count beside it. A deck total
    that quietly omits eight cards it couldn't price is worse than no total,
    because it looks authoritative.
    """
    from densa_deck.collection.ownership import ownership_for_deck

    entries = getattr(deck, "entries", None) or []
    needed: dict[str, int] = {}
    display: dict[str, str] = {}
    for entry in entries:
        name = (getattr(entry, "card_name", "") or "").strip()
        if not name:
            continue
        key = name.lower()
        needed[key] = needed.get(key, 0) + int(getattr(entry, "quantity", 1) or 0)
        display.setdefault(key, name)

    owned_values = _owned_unit_values(store, card_db)
    cheapest = card_db.cheapest_prices_for_names(list(display.values()))
    ownership = ownership_for_deck(deck, store, version_store, deck_id=deck_id)
    own_rows = {r["card_name"].lower(): r for r in ownership["cards"]}

    deck_total = 0.0
    build_total = 0.0
    complete_total = 0.0
    deck_unpriced = 0
    build_unpriced = 0
    complete_unpriced = 0
    rows = []

    for key, qty in needed.items():
        own = own_rows.get(key, {})
        owned_qty = int(own.get("owned", 0))
        missing = int(own.get("missing", qty))

        # Deck value: copies you own valued at what you own; anything short
        # falls back to the cheapest printing, so the number reflects the
        # deck as it would actually be built.
        owned_unit = owned_values.get(key)
        cheap_unit = cheapest.get(key)
        counted_owned = min(owned_qty, qty)

        line_deck = 0.0
        line_deck_known = True
        if counted_owned:
            if owned_unit is None:
                line_deck_known = False
            else:
                line_deck += owned_unit * counted_owned
        if missing:
            if cheap_unit is None:
                line_deck_known = False
            else:
                line_deck += cheap_unit * missing

        if line_deck_known:
            deck_total += line_deck
        else:
            deck_unpriced += 1

        if cheap_unit is None:
            build_unpriced += 1
        else:
            build_total += cheap_unit * qty

        if missing:
            if cheap_unit is None:
                complete_unpriced += 1
            else:
                complete_total += cheap_unit * missing

        rows.append({
            "card_name": display[key],
            "needed": qty,
            "owned": owned_qty,
            "missing": missing,
            "blocked": int(own.get("blocked", 0)),
            "available": int(own.get("available", 0)),
            "owned_unit_usd": round(owned_unit, 2) if owned_unit is not None else None,
            "cheapest_unit_usd": round(cheap_unit, 2) if cheap_unit is not None else None,
            "line_value_usd": round(line_deck, 2) if line_deck_known else None,
            "line_to_complete_usd": (
                round(cheap_unit * missing, 2) if missing and cheap_unit is not None else 0.0
            ),
        })

    rows.sort(key=lambda r: (-(r["line_to_complete_usd"] or 0), r["card_name"].lower()))

    return {
        "deck_name": getattr(deck, "name", "Deck"),
        "distinct_cards": len(needed),
        "total_cards": sum(needed.values()),
        "owned_distinct": ownership["owned_distinct"],
        "missing_distinct": ownership["missing_distinct"],
        "missing_copies": ownership["missing_copies"],
        "blocked_distinct": ownership["blocked_distinct"],
        "deck_value_usd": round(deck_total, 2),
        "deck_value_unpriced": deck_unpriced,
        "build_value_usd": round(build_total, 2),
        "build_value_unpriced": build_unpriced,
        "cost_to_complete_usd": round(complete_total, 2),
        "cost_to_complete_unpriced": complete_unpriced,
        "cards": rows,
        "shopping_list": [
            r for r in rows if r["missing"] > 0
        ],
    }


def shopping_list_text(value: dict) -> str:
    """The missing cards as a pasteable decklist.

    Deliberately plain `N Card Name` — that is what every vendor's mass-entry
    box accepts, so the shopping list is one copy-paste from a cart.
    """
    lines = []
    for row in value.get("shopping_list", []):
        lines.append(f"{row['missing']} {row['card_name']}")
    return "\n".join(lines)
