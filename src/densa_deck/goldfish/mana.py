"""Colour-aware mana: cost parsing, land dispersion, and payment solving.

The simulator used to model mana as a single integer, so a five-colour pile
played exactly like a mono-green deck — every land made one generic mana and
every spell only needed enough of it. Colour screw, the single most common
way a real deck fails, was invisible.

This module supplies the three pieces needed to fix that:

  * `parse_mana_cost` — turn "{2}{W}{U}" into generic + coloured requirements,
    handling hybrid, twobrid and Phyrexian pips as the flexible costs they are.
  * `source_colors` — what colours a permanent can actually produce, read off
    Scryfall's `produced_mana`.
  * `can_pay` / `pay_cost` — an exact answer to "can these untapped sources
    pay this cost", solved as bipartite matching rather than guessed at.

Payment is a matching problem, not a counting problem: three lands that each
tap for only white cannot cast a {W}{U}{B} spell even though there are three
of them. Greedy assignment gets this wrong in ordinary cases (assign a dual
to a pip a basic could have covered and the last pip fails), so this uses
augmenting paths and is exact.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from densa_deck.models import Card

COLORS = ("W", "U", "B", "R", "G")
ALL_COLORS = frozenset(COLORS)
# Colourless mana is its own kind: {C} costs need a colourless source, and a
# colourless source cannot pay a coloured pip.
COLORLESS = "C"

_GENERIC_RE = re.compile(r"\{(\d+)\}")
_SIMPLE_PIP_RE = re.compile(r"\{([WUBRGC])\}")
# {W/U} hybrid, {2/W} twobrid, {W/P} Phyrexian
_HYBRID_RE = re.compile(r"\{([WUBRGC2])/([WUBRGCP])\}")


@dataclass
class ManaCost:
    """A parsed mana cost.

    `pips` are strict single-colour requirements. `hybrid` entries are sets of
    colours any one of which satisfies that pip — a {W/U} pip, or a {2/W} pip
    once we've decided to pay it with a colour rather than two generic.
    """

    generic: int = 0
    pips: Counter = field(default_factory=Counter)
    hybrid: list[frozenset] = field(default_factory=list)
    # Lazily built by `colored_requirements`; not part of the cost's identity.
    _requirements: list | None = field(default=None, repr=False, compare=False)

    @property
    def total(self) -> int:
        """Converted mana cost of the parsed requirements."""
        return self.generic + sum(self.pips.values()) + len(self.hybrid)

    @property
    def colored_requirements(self) -> list[frozenset]:
        """Every coloured requirement as a set of acceptable colours.

        Built once and kept: parsed costs are cached and shared, and the
        payment solver asks for this on every cast check.
        """
        if self._requirements is None:
            reqs: list[frozenset] = []
            for color, count in self.pips.items():
                for _ in range(count):
                    reqs.append(frozenset({color}))
            reqs.extend(self.hybrid)
            self._requirements = reqs
        return self._requirements

    def is_colorless(self) -> bool:
        return not self.pips and not self.hybrid


def parse_mana_cost(mana_cost: str) -> ManaCost:
    """Parse a Scryfall mana-cost string into generic and coloured demand.

    {X} contributes nothing — an X spell is castable for X=0, and pretending
    otherwise would mark every X spell uncastable.

    Cached: a deck has a few dozen distinct cost strings, but the casting
    loop asks for them tens of thousands of times per batch. Callers treat
    the result as read-only — `effective_mana_cost` builds a new ManaCost
    rather than mutating this one.
    """
    return _parse_mana_cost_cached(mana_cost or "")


@lru_cache(maxsize=8192)
def _parse_mana_cost_cached(mana_cost: str) -> ManaCost:
    cost = ManaCost()
    if not mana_cost:
        return cost

    text = mana_cost.upper()

    for match in _GENERIC_RE.finditer(text):
        cost.generic += int(match.group(1))

    # Hybrids first so their symbols aren't double-counted as simple pips.
    hybrid_spans = []
    for match in _HYBRID_RE.finditer(text):
        left, right = match.group(1), match.group(2)
        hybrid_spans.append(match.span())
        if right == "P":
            # Phyrexian: payable with 2 life, so it never blocks a cast.
            continue
        if left == "2":
            # Twobrid: one coloured mana OR two generic. We take the colour
            # branch when available and fall back to generic in the solver.
            cost.hybrid.append(frozenset({right}))
            continue
        cost.hybrid.append(frozenset({left, right}))

    for match in _SIMPLE_PIP_RE.finditer(text):
        if any(start <= match.start() < end for start, end in hybrid_spans):
            continue
        cost.pips[match.group(1)] += 1

    return cost


def card_mana_cost(card: Card) -> ManaCost:
    """Parsed cost of a card, falling back to its front face for DFCs."""
    raw = card.mana_cost
    if not raw and card.faces:
        raw = card.faces[0].mana_cost
    return parse_mana_cost(raw or "")


# Basic land types map to the colour they tap for. Used to read a fetchland's
# real colour access off the types it searches for.
BASIC_TYPE_COLORS = {
    "plains": "W",
    "island": "U",
    "swamp": "B",
    "mountain": "R",
    "forest": "G",
}

# Everything the search clause names, up to the end of its sentence. Scanning
# the whole clause rather than stopping at the first "card" is what catches
# Krosan Verge's "a Forest card and a Plains card".
_FETCH_RE = re.compile(r"search your library for ([^.]*)")
_BASIC_TYPE_WORD_RE = re.compile(r"\b(plains|island|swamp|mountain|forest)s?\b")


def fetch_colors(card: Card) -> frozenset:
    """Colours a fetchland gives access to, from the types it searches for.

    Scryfall records `produced_mana: []` for fetchlands — they sacrifice
    rather than tap for mana — so reading colours off that field alone marks
    every fetch colourless, which is precisely backwards: a fetch is the best
    fixing in the game. "Search your library for a Forest or Island card"
    means green and blue; a generic "basic land card" means all five.
    """
    oracle = (card.oracle_text or "").lower()
    if "search your library" not in oracle or "onto the battlefield" not in oracle:
        return frozenset()

    match = _FETCH_RE.search(oracle)
    scope = match.group(1) if match else ""
    named = {
        BASIC_TYPE_COLORS[word] for word in _BASIC_TYPE_WORD_RE.findall(scope)
    }
    if named:
        return frozenset(named)
    # "a basic land card" / "a land card" with no named types — any colour.
    if "basic land" in scope or "land card" in scope:
        return ALL_COLORS
    return frozenset()


def produces_mana(card: Card | None) -> bool:
    """Whether this permanent can be tapped for mana at all.

    Lands are assumed to make mana unless the card gives positive evidence
    otherwise — it has oracle text, records no produced mana, names no
    fetchable land, and has no "add" ability. That keeps Maze of Ith and
    Arena from being counted as mana sources without breaking cards whose
    data we simply couldn't resolve.
    """
    if card is None:
        return False
    if card.produced_mana or any(f.produced_mana for f in card.faces):
        return True
    if not card.is_land:
        return False
    oracle = (card.oracle_text or "").lower()
    if not oracle:
        return True  # No text to judge by — assume it taps for something.
    if fetch_colors(card):
        return True
    return "add" in oracle


_SOURCE_COLOR_CACHE: dict[str, frozenset] = {}


def source_colors(card: Card | None) -> frozenset:
    """Cached wrapper — oracle text is stable per card name."""
    if card is None:
        return frozenset({COLORLESS})
    cached = _SOURCE_COLOR_CACHE.get(card.name)
    if cached is None:
        cached = _source_colors_uncached(card)
        _SOURCE_COLOR_CACHE[card.name] = cached
    return cached


def clear_mana_caches() -> None:
    """Drop cached cost and colour parses. Tests that reuse a card name for
    different text must call this."""
    _SOURCE_COLOR_CACHE.clear()
    _parse_mana_cost_cached.cache_clear()


def _source_colors_uncached(card: Card | None) -> frozenset:
    """Colours a permanent can produce, from Scryfall's `produced_mana`.

    Falls back to fetchable land types, then to the card's own colour
    identity, and finally to colourless — the honest default for an unknown
    source rather than pretending it makes any colour.
    """
    if card is None:
        return frozenset({COLORLESS})

    produced: set[str] = set(card.produced_mana or [])
    for face in card.faces:
        produced.update(face.produced_mana or [])

    usable = {c for c in produced if c in ALL_COLORS or c == COLORLESS}

    # Cards that both tap for mana and fetch (Krosan Verge taps for {C} and
    # sacrifices for a Forest and a Plains) get the union: the permanent
    # gives access to all of those colours. The solver only ever spends a
    # unit once, so this grants access without granting extra mana.
    fetched = fetch_colors(card)
    if usable or fetched:
        return frozenset(usable | fetched)

    # A land with no produced_mana recorded still taps for something; use its
    # colour identity when we have one.
    identity = {c.value if hasattr(c, "value") else str(c) for c in (card.color_identity or [])}
    identity = {c for c in identity if c in ALL_COLORS}
    if identity:
        return frozenset(identity)
    return frozenset({COLORLESS})


# --- Payment solving --------------------------------------------------------


def _match_colored(requirements: list[frozenset], units: list[frozenset]) -> list[int] | None:
    """Maximum bipartite matching of coloured requirements to mana units.

    Returns the unit index used for each requirement, or None if some
    requirement cannot be satisfied. Kuhn's algorithm with augmenting paths —
    exact, and fast at the sizes involved (a handful of pips, tens of units).
    """
    assigned: dict[int, int] = {}  # unit index -> requirement index

    def try_assign(req_idx: int, seen: set[int]) -> bool:
        for unit_idx, unit_colors in enumerate(units):
            if unit_idx in seen:
                continue
            if not (requirements[req_idx] & unit_colors):
                continue
            seen.add(unit_idx)
            holder = assigned.get(unit_idx)
            if holder is None or try_assign(holder, seen):
                assigned[unit_idx] = req_idx
                return True
        return False

    for req_idx in range(len(requirements)):
        if not try_assign(req_idx, set()):
            return None

    result = [-1] * len(requirements)
    for unit_idx, req_idx in assigned.items():
        result[req_idx] = unit_idx
    return result


def pay_cost(cost: ManaCost, units: list[frozenset]) -> list[int] | None:
    """Indices of the mana units consumed to pay `cost`, or None if it can't be paid.

    Coloured requirements are matched first — they're the constrained side —
    and the generic portion is then paid from whatever is left over.
    """
    requirements = cost.colored_requirements
    if len(requirements) + cost.generic > len(units):
        return None

    used_for_colors = _match_colored(requirements, units) if requirements else []
    if used_for_colors is None:
        return None

    spent = set(used_for_colors)
    remaining = [i for i in range(len(units)) if i not in spent]
    if len(remaining) < cost.generic:
        return None

    # Spend the least flexible leftovers on the generic portion so that any
    # mana kept back stays as useful as possible.
    remaining.sort(key=lambda i: len(units[i]))
    return list(spent) + remaining[: cost.generic]


def can_pay(cost: ManaCost, units: list[frozenset]) -> bool:
    """Whether these mana units can pay this cost, colours included."""
    return pay_cost(cost, units) is not None


def describe_dispersion(sources: list[frozenset]) -> Counter:
    """Count how many sources can produce each colour.

    A dual land counts once for each colour it makes, which is what "how many
    black sources do I have" means in practice.
    """
    counts: Counter = Counter()
    for colors in sources:
        for color in colors:
            counts[color] += 1
    return counts
