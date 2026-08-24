"""Owned / committed / available maths.

The question a collection has to answer while you're building a deck is not
"do I own a Sol Ring" but "do I own one that isn't already sleeved into
something else". That needs three numbers per card:

    owned      copies in the collection, across every printing and finish
    committed  copies called for by the latest saved version of every deck
    available  owned - committed, floored at zero

Allocation is computed at the **oracle/name level**, not the printing level,
and that is a deliberate design choice rather than a shortcut:

  * `DeckEntry` carries `card_name + quantity + zone` and nothing else. The
    deck parser actively strips set codes and collector numbers from imported
    lists, so a deck literally cannot say which printing it wants.
  * Saved decks are `{card_name: quantity}` snapshots. Name is the only key
    both sides share.

So "which physical copy is in which deck" is a Phase 3 opt-in refinement.
Everything users actually ask — can I build this, what am I missing, how many
are spare — is answerable at the name level today.

Committed is measured against the **latest saved version of each deck**, keyed
on the stable `deck_id`. Counting every historical version would multiply-count
a card that survived ten revisions of the same deck.
"""

from __future__ import annotations

from densa_deck.collection.models import OwnershipRow
from densa_deck.collection.storage import CollectionStore


def committed_by_name(version_store, exclude_deck_id: str | None = None) -> dict[str, int]:
    """Lowercased card name -> copies committed across all saved decks.

    `exclude_deck_id` leaves one deck out, which is what you want when asking
    "what's available for *this* deck" — a deck should not be counted as
    competing with itself for its own cards.

    Tolerant by design: a deck whose latest snapshot fails to load is skipped
    rather than allowed to blow up an ownership panel. A missing deck should
    understate commitment, never break the collection view.
    """
    committed: dict[str, int] = {}
    try:
        decks = version_store.list_decks()
    except Exception:
        return committed

    for deck in decks:
        deck_id = deck.get("deck_id") if isinstance(deck, dict) else None
        if not deck_id or deck_id == exclude_deck_id:
            continue
        try:
            snap = version_store.get_latest(deck_id)
        except Exception:
            continue
        if not snap:
            continue
        for name, qty in (getattr(snap, "decklist", None) or {}).items():
            key = (name or "").lower()
            if not key:
                continue
            try:
                committed[key] = committed.get(key, 0) + int(qty)
            except (TypeError, ValueError):
                continue
    return committed


def ownership_rows(
    store: CollectionStore,
    version_store=None,
    *,
    names: list[str] | None = None,
    exclude_deck_id: str | None = None,
) -> dict[str, OwnershipRow]:
    """Ownership keyed by lowercased card name.

    Restrict to `names` when answering about one decklist; omit it for the
    whole collection. Names with neither ownership nor commitment are still
    returned when explicitly asked for, so a caller can render "not owned"
    without a second lookup.
    """
    owned = store.owned_by_name()
    committed = committed_by_name(version_store, exclude_deck_id) if version_store else {}

    if names is None:
        keys = set(owned) | set(committed)
        display = {k: k for k in keys}
    else:
        display = {}
        for n in names:
            display[(n or "").lower()] = n

    out: dict[str, OwnershipRow] = {}
    for key, shown in display.items():
        out[key] = OwnershipRow(
            card_name=shown if names is not None else shown,
            owned=owned.get(key, 0),
            committed=committed.get(key, 0),
        )
    return out


def ownership_for_deck(
    deck,
    store: CollectionStore,
    version_store=None,
    *,
    deck_id: str | None = None,
) -> dict:
    """Owned / missing / available for one deck.

    `deck_id` excludes this deck from its own committed totals — without it a
    saved deck would report every one of its own cards as unavailable.

    Returns per-card rows plus totals. `missing` counts copies the deck needs
    beyond what is owned; `available_elsewhere` counts copies that exist but
    are committed to a different deck — a materially different problem, since
    one is "buy it" and the other is "unsleeve it".
    """
    entries = getattr(deck, "entries", None) or []
    needed: dict[str, int] = {}
    display: dict[str, str] = {}
    for entry in entries:
        name = getattr(entry, "card_name", "") or ""
        key = name.lower()
        if not key:
            continue
        needed[key] = needed.get(key, 0) + int(getattr(entry, "quantity", 1) or 0)
        display.setdefault(key, name)

    owned = store.owned_by_name()
    committed = committed_by_name(version_store, exclude_deck_id=deck_id) if version_store else {}

    rows = []
    owned_distinct = 0
    missing_copies = 0
    missing_distinct = 0
    blocked_distinct = 0

    for key, need in sorted(needed.items(), key=lambda kv: display[kv[0]].lower()):
        have = owned.get(key, 0)
        spoken_for = committed.get(key, 0)
        free = max(0, have - spoken_for)
        short = max(0, need - have)
        blocked = max(0, min(need, have) - free)

        if have >= need:
            owned_distinct += 1
        if short > 0:
            missing_copies += short
            missing_distinct += 1
        if blocked > 0:
            blocked_distinct += 1

        rows.append({
            "card_name": display[key],
            "needed": need,
            "owned": have,
            "committed_elsewhere": spoken_for,
            "available": free,
            "missing": short,
            "blocked": blocked,
        })

    return {
        "cards": rows,
        "distinct_cards": len(needed),
        "owned_distinct": owned_distinct,
        "missing_distinct": missing_distinct,
        "missing_copies": missing_copies,
        "blocked_distinct": blocked_distinct,
        "total_needed": sum(needed.values()),
        "total_owned": sum(min(owned.get(k, 0), v) for k, v in needed.items()),
    }
