"""Scryfall bulk data ingestion pipeline."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import IO, Iterator

import httpx
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from densa_deck.data.database import CardDatabase
from densa_deck.models import Card, CardFace, CardLayout, Color, Legality

console = Console()

SCRYFALL_BULK_API = "https://api.scryfall.com/bulk-data"
BULK_TYPE = "oracle_cards"  # One entry per unique card (no reprints)


def bulk_download_url(manifest: dict) -> str:
    """Pull the bulk-file URL out of a Scryfall manifest entry.

    Scryfall renamed this field from `download_uri` to `jsonl_download_uri`
    when they switched the bulk payload from a JSON array to gzipped JSON
    Lines. We accept either, newest name last-resort first, so the ingest
    works against both the old and the current API shape.

    Raises KeyError with an explicit message if neither is present — a
    loud failure here is far better than the silent one we shipped.
    """
    for key in ("download_uri", "jsonl_download_uri"):
        url = manifest.get(key)
        if url:
            return str(url)
    raise KeyError(
        "Scryfall bulk manifest has neither 'download_uri' nor "
        f"'jsonl_download_uri' (keys present: {sorted(manifest)})"
    )


def bulk_size_bytes(manifest: dict) -> int:
    """Bulk-file size in bytes, tolerating Scryfall's `size` ->
    `compressed_size` rename. Returns 0 when neither is present."""
    for key in ("size", "compressed_size"):
        value = manifest.get(key)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


async def fetch_bulk_data_url() -> str:
    """Backwards-compatible accessor that only returns the download URL.

    Prefer `fetch_bulk_data_manifest` when you also need the `updated_at`
    timestamp for update-check comparisons.
    """
    entry = await fetch_bulk_data_manifest()
    return bulk_download_url(entry)


async def fetch_bulk_data_manifest() -> dict:
    """Return the full Scryfall bulk-data manifest entry for oracle_cards.

    Read the URL and size out of the returned entry with
    `bulk_download_url()` / `bulk_size_bytes()` rather than indexing it
    directly — Scryfall has renamed both fields once already.

    The `updated_at` timestamp is what the "is my local card DB out of
    date?" check compares against — Scryfall regenerates the bulk file
    whenever new cards are added to their Oracle, which is typically once
    per set release but can happen between sets for errata.

    30-second timeout so a hung Scryfall API doesn't stall an ingest
    thread forever.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(SCRYFALL_BULK_API, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        for item in data["data"]:
            if item["type"] == BULK_TYPE:
                return item
    raise RuntimeError(f"Could not find bulk data type '{BULK_TYPE}' in Scryfall API response")


async def download_bulk_file(url: str, dest: Path) -> Path:
    """Stream-download the bulk JSON file with atomic write."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=300) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                ) as progress:
                    task = progress.add_task("Downloading Scryfall data...", total=total or None)
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))
        # Atomic rename on success
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def parse_scryfall_card(raw: dict) -> Card | None:
    """Convert a raw Scryfall JSON object into our Card model."""
    # Skip tokens, emblems, art series, etc.
    layout_str = raw.get("layout", "normal")
    try:
        layout = CardLayout(layout_str)
    except ValueError:
        return None

    if layout in (
        CardLayout.TOKEN,
        CardLayout.DOUBLE_FACED_TOKEN,
        CardLayout.EMBLEM,
        CardLayout.ART_SERIES,
        CardLayout.PLANAR,
        CardLayout.SCHEME,
        CardLayout.VANGUARD,
    ):
        return None

    # Parse faces
    faces: list[CardFace] = []
    if "card_faces" in raw:
        for face_raw in raw["card_faces"]:
            faces.append(
                CardFace(
                    name=face_raw.get("name", ""),
                    mana_cost=face_raw.get("mana_cost", ""),
                    cmc=face_raw.get("cmc", raw.get("cmc", 0.0)),
                    type_line=face_raw.get("type_line", ""),
                    oracle_text=face_raw.get("oracle_text", ""),
                    power=face_raw.get("power"),
                    toughness=face_raw.get("toughness"),
                    loyalty=face_raw.get("loyalty"),
                    colors=[Color(c) for c in face_raw.get("colors", [])],
                    color_indicator=[Color(c) for c in face_raw.get("color_indicator", [])],
                    produced_mana=face_raw.get("produced_mana", []),
                )
            )

    # Parse legalities
    legalities = {}
    for fmt, status in raw.get("legalities", {}).items():
        try:
            legalities[fmt] = Legality(status)
        except ValueError:
            pass

    type_line = raw.get("type_line", "")
    # Scryfall type lines are "supertypes types — subtypes" (em dash U+2014). Subtype tokens
    # can be arbitrary creature types — e.g. Mistform Island is "Creature — Illusion Island",
    # which would false-positive on a naive `'land' in type_line`. Split on the em dash and
    # match only against the pre-dash portion so subtype names can't leak into type flags.
    types_part = type_line.split(" — ")[0].lower()

    return Card(
        scryfall_id=raw["id"],
        oracle_id=raw.get("oracle_id", raw["id"]),
        name=raw.get("name", "Unknown"),
        layout=layout,
        cmc=raw.get("cmc", 0.0),
        mana_cost=raw.get("mana_cost", ""),
        type_line=type_line,
        oracle_text=raw.get("oracle_text", ""),
        colors=[Color(c) for c in raw.get("colors", [])],
        color_identity=[Color(c) for c in raw.get("color_identity", [])],
        produced_mana=raw.get("produced_mana", []),
        keywords=raw.get("keywords", []),
        legalities=legalities,
        faces=faces,
        power=raw.get("power"),
        toughness=raw.get("toughness"),
        loyalty=raw.get("loyalty"),
        rarity=raw.get("rarity", ""),
        set_code=raw.get("set", ""),
        price_usd=_parse_price(raw.get("prices", {})),
        is_land="land" in types_part,
        is_creature="creature" in types_part,
        is_instant="instant" in types_part,
        is_sorcery="sorcery" in types_part,
        is_artifact="artifact" in types_part,
        is_enchantment="enchantment" in types_part,
        is_planeswalker="planeswalker" in types_part,
        is_battle="battle" in types_part,
    )


def _parse_price(prices: dict) -> float | None:
    """Extract the USD market price from Scryfall's prices block.

    Scryfall returns prices as strings (e.g. "1.25") or null. We prefer
    paper `usd`, fall back to `usd_foil`, and return None if neither is
    populated — null means "unknown" rather than "free" so the downstream
    budget filter can treat NULL as "don't exclude" instead of "free card".
    """
    if not prices:
        return None
    for key in ("usd", "usd_foil", "usd_etched"):
        val = prices.get(key)
        if val:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _open_bulk(path: Path) -> IO[str]:
    """Open a bulk file as text, transparently decompressing gzip.

    Sniffed from the magic bytes rather than the filename so a Scryfall
    change to either the extension or the encoding keeps working.
    """
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _first_significant_char(path: Path) -> str:
    """First non-whitespace character of the bulk file ('[' => JSON array)."""
    with _open_bulk(path) as f:
        while True:
            ch = f.read(1)
            if not ch:
                return ""
            if not ch.isspace():
                return ch


def iter_bulk_records(path: Path) -> Iterator[dict]:
    """Yield raw records from any Scryfall bulk file.

    Shared by the card ingest and the opt-in rulings download — both are
    the same gzipped-JSONL-or-JSON-array shape.

    Handles both payload shapes Scryfall has shipped — a single JSON array
    and JSON Lines (one object per line) — gzipped or plain, detected by
    sniffing rather than assumed. JSON Lines is streamed a line at a time,
    which also keeps the ~500 MB oracle dump off the heap.
    """
    if _first_significant_char(path) == "[":
        with _open_bulk(path) as f:
            for raw in json.load(f):
                yield raw
        return

    with _open_bulk(path) as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line or line in ("[", "]"):
                continue
            yield json.loads(line)


# Kept as the card-ingest-facing name; the implementation is format-generic.
iter_raw_cards = iter_bulk_records


def load_bulk_file(path: Path) -> list[Card]:
    """Parse the downloaded bulk file into Card objects."""
    cards: list[Card] = []
    console.print(f"[cyan]Parsing {path.name}...[/cyan]")
    skipped = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}"),
    ) as progress:
        # Total is unknown up front when streaming JSON Lines, so the bar
        # runs indeterminate and reports a running count instead.
        task = progress.add_task("Parsing cards...", total=None)
        for raw in iter_raw_cards(path):
            card = parse_scryfall_card(raw)
            if card:
                cards.append(card)
            else:
                skipped += 1
            progress.update(task, advance=1)
    console.print(f"[green]Parsed {len(cards)} cards[/green] ({skipped} skipped)")
    return cards


async def ingest(db: CardDatabase | None = None, force: bool = False):
    """Full ingestion pipeline: download bulk data, parse, store."""
    if db is None:
        db = CardDatabase()

    existing = db.card_count()
    if existing > 0 and not force:
        console.print(
            f"[yellow]Database already has {existing} cards. Use --force to re-download.[/yellow]"
        )
        return

    console.print("[bold cyan]Starting Scryfall data ingestion...[/bold cyan]")

    cache_dir = db.db_path.parent / "bulk"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Bound before the try: the filename depends on what Scryfall serves,
    # but the finally-block cleanup must not NameError if we fail earlier.
    dest: Path | None = None

    try:
        # Get the full manifest (not just the URL) so we can record the
        # Scryfall-side `updated_at` timestamp alongside the ingest. The
        # desktop app's on-launch update check (api.check_card_db_update)
        # compares this stored timestamp against the live remote one to
        # decide whether to show the "update available" banner — without
        # writing it here, every CLI-ingested user would see a phantom
        # update banner the next time they opened the app.
        manifest = await fetch_bulk_data_manifest()
        url = bulk_download_url(manifest)
        remote_updated_at = manifest.get("updated_at", "")
        console.print(f"[dim]Bulk data URL: {url}[/dim]")

        # Name the cache file after what Scryfall actually served so the
        # gzip/JSONL sniffing has a sane filename to report in errors.
        suffix = ".jsonl.gz" if url.endswith(".gz") else ".json"
        dest = cache_dir / f"oracle_cards{suffix}"

        # Download
        await download_bulk_file(url, dest)

        # Parse
        cards = load_bulk_file(dest)

        # Store
        console.print("[cyan]Storing cards in database...[/cyan]")
        db.upsert_cards(cards)
        db.set_metadata("last_ingest", str(len(cards)))
        # Match the desktop-app ingest path so on-launch update checks
        # behave the same regardless of which flow populated the DB.
        if remote_updated_at:
            db.set_metadata("scryfall_bulk_updated_at", remote_updated_at)
        from datetime import datetime as _dt
        db.set_metadata(
            "last_ingest_completed_at",
            _dt.now().isoformat(timespec="seconds"),
        )
        console.print(f"[bold green]Done! {len(cards)} cards stored.[/bold green]")
    except httpx.HTTPError as e:
        console.print(f"[bold red]Network error during ingestion: {e}[/bold red]")
        console.print("[yellow]Check your internet connection or try again later.[/yellow]")
        raise SystemExit(1)
    except (json.JSONDecodeError, KeyError) as e:
        console.print(f"[bold red]Failed to parse card data: {e}[/bold red]")
        raise SystemExit(1)
    finally:
        # Always clean up bulk file (may be unset if we failed before the
        # manifest told us what filename Scryfall was serving).
        if dest is not None:
            dest.unlink(missing_ok=True)
