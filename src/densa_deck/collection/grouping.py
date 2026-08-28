"""Isolating part of a collection, and letting it leave.

A collection is a filter, not a box (see `storage.add_to_collection`). That is
what makes this possible at all: putting a thousand cards in "Bundle for Dave"
does not move them, does not change what you own, and can be undone by
untagging. Nothing is destructive until you say the bundle has gone.

Three ways to fill a group, because the three are genuinely different jobs:

* **By scanning** — the fast physical pass. You are holding the cards. The
  point is that this must NOT add copies: you already own them, and a scanner
  that files a second one turns a stocktake into an inventory error.
* **From a deck** — "I'm giving my Atraxa deck to a friend." The deck knows
  card names; the collection knows physical stacks; this joins them.
* **By hand** — searching and ticking, which the collection UI already does.

And one way to empty it: `retire_group`, which is the only destructive thing
in this file and is deliberately the only one that needs a separate,
deliberate call. Everything before it is reversible.
"""

from __future__ import annotations

from datetime import datetime, timezone

from densa_deck.collection.storage import DEFAULT_COLLECTION_NAME


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _resolve_collection(store, collection_uid: str) -> dict:
    found = store.collection_by_uid(collection_uid)
    if not found:
        raise ValueError("No such collection.")
    return found


def tag_owned_printing(store, printing_id: str, collection_uid: str, *,
                       finish: str | None = None,
                       condition: str | None = None,
                       quantity: int = 0) -> dict:
    """Put the copies you ALREADY OWN of one printing into a list.

    The scanner's other mode. `scan_commit` adds a copy, which is right when
    you are entering cards you have just acquired and wrong when you are
    walking a pile you already own picking out a bundle — there, a second copy
    is not a tag, it is a counting error you will not notice for months.

    Three outcomes, and the caller has to be able to tell them apart:

    * **one stack** — tagged, done. The common case.
    * **several stacks** — you own this printing foil and nonfoil, or in two
      conditions. Returns them for the caller to choose from rather than
      picking one, because guessing wrong tags the wrong physical object.
    * **none** — you do not own it. Reported as `owned: 0` rather than
      silently adding it, because "this card is not in your collection" is
      real information when you are building a sale list out of a pile.

    `finish` / `condition` narrow it, which is how a caller answers the
    several-stacks case without needing a second concept.

    `quantity` is how many copies the group is claiming; 0 means the whole
    stack, which is what a scan means — you are holding one card and putting
    the pile it belongs to in the bundle. Selling two of the four you own is
    the case that needs a number.
    """
    collection = _resolve_collection(store, collection_uid)
    items, _ = store.list_items(printing_id=printing_id, finish=finish,
                                condition=condition, limit=100)
    if not items:
        return {
            "printing_id": printing_id,
            "tagged": 0,
            "owned": 0,
            "candidates": [],
            "collection_uid": collection_uid,
        }

    if len(items) > 1:
        return {
            "printing_id": printing_id,
            "tagged": 0,
            "owned": sum(i.quantity for i in items),
            # Enough to tell them apart on a screen, and to send back as the
            # narrowing arguments.
            "candidates": [
                {
                    "item_id": i.item_id,
                    "card_name": i.card_name,
                    "finish": i.finish.value if hasattr(i.finish, "value") else str(i.finish),
                    "condition": (i.condition.value if hasattr(i.condition, "value")
                                  else str(i.condition)),
                    "location": i.location,
                    "quantity": i.quantity,
                }
                for i in items
            ],
            "collection_uid": collection_uid,
        }

    item = items[0]
    added = store.add_to_collection(item.item_id, collection["collection_id"],
                                    quantity=int(quantity))
    return {
        "printing_id": printing_id,
        "item_id": item.item_id,
        "card_name": item.card_name,
        "quantity": item.quantity,
        # False when it was already in the list. Worth distinguishing: on a
        # physical pass you WILL rescan a card, and "already in" is a
        # different reassurance from "added".
        "tagged": 1 if added else 0,
        "already_in": not added,
        "owned": item.quantity,
        "candidates": [],
        "collection_uid": collection_uid,
    }


def tag_item(store, item_id: int, collection_uid: str,
             quantity: int = 0) -> dict:
    """Tag one exact stack — how a caller answers the several-stacks case.

    `quantity` 0 means the whole stack. A number means the group claims that
    many copies and leaves the rest, which is what "sell two of my four" is.
    """
    collection = _resolve_collection(store, collection_uid)
    added = store.add_to_collection(int(item_id), collection["collection_id"],
                                    quantity=int(quantity))
    if not added and quantity:
        # Already in the list, and now with a different claim on it. Setting
        # it rather than ignoring it is what makes the number editable.
        store.set_membership_quantity(int(item_id),
                                      collection["collection_id"],
                                      int(quantity))
    return {"item_id": int(item_id), "tagged": 1 if added else 0,
            "already_in": not added, "quantity": int(quantity),
            "collection_uid": collection_uid}


def untag_item(store, item_id: int, collection_uid: str) -> dict:
    """Take one stack back out. The card is untouched; a filter cannot destroy."""
    collection = _resolve_collection(store, collection_uid)
    removed = store.remove_from_collection(int(item_id),
                                           collection["collection_id"])
    return {"item_id": int(item_id), "untagged": 1 if removed else 0,
            "collection_uid": collection_uid}


def group_from_deck(store, card_db, deck, collection_uid: str, *,
                    deck_id: str | None = None) -> dict:
    """Put the physical cards of a deck into a list.

    "I'm giving my Atraxa deck away." A deck knows card NAMES; the collection
    knows physical stacks; this is the join, and it is not a clean one — you
    may own three copies of a card the deck wants one of, or none at all.

    Two sources, in order:

    **Allocations first.** If the user has bound specific copies to this deck
    (`allocation.py`), those are the answer and there is nothing to guess:
    they said which sleeve holds which card.

    **Otherwise, owned stacks by name.** Cheapest copies first, so giving a
    deck away does not quietly hand over the foil when you own a plain one
    too — the same reasoning `deck_value` uses for what a deck is worth.

    Cards it could not place come back in `missing` rather than being dropped.
    A group silently short eleven cards is worse than one that says so, since
    the whole point is to hand over a known quantity.
    """
    collection = _resolve_collection(store, collection_uid)

    wanted: dict[str, int] = {}
    for entry in (getattr(deck, "entries", None) or []):
        name = (getattr(entry, "card_name", "") or "").strip()
        if not name:
            continue
        wanted[name.lower()] = wanted.get(name.lower(), 0) + int(
            getattr(entry, "quantity", 1) or 0)
    display = {}
    for entry in (getattr(deck, "entries", None) or []):
        name = (getattr(entry, "card_name", "") or "").strip()
        if name:
            display.setdefault(name.lower(), name)

    # item_id -> copies this group claims from that stack.
    tagged_items: dict[int, int] = {}
    covered: dict[str, int] = {}

    # Allocations: the user already said which copies these are.
    if deck_id:
        try:
            from densa_deck.collection.allocation import allocations_for_deck
            for row in allocations_for_deck(store, card_db, deck_id):
                key = (row.get("card_name") or "").lower()
                if key not in wanted:
                    continue
                take = int(row.get("quantity", 0)) or 1
                tagged_items[int(row["item_id"])] = (
                    tagged_items.get(int(row["item_id"]), 0) + take)
                covered[key] = covered.get(key, 0) + take
        except Exception:
            # Allocation is an optional layer. Losing it costs precision here,
            # never the operation.
            covered = dict(covered)

    # Everything still short, filled from what is owned, cheapest first.
    for key, need in wanted.items():
        still = need - covered.get(key, 0)
        if still <= 0:
            continue
        items, _ = store.list_items(name_like=display[key], limit=200)
        # `name_like` is a substring match, so "Bolt" would drag in
        # "Lightning Bolt". Only exact names are this card.
        items = [i for i in items
                 if (i.card_name or "").strip().lower() == key
                 and i.item_id not in tagged_items]
        items.sort(key=lambda i: _unit_price(card_db, i))
        for item in items:
            if still <= 0:
                break
            take = min(still, item.quantity)
            # How many of this stack the group claims — NOT the whole stack.
            # A deck wanting one Lightning Bolt out of the four you own must
            # hand over one, and a group that could only say "this stack"
            # would give away all four when it was retired.
            tagged_items[item.item_id] = tagged_items.get(item.item_id, 0) + take
            covered[key] = covered.get(key, 0) + take
            still -= take

    added = 0
    for item_id, take in tagged_items.items():
        if store.add_to_collection(item_id, collection["collection_id"],
                                   quantity=take):
            added += 1

    missing = [
        {"card_name": display[key], "needed": need,
         "found": covered.get(key, 0), "short": need - covered.get(key, 0)}
        for key, need in sorted(wanted.items())
        if covered.get(key, 0) < need
    ]

    return {
        "collection_uid": collection_uid,
        "stacks_tagged": added,
        "stacks_matched": len(tagged_items),
        "cards_wanted": sum(wanted.values()),
        "cards_found": sum(covered.values()),
        "missing": missing,
    }


def _unit_price(card_db, item) -> float:
    """What one copy of a stack is worth, for "give away the cheap one first".

    An unpriced card sorts LAST rather than first. Treating unknown as zero
    would hand over every card the catalogue could not price before touching
    anything it could, which is the wrong way round when the unknown ones are
    disproportionately the odd, old and interesting.
    """
    try:
        printing = card_db.get_printing(item.printing_id)
    except Exception:
        printing = None
    if not printing:
        return float("inf")
    price = printing.get("price_usd")
    return float(price) if price is not None else float("inf")


def group_contents(store, card_db, collection_uid: str, *,
                   limit: int = 2000) -> dict:
    """What is in a group, what it is worth, and what you would regret selling.

    The review step, and the reason retiring is a separate call. Three things
    worth knowing before a thousand cards leave the house:

    * how many stacks and copies, and what they are worth as you own them;
    * which of them your DECKS still want — the honest version of "are you
      sure", because the alternative is finding out at the table;
    * which are worth enough individually to be worth pulling out of a bundle.
    """
    from densa_deck.collection.query import search_collection

    collection = _resolve_collection(store, collection_uid)
    items, total, totals = search_collection(
        store, card_db, collection_id=collection["collection_id"],
        sort="value_desc", limit=limit, offset=0)

    # How many copies of each stack this group is actually claiming. 0 in the
    # table means the whole stack, which is what a plain tag means.
    claimed = store.membership_quantities(collection["collection_id"])
    taking = {i.item_id: (claimed.get(i.item_id) or i.quantity) for i in items}

    # What you would regret. Not the general overlaps report, which answers a
    # different question: this one is "after these copies leave, is a list
    # still expecting them?"
    #
    # The default collection is excluded, and so is this group. Neither is a
    # claim on the card — one is where it lives and the other is the premise —
    # and counting them would flag every card in the bundle.
    at_risk = []
    try:
        for row in store.overlaps(2):
            item_id = row.get("item_id")
            if item_id not in taking:
                continue
            others = [name for name in (row.get("collections") or [])
                      if name not in (collection.get("name"),
                                      DEFAULT_COLLECTION_NAME)]
            if not others:
                continue
            left = int(row.get("quantity", 0)) - int(taking.get(item_id, 0))
            if left < len(others):
                at_risk.append({
                    "card_name": row.get("card_name"),
                    "collections": others,
                    "quantity": row.get("quantity", 0),
                    "leaving": taking.get(item_id, 0),
                    "left_after": max(0, left),
                })
    except Exception:
        at_risk = []

    return {
        "collection_uid": collection_uid,
        "name": collection.get("name", ""),
        "stacks": total,
        # What is LEAVING, which is not the same as what the stacks hold: a
        # group can claim two of the four copies in a stack.
        "copies": sum(taking.values()),
        "copies_in_stacks": totals.get("copies", 0),
        "value_usd": round(sum(
            (getattr(i, "unit_price_usd", None) or 0.0) * taking[i.item_id]
            for i in items), 2),
        "unpriced_stacks": totals.get("unpriced_stacks", 0),
        "wanted_elsewhere": at_risk,
        "cards": [
            {
                "item_id": i.item_id,
                "printing_id": i.printing_id,
                "card_name": i.card_name,
                "set_code": getattr(i, "set_code", "") or "",
                "collector_number": getattr(i, "collector_number", "") or "",
                "finish": i.finish.value if hasattr(i.finish, "value") else str(i.finish),
                "condition": (i.condition.value if hasattr(i.condition, "value")
                              else str(i.condition)),
                "language": i.language,
                # How many you own, and how many of them this group takes.
                "quantity": taking[i.item_id],
                "owned": i.quantity,
                "unit_price_usd": getattr(i, "unit_price_usd", None),
            }
            for i in items
        ],
    }


def retire_group(store, card_db, collection_uid: str, *,
                 sale_price_usd: float | None = None,
                 sold_to: str = "", note: str = "",
                 delete_group: bool = True) -> dict:
    """The cards in this group have gone. Take them off the collection.

    The one destructive call in this file, and deliberately separate from
    everything that builds a group: tagging is free and reversible right up
    until this, so a thousand-card bundle can be assembled and revised without
    risk and the irreversible step happens once, on a list already reviewed.

    `sale_price_usd` records the whole thing as a sale — one lot, allocated
    across the cards by market value, so a bundle leaves the collection and
    lands in the P&L in the same action. Without it this is a giveaway, which
    is a real case and must not be forced to pretend it earned nothing.

    Returns what left, priced, so the caller can show it afterwards. A
    destructive action that reports only "done" gives you nothing to check
    against.
    """
    contents = group_contents(store, card_db, collection_uid)

    # Everything, not the first page of it.
    #
    # `group_contents` caps what it returns, which is right for a review
    # screen and catastrophic here: this is the destructive call, and it
    # finishes by deleting the group. A bundle bigger than one page would
    # have the cards past the cap left in the collection with the list that
    # named them thrown away — no record of which they were, and a sale
    # recorded for a lot that did not all leave.
    #
    # `stacks` is the full count regardless of how many rows came back, so
    # the shortfall is visible rather than something to hope about.
    if len(contents["cards"]) < int(contents.get("stacks") or 0):
        contents = group_contents(store, card_db, collection_uid,
                                  limit=int(contents["stacks"]))

    rows = contents["cards"]
    short = len(rows) < int(contents.get("stacks") or 0)
    if not rows:
        return {"collection_uid": collection_uid, "copies_removed": 0,
                "stacks_removed": 0, "value_usd": 0.0, "sale_recorded": False}

    # Split the lot price across the cards by what they are worth, so each
    # sale row carries a believable price rather than the total or a zero.
    priced_total = sum((r["unit_price_usd"] or 0.0) * r["quantity"] for r in rows)
    recorded = 0
    if sale_price_usd is not None:
        from densa_deck.collection.reseller import record_sale
        for row in rows:
            worth = (row["unit_price_usd"] or 0.0) * row["quantity"]
            share = (
                float(sale_price_usd) * (worth / priced_total)
                if priced_total > 0
                else float(sale_price_usd) / len(rows)
            )
            try:
                record_sale(
                    store,
                    printing_id=row["printing_id"],
                    card_name=row["card_name"],
                    sale_price_usd=round(share, 2),
                    quantity=row["quantity"],
                    finish=row["finish"],
                    condition=row["condition"],
                    # The stack, so cost basis is copied onto the sale row
                    # from the copies actually being sold. Without it a
                    # bundle sale records revenue and no basis, and the P&L
                    # reads as pure profit.
                    item_id=row["item_id"],
                    # This function does the removing, once, below. Letting
                    # record_sale do it too would take the copies off twice —
                    # harmless on a stack that hits zero, and wrong on one
                    # that does not.
                    remove_from_collection=False,
                    notes=(note or f"Part of {contents['name'] or 'a bundle'}")
                    + (f" — to {sold_to}" if sold_to else ""),
                )
                recorded += 1
            except Exception:
                # A sale row that would not write must not strand the cards
                # half-removed. The removal below is what the user asked for.
                pass

    copies = 0
    stacks = 0
    for row in rows:
        try:
            # Only what the group claimed. A bundle taking two of the four
            # copies in a stack must leave two behind, not empty it.
            keep = max(0, int(row["owned"]) - int(row["quantity"]))
            store.set_item_quantity(row["item_id"], keep)
            copies += row["quantity"]
            stacks += 1
        except Exception:
            pass

    # Only when the whole group actually left. Deleting a list that still
    # describes cards sitting in the collection destroys the only record of
    # which ones they were.
    if delete_group and not short:
        try:
            collection = _resolve_collection(store, collection_uid)
            # The cards are already gone; this only removes the now-empty
            # grouping. discard_cards stays False so this can never be the
            # thing that deletes something.
            store.delete_collection(collection["collection_id"],
                                    discard_cards=False)
        except Exception:
            pass

    return {
        "collection_uid": collection_uid,
        "name": contents["name"],
        "stacks_removed": stacks,
        "copies_removed": copies,
        "value_usd": contents["value_usd"],
        "sale_recorded": recorded > 0,
        "sale_rows": recorded,
        "sale_price_usd": sale_price_usd,
        "group_deleted": delete_group and not short,
        # True when the group held more than could be read back in one go and
        # some of it is still there. The caller has to be able to say so
        # rather than reporting a bundle gone that partly is not.
        "incomplete": short,
        "stacks_expected": int(contents.get("stacks") or 0),
    }
