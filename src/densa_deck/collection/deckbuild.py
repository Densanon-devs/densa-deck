"""Build a deck out of a collection, using only cards that are in it.

Every other suggestion path in this engine reaches for the whole catalogue —
`find_add_candidates` scans 34,000 cards and hands back the best answer,
which is the right shape for "what should I buy". It is the wrong shape for
the question someone asks standing over a box of cards: **make me a deck out
of THIS**. The answer has to be constrained to what is physically present,
and it has to respect how many copies are there.

Deterministic, and deliberately so. The analyst can explain the result
afterwards and is welcome to, but the deck itself is arithmetic against a
pool — a build that came out different every time, or failed when no model
was loaded, would be worse than one that is merely good. Same reasoning as
`suggest_deckbuild_additions`, which is also LLM-free for exactly this.

The honest part is the reporting. A collection usually cannot fill a format's
targets, and a builder that quietly hands back 60 cards with four lands has
told you nothing. Every role says what it wanted, what it found, and what it
was short of.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from densa_deck.classification.tagger import classify_card
from densa_deck.models import CardTag, Format

# What each role is filled from, in priority order. The order matters: a card
# is spent once, and a Sol Ring counted as a threat is a Sol Ring not counted
# as ramp. Lands first because a deck short on lands is not a deck.
ROLE_TAGS: list[tuple[str, tuple[CardTag, ...]]] = [
    ("lands", (CardTag.LAND,)),
    ("ramp", (CardTag.RAMP, CardTag.MANA_ROCK, CardTag.MANA_DORK)),
    ("draw", (CardTag.CARD_DRAW, CardTag.CANTRIP)),
    ("removal", (CardTag.TARGETED_REMOVAL, CardTag.BOARD_WIPE,
                 CardTag.COUNTERSPELL,
                 CardTag.ARTIFACT_ENCHANTMENT_REMOVAL)),
    ("threats", (CardTag.THREAT, CardTag.FINISHER, CardTag.TOKEN_MAKER)),
]

# Roles that will take a second-best card rather than report a hole.
#
# `_is_threat` wants power 4, or 3 with evasion — right for ANALYSING a deck,
# where a 2/2 bear is not a threat, and wrong for BUILDING one, where a deck
# of bears is still a creature deck. Tagged threats are taken first and these
# only make up the difference, so a pool with real finishers in it still gets
# them; a pool without stops reporting zero threats for a deck that is
# nothing but creatures.
ROLE_FALLBACK = {
    "threats": lambda entry: bool(
        entry.card is not None and getattr(entry.card, "is_creature", False)),
}


@dataclass
class PoolCard:
    """One card in the pool, and how many of it there are."""

    name: str
    quantity: int
    card: object = None                     # densa_deck.models.Card, or None
    tags: set = field(default_factory=set)
    cmc: float = 0.0
    colors: set = field(default_factory=set)
    is_land: bool = False
    is_basic: bool = False


@dataclass
class RoleReport:
    role: str
    wanted: int
    filled: int

    @property
    def short(self) -> int:
        return max(0, self.wanted - self.filled)


def pool_from_collection(store, card_db, collection_uid: str | None = None) -> list[PoolCard]:
    """Everything in a collection, as cards the builder can reason about.

    Quantities are summed across stacks: a foil and a nonfoil of the same
    card are two copies to a deckbuilder even though they are two very
    different objects to the collection. Which physical one gets sleeved is a
    question for `grouping.py`, not this.
    """
    from densa_deck.collection.query import search_collection

    collection_id = None
    if collection_uid:
        found = store.collection_by_uid(collection_uid)
        if not found:
            raise ValueError("No such collection.")
        collection_id = found["collection_id"]

    items, _total, _ = search_collection(
        store, card_db, collection_id=collection_id, sort="name", limit=20000)

    counts: dict[str, int] = {}
    for item in items:
        if item.quantity <= 0:
            continue
        key = (item.card_name or "").strip()
        if key:
            counts[key] = counts.get(key, 0) + item.quantity

    pool: list[PoolCard] = []
    for name, quantity in counts.items():
        card = card_db.lookup_by_name(name)
        entry = PoolCard(name=name, quantity=quantity, card=card)
        if card is not None:
            entry.tags = set(classify_card(card))
            entry.cmc = card.display_cmc()
            entry.colors = {c.value for c in card.color_identity}
            entry.is_land = card.is_land
            entry.is_basic = CardTag.BASIC_LAND in entry.tags
        pool.append(entry)
    return pool


def _identity_for(pool: list[PoolCard], commander: PoolCard | None,
                  colors: set[str] | None) -> set[str]:
    """Which colours the deck is allowed to be.

    A commander decides it outright — that is the rule. Told nothing, the
    best guess is the colours the pool actually supports, which is a far more
    useful default than "all five": a collection with two blue cards in it
    should not produce a deck that is nominally blue.
    """
    if colors:
        return {c.strip().upper() for c in colors if c.strip()}
    if commander is not None and commander.colors:
        return set(commander.colors)

    weight: dict[str, int] = {}
    for entry in pool:
        for colour in entry.colors:
            weight[colour] = weight.get(colour, 0) + entry.quantity
    if not weight:
        return set()
    # The two best-supported colours, which is the shape most collections
    # actually hold. Going wider on a thin pool produces a deck that cannot
    # cast itself.
    ranked = sorted(weight, key=lambda c: -weight[c])
    return set(ranked[:2])


def _playable(entry: PoolCard, identity: set[str], format_: Format) -> bool:
    """Legal in the format, and inside the deck's colours."""
    if entry.card is None:
        return False                      # not in the catalogue; cannot judge
    if not entry.colors.issubset(identity):
        return False
    legality = entry.card.legalities.get(
        format_.value if isinstance(format_, Format) else str(format_))
    return str(getattr(legality, "value", legality)) in ("legal", "restricted")


def build_from_pool(pool: list[PoolCard], format_: Format = Format.COMMANDER, *,
                    commander_name: str = "", colors: set[str] | None = None,
                    ) -> dict:
    """Fill a decklist from the pool, best-first against the format's targets.

    Roles are filled in the order of `ROLE_TAGS`, and a card is spent once:
    counted as ramp it is not also counted as a threat. That is what stops a
    deck reporting twelve ramp and twelve threats out of twelve cards.

    Anything still missing after the roles are served is filled with the
    remaining playables by mana value, cheapest first — a deck of the pool's
    twenty most expensive cards is a deck that never casts anything.
    """
    from densa_deck.formats.profiles import COMMANDER, get_format_profile

    # Commander when the format is one this engine has no profile for — its
    # targets are the most demanding, so a deck built to them is playable
    # everywhere, and silently building to nothing would be worse.
    profile = get_format_profile(format_) or COMMANDER
    targets = profile.targets

    commander = None
    if commander_name:
        commander = next(
            (p for p in pool
             if p.name.lower() == commander_name.strip().lower()), None)
        if commander is None:
            raise ValueError(f"{commander_name!r} is not in that collection.")

    identity = _identity_for(pool, commander, colors)
    playable = [p for p in pool if _playable(p, identity, format_)]

    # How many of each card may be used: the format's limit, or how many are
    # owned, whichever is smaller. Basics are the standing exception.
    def allowance(entry: PoolCard) -> int:
        if entry.is_basic:
            return entry.quantity
        return min(entry.quantity, targets.max_copies)

    chosen: dict[str, int] = {}
    spent: set[str] = set()
    if commander is not None:
        chosen[commander.name] = 1
        spent.add(commander.name)

    reports: list[RoleReport] = []
    for role, tags in ROLE_TAGS:
        wanted = getattr(targets, role, (0, 0))[0]
        if role == "lands":
            # Lands are counted toward the deck size, so the commander does
            # not displace one.
            wanted = targets.lands[0]
        filled = 0
        wanted_tags = set(tags)
        fallback = ROLE_FALLBACK.get(role)

        # Two passes: the cards that genuinely carry the role, then — for the
        # roles that allow it — the next best thing. Doing it in one pass with
        # an OR would let a bear beat a dragon on mana value alone.
        for accept in ([lambda e: bool(e.tags & wanted_tags)]
                       + ([fallback] if fallback else [])):
            # Cheapest first inside a role: a curve is not a preference, it is
            # whether the deck functions.
            for entry in sorted(playable, key=lambda p: (p.cmc, p.name)):
                if filled >= wanted:
                    break
                if entry.name in spent and not entry.is_basic:
                    continue
                if not accept(entry):
                    continue
                take = min(allowance(entry) - chosen.get(entry.name, 0),
                           wanted - filled)
                if take <= 0:
                    continue
                chosen[entry.name] = chosen.get(entry.name, 0) + take
                filled += take
                spent.add(entry.name)
        reports.append(RoleReport(role=role, wanted=wanted, filled=filled))

    # Everything else, cheapest first, until the deck is the right size.
    size = sum(chosen.values())
    if size < targets.min_deck_size:
        for entry in sorted(playable, key=lambda p: (p.cmc, p.name)):
            if size >= targets.min_deck_size:
                break
            spare = allowance(entry) - chosen.get(entry.name, 0)
            if spare <= 0:
                continue
            take = min(spare, targets.min_deck_size - size)
            chosen[entry.name] = chosen.get(entry.name, 0) + take
            size += take

    return {
        "format": format_.value if isinstance(format_, Format) else str(format_),
        "commander": commander.name if commander else "",
        "colors": sorted(identity),
        "decklist": dict(sorted(chosen.items())),
        "total_cards": sum(chosen.values()),
        "target_size": targets.min_deck_size,
        "short_by": max(0, targets.min_deck_size - sum(chosen.values())),
        "pool_size": sum(p.quantity for p in pool),
        "playable_in_colors": sum(p.quantity for p in playable),
        "roles": [
            {"role": r.role, "wanted": r.wanted, "filled": r.filled,
             "short": r.short}
            for r in reports
        ],
    }


def decklist_text(built: dict) -> str:
    """The built deck as something to paste, save or analyse.

    The commander goes under its own header, because every downstream reader
    in this engine — the parser, the validator, the analyst — treats that
    zone differently, and a commander listed as a maindeck card is a 101-card
    deck with a rules violation in it.
    """
    lines: list[str] = []
    commander = built.get("commander") or ""
    if commander:
        lines.extend(["Commander:", f"1 {commander}", "", "Mainboard:"])
    for name, quantity in built.get("decklist", {}).items():
        if commander and name == commander:
            continue
        lines.append(f"{quantity} {name}")
    return "\n".join(lines) + "\n"
