"""Deck import and parsing from multiple formats."""

from __future__ import annotations

import re

from densa_deck.models import DeckEntry, Zone

# Regex patterns for decklist parsing
# Matches: "1 Lightning Bolt", "1x Lightning Bolt", "Lightning Bolt"
_QTY_NAME = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.+?)\s*$")
# Name-only: anything non-empty that isn't a quantity-prefixed line. The ASCII-only [A-Za-z]
# anchor used to live here but blocked Unicode card names like "Æther Vial" and
# "Lim-Dûl's Vault" — exporters that preserve diacritics (some Moxfield exports) would
# silently drop those rows.
_NAME_ONLY = re.compile(r"^\s*(\S.*?)\s*$")

# The set code and collector number an exporter appends: "(M21) 199", "(NEO)".
# The number is optional because plenty of exporters emit the set alone.
_SET_SUFFIX = re.compile(r"\s*\(([A-Za-z0-9]+)\)\s*([A-Za-z0-9★-]+)?\s*$")
# The other spelling, "[ELD]", which carries a set and never a number.
_BRACKET_SUFFIX = re.compile(r"\s*\[([^\]]*)\]\s*$")

# Section headers
_SECTION_PATTERNS = {
    Zone.COMMANDER: re.compile(r"^(commander|cmdr)\s*:?\s*$", re.IGNORECASE),
    Zone.COMPANION: re.compile(r"^companion\s*:?\s*$", re.IGNORECASE),
    Zone.SIDEBOARD: re.compile(r"^(sideboard|sb|side)\s*:?\s*$", re.IGNORECASE),
    Zone.MAYBEBOARD: re.compile(r"^(maybeboard|maybe|considering)\s*:?\s*$", re.IGNORECASE),
    Zone.MAINBOARD: re.compile(r"^(mainboard|main|deck|mainlist)\s*:?\s*$", re.IGNORECASE),
}


def parse_decklist(text: str) -> list[DeckEntry]:
    """Parse a plain-text decklist into DeckEntry objects.

    Supports formats:
    - Plain text: "4 Lightning Bolt"
    - With quantity marker: "4x Lightning Bolt"
    - Moxfield/Archidekt exports with section headers
    - Card names alone (quantity defaults to 1)
    - Set codes in parens: "4 Lightning Bolt (M21) 199" (kept on the entry,
      off the name — a slot that names a printing means that exact card)
    - Category tags: "1 Sol Ring #ramp #mana"
    """
    entries: list[DeckEntry] = []
    current_zone = Zone.MAINBOARD
    seen_blank = False

    for raw_line in text.strip().splitlines():
        line = raw_line.strip()

        # Skip empty lines (but track for sideboard heuristic)
        if not line:
            seen_blank = True
            continue

        # Skip comment lines
        if line.startswith("//") or line.startswith("#"):
            continue

        # Check for section headers
        matched_section = False
        for zone, pattern in _SECTION_PATTERNS.items():
            if pattern.match(line):
                current_zone = zone
                matched_section = True
                break
        if matched_section:
            continue

        # Moxfield-style "SIDEBOARD:" prefix inline
        zone_override = current_zone
        for zone, pattern in _SECTION_PATTERNS.items():
            prefix_match = re.match(rf"^{pattern.pattern.strip('^$')}\s*", line, re.IGNORECASE)
            if prefix_match:
                zone_override = zone
                line = line[prefix_match.end() :].strip()
                break

        # Extract custom tags (e.g. #ramp #draw)
        custom_tags: list[str] = []
        tag_matches = re.findall(r"#(\w+)", line)
        if tag_matches:
            custom_tags = tag_matches
            line = re.sub(r"\s*#\w+", "", line).strip()

        # Strip the trailing star some exporters use for foil, before the set
        # code is read — "(M21) 199 *F*" would otherwise hide the number.
        line = re.sub(r"\s*\*F\*\s*$", "", line).strip()

        # Take the set code and collector number OFF the name — "Sol Ring
        # (M21) 199" is not a card called that — but KEEP them. They used to
        # be discarded, which is what made a printing-level list impossible to
        # import: the pair in the bottom-left corner of a card is the only
        # form of "which exact printing" that survives a plain text file.
        #
        # Everything downstream still reads `card_name` and is unaffected;
        # legality, combos and goldfishing are facts about cards, not
        # printings. Some exporters lowercase the set code, so accept either.
        set_code = ""
        collector_number = ""
        parens = _SET_SUFFIX.search(line)
        if parens:
            set_code = parens.group(1)
            collector_number = (parens.group(2) or "").strip()
            line = line[: parens.start()].strip()
        else:
            bracket = _BRACKET_SUFFIX.search(line)
            if bracket:
                set_code = (bracket.group(1) or "").strip()
                line = line[: bracket.start()].strip()

        # Try quantity + name
        m = _QTY_NAME.match(line)
        if m:
            qty = int(m.group(1))
            name = m.group(2).strip()
        else:
            # Try name only
            m = _NAME_ONLY.match(line)
            if m:
                qty = 1
                name = m.group(1).strip()
            else:
                continue  # Unparseable line

        if not name:
            continue

        # Heuristic: in formats without section headers, a blank line
        # before remaining entries often means sideboard
        if seen_blank and current_zone == Zone.MAINBOARD and zone_override == Zone.MAINBOARD:
            # Only apply blank-line sideboard heuristic if no explicit sections used
            if not any(
                any(p.search(line) for p in _SECTION_PATTERNS.values())
                for line in text.strip().splitlines()
                if line.strip()
            ):
                zone_override = Zone.SIDEBOARD

        entries.append(
            DeckEntry(
                card_name=name,
                quantity=qty,
                zone=zone_override,
                custom_tags=custom_tags,
                set_code=set_code,
                collector_number=collector_number,
            )
        )

    return entries


def parse_csv(text: str) -> list[DeckEntry]:
    """Parse CSV-format decklist (quantity,name,zone).

    Uses Python's csv module to correctly handle quoted fields
    (e.g. card names with commas like "Jace, the Mind Sculptor").
    """
    import csv
    import io

    entries: list[DeckEntry] = []
    reader = csv.reader(io.StringIO(text.strip()))
    for row in reader:
        if not row:
            continue
        # Skip header row
        first = row[0].strip().lower()
        if first in ("quantity", "qty", "count"):
            continue
        if len(row) < 2:
            continue
        try:
            qty = int(row[0].strip())
        except ValueError:
            continue
        name = row[1].strip()
        if not name:
            continue
        zone = Zone.MAINBOARD
        if len(row) >= 3:
            zone_str = row[2].strip().lower()
            for z in Zone:
                if z.value == zone_str:
                    zone = z
                    break
        entries.append(DeckEntry(card_name=name, quantity=qty, zone=zone))
    return entries


def detect_format(text: str) -> str:
    """Detect if input is plain text or CSV."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    csv_score = sum(1 for line in lines[:10] if "," in line)
    if csv_score > len(lines[:10]) * 0.5:
        return "csv"
    return "text"


def parse_auto(text: str) -> list[DeckEntry]:
    """Auto-detect format and parse."""
    fmt = detect_format(text)
    if fmt == "csv":
        return parse_csv(text)
    return parse_decklist(text)
