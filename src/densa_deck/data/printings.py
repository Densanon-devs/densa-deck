"""Opt-in printing-level ingest from Scryfall's `default_cards` bulk file.

The main card ingest (`data/scryfall.py`) pulls `oracle_cards` — one row per
unique card, using an arbitrary representative printing. That is exactly right
for deck analysis, where "Sol Ring" is one card no matter which of its 131
printings you sleeve. It is useless for a physical collection, where the
Scars of Mirrodin copy and the Secret Lair foil are different objects with a
20x price difference.

This module fills `card_printings` from `default_cards` instead: every paper
printing in its default language.

Deliberately a *separate, opt-in* download, following the same rule as the
v0.6.0 rulings feature — not part of card data, never fetched automatically,
removable in one click. A user who only analyses decklists never pays for it.

Measured cost (2026-08-14): 73.9 MB gzipped download, 116,710 records of which
107,353 are paper, ~6s to parse + insert + index, ~35 MB on disk.

Prices ride along for free — the same bulk carries usd / usd_foil / usd_etched
per printing, so this is also the pricing feed. Per Scryfall's bulk-data docs,
those prices are refreshed once a day and are "dangerously stale after 24
hours"; `prices_synced_at` records when we took them so no surface has to
guess how old a number is.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from densa_deck.data.database import CardDatabase, printing_row_from_scryfall
from densa_deck.data.scryfall import (
    bulk_download_url,
    bulk_size_bytes,
    download_bulk_file,
    fetch_bulk_data_manifest,
    iter_bulk_records,
)

PRINTINGS_BULK_TYPE = "default_cards"

# Metadata keys on the shared `metadata` table in cards.db.
META_SYNCED_AT = "printings_synced_at"
META_BULK_UPDATED_AT = "printings_bulk_updated_at"
META_COUNT = "printings_count"

# Rows held in memory before flushing. The oracle ingest accumulates every
# parsed card in one list; at 107k printings that is a needless ~100 MB spike,
# so this path batches instead.
_FLUSH_EVERY = 20000

ProgressFn = Callable[[int, str], None]


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


async def fetch_printings_manifest() -> dict:
    """Scryfall manifest entry for the printings bulk file."""
    return await fetch_bulk_data_manifest(PRINTINGS_BULK_TYPE)


def load_printings_from_file(
    path: Path,
    db: CardDatabase,
    *,
    synced_at: str | None = None,
    progress: ProgressFn | None = None,
) -> int:
    """Stream a `default_cards` bulk file into `card_printings`.

    Returns the number of paper printings stored. Digital-only records are
    skipped by `printing_row_from_scryfall` — you cannot own an Arena card.
    """
    synced_at = synced_at or _now()
    batch: list[tuple] = []
    stored = 0
    seen = 0

    for raw in iter_bulk_records(path):
        seen += 1
        row = printing_row_from_scryfall(raw, synced_at)
        if row is None:
            continue
        batch.append(row)
        if len(batch) >= _FLUSH_EVERY:
            db.upsert_printings(batch)
            stored += len(batch)
            batch = []
            if progress:
                progress(stored, f"Stored {stored:,} printings...")

    if batch:
        db.upsert_printings(batch)
        stored += len(batch)

    db.set_metadata(META_SYNCED_AT, synced_at)
    db.set_metadata(META_COUNT, str(db.printing_count()))
    if progress:
        progress(stored, f"Stored {stored:,} printings from {seen:,} records.")
    return stored


async def ingest_printings(
    db: CardDatabase | None = None,
    *,
    force: bool = False,
    progress: ProgressFn | None = None,
) -> dict:
    """Download and ingest printing data.

    No-ops when printings are already present unless `force=True`, matching
    the card ingest's contract. `force` is also how a price refresh happens —
    the same file carries both, so re-running this is the price update.
    """
    if db is None:
        db = CardDatabase()

    existing = db.printing_count()
    if existing > 0 and not force:
        return {"ok": True, "skipped": True, "printings": existing}

    def emit(pct: int, msg: str):
        if progress:
            progress(pct, msg)

    emit(5, "Checking Scryfall for printing data...")
    manifest = await fetch_printings_manifest()
    url = bulk_download_url(manifest)
    size_mb = bulk_size_bytes(manifest) / (1024 * 1024)

    cache_dir = db.db_path.parent / "bulk"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / "default_cards.jsonl.gz"

    try:
        emit(10, f"Downloading printing data ({size_mb:.0f} MB)...")
        await download_bulk_file(url, dest)

        emit(45, "Reading printings...")
        synced_at = _now()
        stored = load_printings_from_file(
            dest,
            db,
            synced_at=synced_at,
            # Map the row counter onto 45-95% of the bar. 107k is the
            # measured paper-printing count; the clamp keeps the bar sane if
            # Scryfall's catalogue grows past it.
            progress=lambda n, msg: emit(min(95, 45 + int(n / 107000 * 50)), msg),
        )
        db.set_metadata(META_BULK_UPDATED_AT, str(manifest.get("updated_at", "")))
        emit(100, f"Stored {stored:,} printings.")
        return {
            "ok": True,
            "skipped": False,
            "printings": stored,
            "synced_at": synced_at,
            "bulk_updated_at": manifest.get("updated_at", ""),
        }
    finally:
        # The bulk file is a cache, not an asset — drop it so we aren't
        # sitting on 74 MB the user can't see or explain.
        dest.unlink(missing_ok=True)


def printings_status(db: CardDatabase) -> dict:
    """What the UI needs to describe the printings feed."""
    count = db.printing_count()
    synced_at = db.get_metadata(META_SYNCED_AT) or ""
    return {
        "printing_count": count,
        "ready": count > 0,
        "synced_at": synced_at,
        "bulk_updated_at": db.get_metadata(META_BULK_UPDATED_AT) or "",
        "price_age_hours": _age_hours(synced_at),
        "prices_stale": _is_stale(synced_at),
    }


def _age_hours(iso_ts: str) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - then).total_seconds() / 3600.0


def _is_stale(iso_ts: str) -> bool:
    """True once prices pass Scryfall's own 24-hour staleness line.

    Their bulk docs call prices "dangerously stale after 24 hours" and say the
    feed is not fit to power a sales system. We can't make the data fresher,
    but we can refuse to present an old number as if it were current.
    """
    age = _age_hours(iso_ts)
    return age is None or age > 24.0


def remove_printings(db: CardDatabase) -> int:
    """Drop all printing rows. Returns how many were removed.

    The opt-in download needs a one-click undo, same as rulings. Collection
    data lives in a different database and is untouched by this.
    """
    conn = db.connect()
    count = db.printing_count()
    conn.execute("DELETE FROM card_printings")
    conn.commit()
    db.set_metadata(META_SYNCED_AT, "")
    db.set_metadata(META_COUNT, "0")
    return count
