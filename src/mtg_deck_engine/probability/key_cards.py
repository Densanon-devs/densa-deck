"""Key card access calculator.

Answers questions like:
- What's the chance I see my Sol Ring by turn 3?
- What's the chance I assemble my combo by turn 6?
- What's the chance I have at least one removal spell by turn 3?

Supports both hypergeometric (fast, exact for single cards) and
Monte Carlo (for complex multi-card package scenarios).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from mtg_deck_engine.models import CardTag, Deck, DeckEntry, Zone
from mtg_deck_engine.probability.hypergeometric import (
    cards_seen_by_turn,
    prob_at_least,
    prob_card_by_turn,
)


@dataclass
class CardAccessResult:
    """Probability of accessing a specific card or package."""

    name: str
    copies_in_deck: int = 0
    deck_size: int = 0
    # Probability of seeing at least 1 copy by each turn
    by_turn: dict[int, float] = field(default_factory=dict)


@dataclass
class PackageAccessResult:
    """Probability of assembling a multi-card package."""

    name: str
    components: list[str] = field(default_factory=list)
    # Probability of seeing all required components by each turn
    by_turn: dict[int, float] = field(default_factory=dict)


@dataclass
class RoleAccessResult:
    """Probability of drawing at least one card with a given functional role."""

    role: str
    total_in_deck: int = 0
    by_turn: dict[int, float] = field(default_factory=dict)


@dataclass
class KeyCardReport:
    """Complete key card access analysis."""

    card_access: list[CardAccessResult] = field(default_factory=list)
    package_access: list[PackageAccessResult] = field(default_factory=list)
    role_access: list[RoleAccessResult] = field(default_factory=list)


def analyze_card_access(
    deck: Deck,
    card_names: list[str] | None = None,
    max_turn: int = 10,
    on_play: bool = True,
) -> list[CardAccessResult]:
    """Calculate probability of seeing specific cards by each turn.

    Uses exact hypergeometric calculation (fast).

    Args:
        deck: The deck to analyze.
        card_names: Cards to check. If None, auto-selects key cards.
        max_turn: Calculate through this turn number.
        on_play: Whether on the play or draw.
    """
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD)]
    deck_size = sum(e.quantity for e in active)

    if deck_size == 0:
        return []

    # Auto-select key cards if none specified
    if card_names is None:
        card_names = _auto_select_key_cards(deck)

    results: list[CardAccessResult] = []

    for name in card_names:
        # Count copies in deck
        copies = sum(
            e.quantity for e in active
            if e.card_name.lower() == name.lower()
        )
        if copies == 0:
            continue

        result = CardAccessResult(
            name=name,
            copies_in_deck=copies,
            deck_size=deck_size,
        )

        for turn in range(1, max_turn + 1):
            p = prob_card_by_turn(copies, deck_size, turn, on_play)
            result.by_turn[turn] = round(p, 4)

        results.append(result)

    return results


def analyze_role_access(
    deck: Deck,
    roles: list[CardTag] | None = None,
    max_turn: int = 10,
    on_play: bool = True,
) -> list[RoleAccessResult]:
    """Calculate probability of drawing at least one card with a given role by each turn.

    Uses exact hypergeometric calculation.
    """
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD)]
    deck_size = sum(e.quantity for e in active)

    if deck_size == 0:
        return []

    if roles is None:
        roles = [
            CardTag.RAMP,
            CardTag.CARD_DRAW,
            CardTag.TARGETED_REMOVAL,
            CardTag.BOARD_WIPE,
            CardTag.COUNTERSPELL,
        ]

    results: list[RoleAccessResult] = []

    for role in roles:
        total = sum(
            e.quantity for e in active
            if e.card and role in e.card.tags
        )
        if total == 0:
            continue

        result = RoleAccessResult(role=role.value, total_in_deck=total)

        for turn in range(1, max_turn + 1):
            n = cards_seen_by_turn(turn, on_play)
            n = min(n, deck_size)
            p = prob_at_least(1, deck_size, total, n)
            result.by_turn[turn] = round(p, 4)

        results.append(result)

    return results


def analyze_package_access(
    deck: Deck,
    packages: dict[str, list[str]],
    max_turn: int = 10,
    simulations: int = 10000,
    on_play: bool = True,
    seed: int | None = None,
) -> list[PackageAccessResult]:
    """Calculate probability of assembling a multi-card package by each turn.

    Uses Monte Carlo simulation since multi-card combos can't be computed
    with a single hypergeometric call (cards are drawn from the same pool).

    Args:
        packages: Dict of package_name -> list of card names needed.
                  e.g. {"Infinite Combo": ["Card A", "Card B", "Card C"]}
    """
    if seed is not None:
        random.seed(seed)

    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD)]
    pool = _build_pool(active)
    deck_size = len(pool)

    if deck_size < 7:
        return []

    results: list[PackageAccessResult] = []

    for pkg_name, components in packages.items():
        result = PackageAccessResult(name=pkg_name, components=components)

        # Track hits per turn across simulations
        turn_hits: dict[int, int] = {t: 0 for t in range(1, max_turn + 1)}

        for _ in range(simulations):
            shuffled = pool.copy()
            random.shuffle(shuffled)

            # Track which components we've found
            needed = {c.lower(): False for c in components}

            for turn in range(1, max_turn + 1):
                n = cards_seen_by_turn(turn, on_play)
                n = min(n, deck_size)
                hand = shuffled[:n]

                # Check if all components are present
                for entry in hand:
                    if entry.card_name.lower() in needed:
                        needed[entry.card_name.lower()] = True

                if all(needed.values()):
                    # Found all components — mark this turn and all future turns
                    for t in range(turn, max_turn + 1):
                        turn_hits[t] += 1
                    break

        for turn in range(1, max_turn + 1):
            result.by_turn[turn] = round(turn_hits[turn] / simulations, 4)

        results.append(result)

    return results


def _auto_select_key_cards(deck: Deck) -> list[str]:
    """Auto-select important cards to track: commanders, tutors, finishers, engines."""
    key: list[str] = []

    # Commanders
    for e in deck.commanders:
        key.append(e.card_name)

    # High-value tagged cards (deduplicate)
    seen = {n.lower() for n in key}
    priority_tags = [
        CardTag.TUTOR,
        CardTag.FINISHER,
        CardTag.ENGINE,
        CardTag.BOARD_WIPE,
    ]

    for entry in deck.entries:
        if entry.card is None or entry.zone in (Zone.MAYBEBOARD, Zone.SIDEBOARD):
            continue
        if entry.card_name.lower() in seen:
            continue
        if entry.card.tags and any(t in entry.card.tags for t in priority_tags):
            key.append(entry.card_name)
            seen.add(entry.card_name.lower())

    # Cap at 10 to keep output manageable
    return key[:10]


def _build_pool(entries: list[DeckEntry]) -> list[DeckEntry]:
    """Expand entries by quantity into a flat draw pool."""
    pool: list[DeckEntry] = []
    for entry in entries:
        for _ in range(entry.quantity):
            pool.append(entry)
    return pool
