"""Card identification from OCR text.

The approach, and why it isn't image matching:

Every Magic card printed since Magic 2015 carries its own collector number
and set code in the bottom-left corner, like

    0079/0249 M
    SOM • EN   Jason Chan

Read those two fields and you have the *exact* printing in one indexed
lookup — no reference images, no perceptual hashes, no 107,000-image corpus
to download and maintain. That is the fast path and it covers most of what
people scan.

Older cards (pre-2015) have no collector number on the face. For those we
fall back to the card *name*, which narrows to an oracle card, and then
disambiguate among that card's printings — usually fewer than 150, often
fewer than 10. Only that last step needs images, and only for a handful of
candidates fetched on demand.

This module is deliberately pure: text in, candidates out. Camera capture and
the OCR engine live behind `ScanBackend` so they can be optional — bundling
OpenCV would add ~50 MB to a 107 MB installer for a feature most users never
touch, and Windows already ships an OCR engine.

**Nothing is ever added silently.** A wrong card in your inventory is worse
than no card, because you won't know to look for it. Low-confidence reads ask
rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

# Confidence tiers drive the UI's behaviour, not just its wording.
CONFIDENCE_EXACT = "exact"        # set + number matched a printing -> auto-add
CONFIDENCE_LIKELY = "likely"      # name matched, one obvious printing -> auto-add
CONFIDENCE_AMBIGUOUS = "ambiguous"  # name matched, several printings -> ask
CONFIDENCE_UNKNOWN = "unknown"    # nothing matched -> manual entry

# How many printings a name match may offer. This was 25, which is fewer than
# the number of times Magic has printed its staples: Arcane Signet has 88
# printings and Sol Ring 131, so the copy actually in someone's hand was
# routinely absent from the list with nothing to say it had been cut. The list
# is a menu the user picks from, so truncating it is not a display detail —
# it makes the right answer unreachable. Set above the most-reprinted card in
# the catalogue; the UI is responsible for presenting a long list usably.
MAX_CANDIDATES = 250

# Collector number then an optional rarity letter, e.g. "0079/0249 M",
# "79/249", "123a", "★123". Scryfall stores the number without the total, so
# we keep only the left half.
# The left side is deliberately wider than "digits". Real collector numbers
# include "90", "90a", "★90", "WWK-90" (The List), "et208" (promos), "js0b"
# (World Championship decks) and "A25-181". Restricting to digits pushed all
# of those onto the name-matching fallback, which is exactly where wrong
# guesses come from.
_COLLECTOR_RE = re.compile(
    r"(?<![\w/])([A-Z0-9]{1,6}(?:-[A-Z0-9]{1,6})?[a-z★]?)\s*/\s*\d{1,4}(?![\d/])",
    re.IGNORECASE,
)
_COLLECTOR_BARE_RE = re.compile(r"(?<![\w/])(\d{1,4}[a-z★]?)\s+[CURMLST](?![\w])")

# Set code: 3-6 uppercase alphanumerics, usually followed by a bullet and the
# language. Anchoring on that separator avoids matching random uppercase runs
# in the rules text.
# Separators seen between the set code and the language on real cards. The
# star forms matter: a promo/foil prints "DTK ★ EN", and omitting the star
# meant every promo failed to yield a set code and fell back to guessing by
# name — which is exactly the sort of card people scan for its value.
# OCR also routinely renders these glyphs as an asterisk, a bullet, or a
# dot, so accept the whole family.
_SEPARATORS = r"[•·∙\*★☆✦✧◆◇★☆◆◇·•\.]"

# Languages actually printed on Magic cards. Anchoring the set-code match on a
# closed list is what lets the separator be optional: OCR frequently drops the
# bullet entirely and returns "DTK EN", and requiring a separator meant those
# reads produced no set code at all and fell through to guessing by name.
# Matching "<3-6 chars> <known language>" instead is both looser about the
# glyph and stricter about what counts as a hit.
_LANGUAGES = ("EN", "DE", "FR", "IT", "ES", "PT", "JA", "JP", "KO", "KR",
              "RU", "ZH", "CS", "CT", "CN", "PH", "LA", "AR", "HE")
_LANG_ALT = "|".join(_LANGUAGES)
# Case-insensitive because OCR mangles case in small print: a real photo of
# this card returned "DtKtEN" for "DTK ★ EN". Matching only uppercase meant
# that line yielded nothing at all. The cost of being loose here is a few
# junk set codes, and those are free to reject — every candidate is checked
# against the catalogue before it can identify anything.
_SET_LANG_RE = re.compile(
    rf"\b([0-9A-Z]{{3,6}})\s*(?:{_SEPARATORS}\s*)?({_LANG_ALT})\b", re.IGNORECASE)
_SET_ONLY_RE = re.compile(rf"\b([A-Z0-9]{{3,6}})\s*{_SEPARATORS}")

# OCR routinely confuses these inside otherwise-numeric fields.
_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"})


# On frames of roughly this era the glyph between the set code and the
# language says whether the card is FOIL: a star ("DTK ★ EN") for foil, a
# bullet ("DTK • EN") for non-foil. That matters because foil and non-foil of
# one printing are separately priced, often by a large multiple.
#
# Measured against Windows OCR, the star NEVER comes back as "★". On a clean
# render it vanishes outright; on a warped camera crop it comes back as "*".
# The bullet, by contrast, survives as "•" reliably. So the star is matched
# through its OCR stand-ins, and a bullet anywhere in the text VETOES the foil
# reading — a misread bullet is the only way this produces a false foil, and
# a false foil silently overvalues someone's collection.
_FOIL_STAR_RE = re.compile(
    rf"\b[0-9A-Z]{{3,6}}\s*[★☆✦✧\*]\s*(?:{_LANG_ALT})\b")
_NONFOIL_BULLET_RE = re.compile(
    rf"\b[0-9A-Z]{{3,6}}\s*[•·∙]\s*(?:{_LANG_ALT})\b")


@dataclass
class CardIdentity:
    """What we managed to read off a card face."""

    collector_number: str = ""
    set_code: str = ""
    language: str = ""
    name: str = ""
    raw_text: str = ""
    # True when the footer carried the foil star. None means "no opinion" —
    # absence of a star is not proof of non-foil on every frame era, so this
    # only ever upgrades a guess, never overrides the user.
    foil_hint: bool = False

    @property
    def has_exact_key(self) -> bool:
        return bool(self.collector_number and self.set_code)


@dataclass
class ScanCandidate:
    printing: dict
    confidence: str
    reason: str
    score: float = 0.0


@dataclass
class ScanResult:
    identity: CardIdentity
    candidates: list[ScanCandidate] = field(default_factory=list)
    confidence: str = CONFIDENCE_UNKNOWN

    @property
    def suggested_finish(self) -> str:
        """Finish to preselect, from the foil star in the footer.

        Only ever suggests foil when the printing was actually made in foil -
        a misread star must not record a finish that never existed.
        """
        if not self.identity.foil_hint:
            return "nonfoil"
        best = self.best or {}
        finishes = [f for f in (best.get("finishes") or "").split(",") if f]
        return "foil" if (not finishes or "foil" in finishes) else "nonfoil"

    @property
    def best(self) -> dict | None:
        return self.candidates[0].printing if self.candidates else None

    @property
    def auto_addable(self) -> bool:
        """Whether this may be added without asking.

        Deliberately narrow. Everything else routes through a human, because
        silently filing the wrong card is the one failure mode that corrupts
        inventory in a way the user cannot see.
        """
        return self.confidence in (CONFIDENCE_EXACT, CONFIDENCE_LIKELY)


class ScanBackend(Protocol):
    """Capture + OCR. Implemented outside this module so it stays optional."""

    name: str

    def available(self) -> bool:
        ...

    def read_text(self, image) -> str:
        ...


def _normalise_number(raw: str) -> str:
    """Collector numbers as Scryfall stores them.

    Leading zeros go (Scryfall stores "79", cards print "0079") but a trailing
    letter stays — "123a" and "123b" are genuinely different printings.
    """
    raw = (raw or "").strip().lower()
    if not raw:
        return ""
    # Only strip leading zeros from purely numeric numbers. "0079" is 79, but
    # "js0b" and "a25-181" must survive intact.
    m = re.match(r"^0*(\d+)([a-z★]?)$", raw)
    if not m:
        return raw
    return m.group(1) + m.group(2)


# Glyphs OCR commonly returns in place of digits. Applied only when looking
# for a collector number, never to the whole text - "Sol Ring" must not
# become "5ol R1ng".
# D and Q are deliberately absent. They were here as 0-lookalikes, and they
# cost a real card: "MID-2I1" — a Modern Horizons collector number with one
# digit misread — repaired to "M10-211", which is a genuine PLST number for a
# different card entirely, and auto-added it. No card font renders 0 as D, so
# the mapping bought nothing and corrupted set-prefixed numbers.
_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0", "ø": "0", "Ø": "0",
    "I": "1", "l": "1", "|": "1", "!": "1",
    "S": "5", "s": "5", "B": "8", "Z": "2", "G": "6",
})


def _repair_digits(text: str) -> str:
    """Text with digit-lookalike glyphs normalised, for numeric matching only.

    Repairs run token by token, and only where the token already looks like a
    number. Translating the whole string flattened the alphabetic half of
    set-prefixed collector numbers: "MID-2I1" became "M1D-211" and stopped
    resolving, and with D once mapped to 0 it became "M10-211" — a real PLST
    number for a different card, which auto-added. Deciding per token keeps
    the fix where it belongs, on "O95" and "2I1", and off "MID" and "DTK".
    """
    out = []
    for token in re.split(r"([^0-9A-Za-zøØ|!]+)", text or ""):
        digits = sum(1 for ch in token if ch.isdigit())
        alnum = sum(1 for ch in token if ch.isalnum())
        # A token already half digits is a number OCR damaged; anything else
        # is a word, and words must survive intact.
        if alnum and digits * 2 >= alnum:
            token = token.translate(_DIGIT_LOOKALIKES)
        out.append(token)
    return "".join(out)


def parse_card_footer(text: str) -> CardIdentity:
    """Pull collector number / set code / language out of OCR text.

    Tolerant by design: OCR of a card corner is noisy, the text may arrive in
    any order, and any individual field may be missing. Anything unreadable
    comes back empty rather than guessed.
    """
    identity = CardIdentity(raw_text=text or "")
    if not text:
        return identity

    # Collector number: prefer the "123/264" form, which is unambiguous.
    # Search the repaired text too - a leading zero routinely comes back as
    # o/O/0-lookalike glyphs ("o95/264"), which silently failed to match.
    for candidate_text in (text, _repair_digits(text)):
        m = _COLLECTOR_RE.search(candidate_text)
        if m:
            identity.collector_number = _normalise_number(m.group(1))
            break
        m = _COLLECTOR_BARE_RE.search(candidate_text)
        if m:
            identity.collector_number = _normalise_number(m.group(1))
            break

    identity.foil_hint = bool(_FOIL_STAR_RE.search(text)
                              and not _NONFOIL_BULLET_RE.search(text))

    for m in _SET_LANG_RE.finditer(text):
        # Capitalisation is the guard against matching ordinary words; see
        # `_looks_like_set_code`. Skipping past a rejected match rather than
        # stopping matters because the real footer often follows the prose
        # that produced the false one.
        if _looks_like_set_code(m.group(1).strip()):
            identity.set_code = m.group(1).strip().lower()
            identity.language = m.group(2).strip().lower()
            break
    if not identity.set_code:
        m = _SET_ONLY_RE.search(text)
        if m:
            identity.set_code = m.group(1).strip().lower()

    return identity


def _candidate_names(text: str, limit: int = 6) -> list[str]:
    """Every line that could plausibly be a card name, best guess first.

    Picking one line and committing to it loses to OCR noise: a real read of
    `'n nz/s«\\nDeath Wind\\n095/264 U'` chose the garbage line, discarded a
    perfectly good "Death Wind", and reported the card unidentifiable.

    The card database is a far better arbiter than any heuristic, so offer it
    each plausible line and let a real match decide.
    """
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if len(line) < 3 or line.startswith("{"):
            continue
        letters = sum(1 for ch in line if ch.isalpha())
        if letters < 3:
            continue

        # A line far too long to be a card name is a whole-frame read: when
        # the outline isn't found we OCR the entire photo, and the engine
        # returns the name, the rules text, the flavour text and the footer
        # as one run. The name is the front of it, so offer the leading
        # phrases. Without this a photo that read "Death Wind" perfectly was
        # reported unidentifiable, because the line also held the collector
        # number and was discarded whole by the filters below.
        if len(line.split()) > _NAME_MAX_WORDS:
            out.extend(_leading_phrases(line))
            continue

        if _COLLECTOR_RE.search(line) or _COLLECTOR_BARE_RE.search(line):
            continue
        if _SET_LANG_RE.search(line) or _SET_ONLY_RE.search(line):
            continue
        if sum(1 for ch in line if ch.isdigit()) > letters:
            continue
        out.append(line)
        if len(out) >= limit:
            break
    # Longer candidates first: OCR noise is usually short fragments, whereas a
    # card name is a substantial run of letters. This ordering also keeps the
    # most specific phrase ahead of its own prefixes, so "Death Wind" is tried
    # before the bare "Death" — which is itself a real card.
    out.sort(key=lambda s: -sum(1 for ch in s if ch.isalpha()))
    return out[:_NAME_CANDIDATE_CAP]


# Card names run to about six words at the outside ("Kongming, 'Sleeping
# Dragon'" and friends), so a longer line is prose, not a name.
_NAME_MAX_WORDS = 6
_NAME_CANDIDATE_CAP = 14


def _leading_phrases(line: str) -> list[str]:
    """The opening one-to-six words of a line, longest first."""
    words = line.split()[:_NAME_MAX_WORDS]
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def _first_line_name(text: str) -> str:
    """Best guess at the card name — the first line that looks like a name.

    Card names sit at the top of the frame, so with whole-card OCR the first
    substantial line is usually it. But OCR output arrives in whatever order
    the engine emits, so a line only qualifies as a name if it actually looks
    like one: it must carry real letters and must not be the footer.

    Getting this wrong is not cosmetic. Treating "0079/0249 M" as a name made
    every exact footer read contradict itself and downgrade to "ambiguous",
    turning the fast path into a prompt.
    """
    for raw in (text or "").splitlines():
        line = raw.strip()
        if len(line) < 3 or line.startswith("{"):
            continue
        letters = sum(1 for ch in line if ch.isalpha())
        if letters < 3:
            continue  # "~~~", "0079/0249 M", stray symbols
        # The footer line: mostly digits and separators with a set code.
        if _COLLECTOR_RE.search(line) or _COLLECTOR_BARE_RE.search(line):
            continue
        if _SET_LANG_RE.search(line) or _SET_ONLY_RE.search(line):
            continue
        digits = sum(1 for ch in line if ch.isdigit())
        if digits > letters:
            continue
        return line
    return ""


def _footer_keys(text: str) -> list[tuple[str, str]]:
    """Every (set, number) pair the text plausibly contains, best first.

    The capture path now reads four crops in three renderings each, so the
    text handed here contains the real footer *and* a dozen lines of mirrored
    garbage from the wrong-orientation crops. Parsing the whole blob once and
    taking the first match let that garbage win: a real read produced
    "n 99Z/96o" above the correct "095/264 U DTK EN", and the card dropped
    from exact to guessing by name.

    So collect every candidate and let the database arbitrate, the same way
    name matching does. Single lines come first because a real footer key
    lives entirely on one line, whereas a whole-text parse will happily pair a
    number from one line with a set code from another.
    """
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    lines = (text or "").splitlines()
    for chunk in [*lines, text or ""]:
        numbers = _collector_numbers_in(chunk)
        if not numbers:
            continue
        # EVERY set-code match in the chunk, not just the first. When the card
        # outline isn't found the whole frame is OCR'd, so the text includes
        # flavour text — and "Silumgar" parses as the set code "SILUMG" in
        # the language "AR". That false match sits earlier in the prose than
        # the real "DTK*EN", so taking the first one threw the card away.
        for code in _set_codes_in(chunk, near=_collector_position(chunk)):
            for variant in _set_code_variants(code):
                for number in numbers:
                    key = (variant, number)
                    if key not in seen:
                        seen.add(key)
                        keys.append(key)
    return keys


def _looks_like_set_code(raw: str) -> bool:
    """Whether a token is capitalised the way a printed set code is.

    This is the guard that makes case-insensitive matching safe. Without it,
    ordinary English runs straight into the language list: "Invade the City"
    parses as the set "INVA" in German, which trims to "INV" — Invasion, a
    real set — and files a Pincer Spider under a card the user never owned.
    Measured on the catalogue gauntlet, that single hole produced 6 wrong
    auto-adds where the correct number is zero. "Raven's" -> RAV (Ravnica)
    and "Contract" -> CONTRA -> CON (Conflux) came through the same way.
    The database cannot arbitrate these: 411 of the 986 set codes are three
    letters, so a trimmed English word hits a real set routinely.

    Cards print the set code in capitals, so requiring the letters to be
    mostly uppercase separates "DTK" and OCR's mangled "DtKt" from "Invade"
    and "Silumg" without needing to know anything about English.
    """
    letters = [c for c in (raw or "") if c.isalpha()]
    if not letters:
        return bool(raw)
    upper = sum(1 for c in letters if c.isupper())
    return upper * 2 >= len(letters)


def _collector_numbers_in(text: str) -> list[str]:
    """Every collector number the text plausibly contains, in reading order.

    Taking only the first match loses to noise the same way taking only the
    first set code did. A real photo produced "9S/264" from one crop above a
    clean "095/264" from another; the first parses as "9s", which matches no
    printing, and the card fell back to guessing among four printings when
    exactly one of them is numbered 95.
    """
    numbers: list[str] = []
    for candidate in (text or "", _repair_digits(text or "")):
        for pattern in (_COLLECTOR_RE, _COLLECTOR_BARE_RE):
            for match in pattern.finditer(candidate):
                number = _normalise_number(match.group(1))
                if number and number not in numbers:
                    numbers.append(number)
    return numbers


def _collector_position(text: str) -> int:
    """Where the collector number sits in the text, or -1."""
    for candidate in (text or "", _repair_digits(text or "")):
        match = _COLLECTOR_RE.search(candidate) or _COLLECTOR_BARE_RE.search(candidate)
        if match:
            return match.start()
    return -1


def _set_codes_in(text: str, *, near: int = -1) -> list[str]:
    """Every token that could be a set code, nearest the collector number first.

    Ordering matters because the catalogue holds 986 set codes, so a token
    pulled out of flavour text can resolve to a real set by coincidence. On a
    real card the set code is printed directly beneath the collector number,
    which makes distance a genuine signal rather than a tiebreak: it puts the
    printed footer ahead of anything the rules text happens to spell.
    """
    matches = []
    for match in _SET_LANG_RE.finditer(text or ""):
        raw = match.group(1).strip()
        if raw and _looks_like_set_code(raw):
            matches.append((abs(match.start() - near) if near >= 0 else 0,
                            raw.lower()))
    matches.sort(key=lambda pair: pair[0])

    codes = []
    for _, code in matches:
        if code not in codes:
            codes.append(code)
    if not codes:
        match = _SET_ONLY_RE.search(text or "")
        if match:
            codes.append(match.group(1).strip().lower())
    return codes


def _set_code_variants(code: str) -> list[str]:
    """Spellings of one OCR'd set code to try, as-read first.

    OCR merges the separator into the code: a real photo of a foil DTK card
    read "DtKtEN", which parses as the set code "DTKT" — one character of
    debris away from the truth. Set codes are three characters for the great
    majority of Magic sets and four for most of the rest, so trimming back to
    those lengths recovers the code without inventing anything.

    Nothing here decides an identity on its own. Every spelling is looked up
    in the catalogue at the collector number that was read alongside it, and
    only a spelling that resolves to a real printing survives.
    """
    code = (code or "").strip().lower()
    if not code:
        return []
    variants = [code]
    for length in (5, 4, 3):
        if len(code) > length and code[:length] not in variants:
            variants.append(code[:length])
    return variants


def _conflicting_name(names, expected: str, card_db) -> str:
    """A name we read that is a real card, and isn't the expected one.

    Returns the offending name, or "" when nothing contradicts the printing.
    """
    for name in [n for n in (names or []) if n]:
        if _names_roughly_match(name, expected):
            return ""
        if card_db.printings_for_card(name):
            return name
    return ""


def identify_card(text: str, card_db, *, name_hint: str = "") -> ScanResult:
    """Turn OCR text into ranked printing candidates.

    Order of attack:
      1. set + collector number -> exact printing, one indexed lookup
      2. name -> that card's printings, ranked
      3. nothing -> unknown, hand it to the user
    """
    identity = parse_card_footer(text)
    identity.name = (name_hint or _first_line_name(text)).strip()
    result = ScanResult(identity=identity)

    # --- 1. exact key -------------------------------------------------
    hit = None
    for set_code, number in _footer_keys(text):
        hit = card_db.find_printing_by_set_number(set_code, number)
        if hit:
            identity.set_code, identity.collector_number = set_code, number
            break
    if hit:
        # If we also read the name of a DIFFERENT REAL CARD, the key is not
        # trustworthy enough to auto-add. Same rule as the CLI's set/number
        # guard: disagreement means ask, never guess.
        #
        # The veto turns on the name resolving in the catalogue, not merely
        # on it failing to match. Every capture now carries lines of OCR
        # debris from the mirrored crops — "Kdd!q-) n ne/S60" and the like —
        # and treating unrecognisable junk as disagreement meant noise could
        # veto a perfectly good read. Junk matches nothing; a real card name
        # matches something, and that is the case worth stopping for.
        conflict = _conflicting_name(
            [identity.name] if name_hint else _candidate_names(text),
            hit["name"], card_db)
        if conflict:
            result.candidates = [ScanCandidate(
                hit, CONFIDENCE_AMBIGUOUS,
                f"Read '{conflict}' but {identity.set_code.upper()} "
                f"#{identity.collector_number} is '{hit['name']}'", 0.5)]
            result.confidence = CONFIDENCE_AMBIGUOUS
            return result
        # The key alone is not enough to file a card without asking.
        #
        # A footer key is specific, which is why it was trusted outright. But
        # a single misread digit lands on a DIFFERENT REAL PRINTING, usually
        # in the same set, and the veto above cannot catch that: it only fires
        # when a name was read AND resolves in the catalogue. When the name is
        # unreadable — a foil, a glare, a crop that clipped it — there is
        # nothing contradicting the key, so a wrong key sailed through as
        # "exact" and filed the wrong card silently.
        #
        # So corroboration is now required, not merely the absence of
        # contradiction. If the card says its own name and that name agrees,
        # auto-add. If nothing on the card confirms the key, the printing is
        # still offered first — one tap, not retyping — but a human looks at
        # it. Nothing is lost except the guess.
        names = [identity.name] if name_hint else _candidate_names(text)
        corroborated = any(
            _names_roughly_match(n, hit["name"]) for n in names if n
        )
        if not corroborated:
            result.candidates = [ScanCandidate(
                hit, CONFIDENCE_AMBIGUOUS,
                f"{identity.set_code.upper()} #{identity.collector_number} "
                f"reads as '{hit['name']}', but the card's name could not be "
                f"read to confirm it", 0.6)]
            result.confidence = CONFIDENCE_AMBIGUOUS
            return result

        result.candidates = [ScanCandidate(
            hit, CONFIDENCE_EXACT,
            f"{hit['set_code'].upper()} #{hit['collector_number']} read from the card", 1.0)]
        result.confidence = CONFIDENCE_EXACT
        return result


    # --- 2. by name ---------------------------------------------------
    # Try every plausible line rather than trusting one heuristic pick. An
    # exact database hit on any line beats a fuzzy hit on the "best" one.
    name_options = [identity.name] if name_hint else _candidate_names(text)
    if identity.name and identity.name not in name_options:
        name_options.insert(0, identity.name)

    printings = []
    was_fuzzy = False
    for option in name_options:
        found = card_db.printings_for_card(option)
        if found:
            identity.name = option
            printings = found
            break
    # A card whose printed name is one FACE of a two-part card.
    #
    # Scryfall stores an adventure, split or transforming card under both
    # halves joined by `//` — "Velvetwing Butterflies // Gaze in Wonder" — but
    # the name printed at the top of the card, and therefore the only one OCR
    # can read, is the front face alone. `printings_for_card` matches the
    # stored name and finds nothing; `lookup_by_name` already knows how to
    # resolve a face, and this is simply asking it.
    #
    # 835 cards in the catalogue have a `//` name. Every one of them was
    # unscannable, which is most of "some cards aren't scanning".
    #
    # Exact resolution, not fuzzy: a face name is the card's real name, so
    # this is as trustworthy as matching the whole thing and stays eligible
    # for auto-add.
    if not printings:
        # Every line, not only the ones the name heuristic liked.
        #
        # `_candidate_names` is tuned to avoid mistaking rules text for a
        # name, and it discards plenty that IS one — measured against the
        # catalogue, it rejects "Flaxen Intruder" and "Tithing Blade"
        # outright. That is the right trade for a fuzzy match, where a bad
        # candidate becomes a wrong card. It is the wrong trade here, because
        # what follows is an EXACT catalogue lookup: a line that resolves to a
        # real card is a real card, and a line that does not costs nothing.
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        for option in [*name_options, *lines]:
            if not option:
                continue
            resolved = card_db.lookup_by_name(option)
            if resolved and resolved.name != option:
                found = card_db.printings_for_card(resolved.name)
                if found:
                    identity.name = resolved.name
                    printings = found
                    break

    if not printings:
        for option in name_options:
            match = _fuzzy_card_name(option, card_db)
            if match:
                identity.name = match
                printings = card_db.printings_for_card(match)
                was_fuzzy = True
                break

    if printings:
        # A set code we read but couldn't pair with a number still
        # narrows the field enormously.
        if identity.set_code:
            scoped = [p for p in printings if p["set_code"] == identity.set_code]
            if scoped:
                printings = scoped

        # So does a collector number without a set code, and that combination
        # is common: a real photo read "Death Wind" and "095/264 u DTK" with
        # no language after the set code, so the code was discarded — yet the
        # name and the number together name exactly one printing. Confirming
        # the number against a name we already resolved is a narrowing, not a
        # guess, so it can leave the result auto-addable.
        #
        # Every candidate number is tried, because the damaged one often comes
        # first: the same photo yielded "9s" from a smeared crop ahead of a
        # clean "95". A number that matches nothing simply doesn't narrow.
        if len(printings) > 1:
            for number in _collector_numbers_in(text):
                numbered = [p for p in printings
                            if p["collector_number"] == number]
                if len(numbered) == 1:
                    printings = numbered
                    identity.collector_number = number
                    break

        # A fuzzy hit is a GUESS, and a guess must never file cardboard
        # on its own. Measured against the real catalogue, clipped names
        # like "Searing B" confidently resolve to "Searing Barb" — a
        # different card that happens to have one printing, which would
        # otherwise auto-add and silently corrupt the inventory. Exact
        # name matches still auto-add; near-misses ask.
        if was_fuzzy:
            confidence = CONFIDENCE_AMBIGUOUS
            reason = f"Closest name match to '{result.identity.raw_text[:24].strip()}'"
        else:
            confidence = (CONFIDENCE_LIKELY if len(printings) == 1
                          else CONFIDENCE_AMBIGUOUS)
            reason = (f"Matched by name ({len(printings)} printing"
                      f"{'s' if len(printings) != 1 else ''})")

        result.candidates = [
            ScanCandidate(p, confidence, reason, 1.0 / len(printings))
            for p in printings[:MAX_CANDIDATES]
        ]
        result.confidence = confidence
        return result

    result.confidence = CONFIDENCE_UNKNOWN
    return result


def _names_roughly_match(a: str, b: str) -> bool:
    """Loose name comparison tolerant of OCR damage.

    Compares only letters, lowercased, and accepts a prefix match — OCR
    frequently clips the tail of a long name or mangles punctuation, and
    "Skithiryx, the Blight Drag" should still match.
    """
    def norm(s: str) -> str:
        return re.sub(r"[^a-z]", "", (s or "").lower())

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    return len(shorter) >= 6 and longer.startswith(shorter)


def _fuzzy_card_name(name: str, card_db) -> str | None:
    """Closest card name in the printings catalogue, or None.

    Bounded to a prefix query so this never degrades into a full scan of
    107k rows on every failed read.
    """
    import difflib

    cleaned = re.sub(r"[^A-Za-z0-9 ,'\-]", "", name or "").strip()
    if len(cleaned) < 4:
        return None
    conn = card_db.connect()
    rows = conn.execute(
        """SELECT DISTINCT name FROM card_printings
           WHERE name LIKE ? COLLATE NOCASE LIMIT 40""",
        (cleaned[:4] + "%",),
    ).fetchall()
    names = [r[0] for r in rows]
    if not names:
        return None
    best = difflib.get_close_matches(cleaned, names, n=1, cutoff=0.75)
    return best[0] if best else None


@dataclass
class ScanSession:
    """Running totals for a scanning run.

    The session count is the point of continuous mode: someone hands you a
    box, you scan it, and the number at the bottom of the screen is what you
    came for.
    """

    scanned: int = 0
    added: int = 0
    skipped: int = 0
    needs_review: int = 0
    value_usd: float = 0.0
    unpriced: int = 0
    entries: list[dict] = field(default_factory=list)

    def record(self, result: ScanResult, *, added: bool, finish: str = "nonfoil") -> dict:
        self.scanned += 1
        printing = result.best
        entry = {
            "card_name": (printing or {}).get("name") or result.identity.name or "(unread)",
            "printing_id": (printing or {}).get("printing_id", ""),
            "set_code": (printing or {}).get("set_code", ""),
            "collector_number": (printing or {}).get("collector_number", ""),
            "confidence": result.confidence,
            "added": added,
            "finish": finish,
        }
        if added:
            self.added += 1
            price = _price_for_finish(printing, finish)
            if price is None:
                self.unpriced += 1
                entry["price_usd"] = None
            else:
                self.value_usd = round(self.value_usd + price, 2)
                entry["price_usd"] = price
        elif result.confidence == CONFIDENCE_UNKNOWN:
            self.skipped += 1
        else:
            self.needs_review += 1
        self.entries.append(entry)
        return entry

    def record_extra_copy(self, printing: dict, finish: str) -> dict:
        """A second (or fifth) copy of a card already filed this run.

        Playsets and bulk boxes hold multiples, and rescanning the same card
        four times is slower and less reliable than saying "four of these".
        Counted as scanned and added, exactly as if it had been read.
        """
        self.scanned += 1
        self.added += 1
        entry = {
            "card_name": printing.get("name", ""),
            "printing_id": printing.get("printing_id", ""),
            "set_code": printing.get("set_code", ""),
            "collector_number": printing.get("collector_number", ""),
            "confidence": "manual",
            "added": True,
            "finish": finish,
        }
        price = _price_for_finish(printing, finish)
        if price is None:
            self.unpriced += 1
            entry["price_usd"] = None
        else:
            self.value_usd = round(self.value_usd + price, 2)
            entry["price_usd"] = price
        self.entries.append(entry)
        return entry

    def undo_copy(self, printing_id: str, finish: str) -> dict | None:
        """Take back one copy filed this run, totals and all.

        Returns the entry that was undone, or None if this run never added
        that printing — the session must not be able to remove what it did not
        add, or the totals stop describing the run.
        """
        for index in range(len(self.entries) - 1, -1, -1):
            entry = self.entries[index]
            if (entry.get("added") and entry.get("printing_id") == printing_id
                    and entry.get("finish", "nonfoil") == finish):
                self.entries.pop(index)
                self.added -= 1
                self.scanned = max(0, self.scanned - 1)
                price = entry.get("price_usd")
                if price is None:
                    self.unpriced = max(0, self.unpriced - 1)
                else:
                    self.value_usd = round(max(0.0, self.value_usd - price), 2)
                return entry
        return None

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "added": self.added,
            "skipped": self.skipped,
            "needs_review": self.needs_review,
            "value_usd": round(self.value_usd, 2),
            "unpriced": self.unpriced,
            "entries": self.entries[-50:],
            # What the phone needs to offer +/- per card: one row per distinct
            # printing filed this run, with how many went in.
            "counts": self.copy_counts(),
        }

    def copy_counts(self) -> list[dict]:
        """Distinct printings added this run, most recent first."""
        seen: dict[tuple, dict] = {}
        for entry in self.entries:
            if not entry.get("added"):
                continue
            key = (entry.get("printing_id", ""), entry.get("finish", "nonfoil"))
            row = seen.get(key)
            if row is None:
                seen[key] = {**entry, "quantity": 1}
            else:
                row["quantity"] += 1
        return list(reversed(list(seen.values())))


def _price_for_finish(printing: dict | None, finish: str) -> float | None:
    if not printing:
        return None
    key = {"foil": "price_usd_foil", "etched": "price_usd_etched"}.get(finish, "price_usd")
    return printing.get(key)
