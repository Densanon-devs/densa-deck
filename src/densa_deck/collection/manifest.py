"""Handing part of a collection to someone else, in a form they can read.

The engine could export a DECK five ways and could not export what you OWN at
all. That gap only shows up the moment you try to sell a thousand cards: the
manifest is the actual deliverable, and there was nothing to produce it.

Three formats, because the three readers are different people:

* **CSV** — the buyer, a spreadsheet, and every collection tool that imports.
  One row per stack, with the four things that decide what a card is worth:
  which printing, which finish, what condition, how many.
* **Decklist text** — a human, and every deckbuilding site. Loses finish and
  condition, which is fine: that reader is asking what cards these are, not
  what they grade at.
* **JSON** — another copy of this app, or anything scripted. Lossless.

Prices are the SNAPSHOT at export, and every format says so. A manifest is a
document about a moment — quoting it later as if it were current is how a
disagreement about money starts.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone


CSV_COLUMNS = [
    "card_name",
    "set_code",
    "collector_number",
    "finish",
    "condition",
    "language",
    "quantity",
    "unit_price_usd",
    "line_price_usd",
    "printing_id",
]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def collect_rows(store, card_db, *, collection_uid: str | None = None,
                 limit: int = 20000) -> tuple[list[dict], dict]:
    """The stacks to export, priced, and the totals that go with them.

    `collection_uid` omitted means the whole collection; passed, it means one
    group — which is what turns this from a backup into a bundle manifest.

    The limit is high rather than absent because this builds a list in memory
    and a runaway is worse than a truncation you can see; 20,000 stacks is far
    past any real collection.
    """
    from densa_deck.collection.query import search_collection

    collection_id = None
    name = "Everything I own"
    if collection_uid:
        found = store.collection_by_uid(collection_uid)
        if not found:
            raise ValueError("No such collection.")
        collection_id = found["collection_id"]
        name = found.get("name") or name

    items, total, totals = search_collection(
        store, card_db, collection_id=collection_id,
        sort="value_desc", limit=limit, offset=0)

    # How many of each stack this group is actually claiming — NOT how many
    # the stack holds. A bundle taking two of the four Bolts you own must
    # promise two; a manifest listing four is a manifest the buyer will count
    # against the box, and it will be short.
    #
    # Whole collection means whole stacks, which is what a backup should say.
    claimed = (store.membership_quantities(collection_id)
               if collection_id is not None else {})

    rows = []
    counted = 0
    for item in items:
        unit = getattr(item, "unit_price_usd", None)
        quantity = claimed.get(item.item_id) or item.quantity
        counted += quantity
        rows.append({
            "card_name": item.card_name,
            "set_code": (getattr(item, "set_code", "") or "").upper(),
            "collector_number": getattr(item, "collector_number", "") or "",
            "finish": (item.finish.value if hasattr(item.finish, "value")
                       else str(item.finish)),
            "condition": (item.condition.value if hasattr(item.condition, "value")
                          else str(item.condition)),
            "language": item.language,
            "quantity": quantity,
            "unit_price_usd": round(unit, 2) if unit is not None else None,
            "line_price_usd": (round(unit * quantity, 2)
                               if unit is not None else None),
            "printing_id": item.printing_id,
        })

    meta = {
        "name": name,
        "collection_uid": collection_uid or "",
        "stacks": total,
        "truncated": total > len(rows),
        "copies": counted,
        "value_usd": round(sum(r["line_price_usd"] or 0.0 for r in rows), 2),
        "unpriced_stacks": totals.get("unpriced_stacks", 0),
        "exported_at": _now(),
    }
    return rows, meta


def to_csv(rows: list[dict], meta: dict) -> str:
    """One row per stack, and a header a spreadsheet will accept.

    No preamble above the header, deliberately. A commented banner is friendly
    to a person and breaks every importer that has ever been written, and this
    file exists to be imported. The provenance goes in a `# ` block AFTER the
    data, where a parser that stops at the last well-formed row never sees it
    and a human scrolling to the bottom does.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS,
                            extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            **row,
            # An empty cell, not a zero. A spreadsheet SUMs a zero and quietly
            # under-reports the total; an empty cell is visibly unknown.
            "unit_price_usd": ("" if row["unit_price_usd"] is None
                               else row["unit_price_usd"]),
            "line_price_usd": ("" if row["line_price_usd"] is None
                               else row["line_price_usd"]),
        })
    buffer.write(
        f"\n# {meta['name']}: {meta['copies']} cards in {meta['stacks']} stacks"
        f"\n# Prices are a snapshot taken {meta['exported_at']}, not a quote."
    )
    if meta.get("unpriced_stacks"):
        buffer.write(
            f"\n# {meta['unpriced_stacks']} stacks had no price and are"
            f" NOT included in the ${meta['value_usd']:.2f} total."
        )
    return buffer.getvalue()


def to_decklist(rows: list[dict], meta: dict) -> str:
    """`4 Lightning Bolt (LEA) 161`, the form every site imports.

    Stacks of one printing that differ only by finish or condition collapse
    into one line, because this format cannot say what separates them and a
    reader would see the same card listed twice with no explanation.
    """
    merged: dict[tuple, int] = {}
    order: list[tuple] = []
    for row in rows:
        key = (row["card_name"], row["set_code"], row["collector_number"])
        if key not in merged:
            order.append(key)
        merged[key] = merged.get(key, 0) + row["quantity"]

    lines = [f"// {meta['name']} — {meta['copies']} cards, exported {meta['exported_at']}"]
    for key in order:
        name, set_code, number = key
        suffix = f" ({set_code})" + (f" {number}" if number else "") if set_code else ""
        lines.append(f"{merged[key]} {name}{suffix}")
    return "\n".join(lines) + "\n"


def to_json(rows: list[dict], meta: dict) -> str:
    """Lossless, for another copy of this app or anything scripted."""
    return json.dumps({"manifest": meta, "cards": rows}, indent=2) + "\n"


FORMATS = {"csv": to_csv, "decklist": to_decklist, "json": to_json}


def export_manifest(store, card_db, *, collection_uid: str | None = None,
                    fmt: str = "csv") -> tuple[str, dict]:
    """Render one group, or everything, in one of the three formats."""
    if fmt not in FORMATS:
        raise ValueError(
            f"Unknown format {fmt!r}. Try one of: {', '.join(sorted(FORMATS))}.")
    rows, meta = collect_rows(store, card_db, collection_uid=collection_uid)
    return FORMATS[fmt](rows, meta), meta


def suggested_filename(meta: dict, fmt: str) -> str:
    """A name that says what it is without being opened.

    Slugified hard: this lands in a Downloads folder next to other people's
    files, and a manifest called `export.csv` is one nobody can identify a
    week later.
    """
    import re
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", (meta.get("name") or "collection")).strip("-")
    day = (meta.get("exported_at") or "")[:10] or "undated"
    ext = {"csv": "csv", "decklist": "txt", "json": "json"}[fmt]
    return f"{stem or 'collection'}-{day}.{ext}"
