"""Archetype models representing virtual opponents.

Each archetype defines a behavioral profile that simulates how an opponent
pressures our deck without needing a full decklist. This models the *effect*
of the opponent rather than card-by-card simulation.

Profiles define:
  - clock: how fast the opponent threatens lethal (turns to kill)
  - interaction: how often they disrupt our board/hand per turn
  - wipe_chance: probability of a board wipe on any given turn
  - pressure_start: turn they begin applying meaningful pressure
  - resilience: how well they recover after we interact with them
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ArchetypeName(str, Enum):
    AGGRO = "aggro"
    MIDRANGE = "midrange"
    CONTROL = "control"
    COMBO = "combo"
    STAX = "stax"
    ARISTOCRATS = "aristocrats"
    SPELLSLINGER = "spellslinger"
    TOKENS = "tokens"
    VOLTRON = "voltron"
    GROUP_HUG = "group_hug"
    TURBO = "turbo"


@dataclass
class ArchetypeProfile:
    """Behavioral profile of a virtual opponent archetype."""

    name: ArchetypeName
    display_name: str
    description: str

    # Clock: how many turns until the opponent would kill us (unimpeded)
    clock_turns: int = 8
    # Damage dealt per turn once clock starts (approximate)
    damage_per_turn: int = 5

    # Interaction profile (per turn probabilities)
    targeted_removal_chance: float = 0.15  # Chance they remove one of our permanents
    counterspell_chance: float = 0.05      # Chance they counter our spell
    wipe_chance: float = 0.03             # Chance of a board wipe
    hand_disruption_chance: float = 0.0   # Chance they force a discard

    # Pressure timeline
    pressure_start_turn: int = 3   # Turn they start dealing damage
    max_pressure_turn: int = 7     # Turn they reach full pressure

    # Stax effects (reduce our available mana / options)
    mana_tax: int = 0              # Extra mana cost on our spells
    cards_taxed_per_turn: int = 0  # Cards we can't draw

    # Resilience: how fast they rebuild after interaction (0-1)
    resilience: float = 0.5

    # Weight in default gauntlet (meta share)
    meta_weight: float = 1.0


# =============================================================================
# Preset archetypes
# =============================================================================

ARCHETYPES: dict[ArchetypeName, ArchetypeProfile] = {
    ArchetypeName.AGGRO: ArchetypeProfile(
        name=ArchetypeName.AGGRO,
        display_name="Aggro",
        description="Fast creature-based damage, minimal interaction",
        clock_turns=6,
        damage_per_turn=6,
        targeted_removal_chance=0.08,
        counterspell_chance=0.0,
        wipe_chance=0.0,
        pressure_start_turn=1,
        max_pressure_turn=4,
        resilience=0.3,
        meta_weight=1.5,
    ),
    ArchetypeName.MIDRANGE: ArchetypeProfile(
        name=ArchetypeName.MIDRANGE,
        display_name="Midrange",
        description="Balanced threats and interaction, moderate clock",
        clock_turns=8,
        damage_per_turn=5,
        targeted_removal_chance=0.20,
        counterspell_chance=0.05,
        wipe_chance=0.05,
        pressure_start_turn=3,
        max_pressure_turn=6,
        resilience=0.6,
        meta_weight=2.0,
    ),
    ArchetypeName.CONTROL: ArchetypeProfile(
        name=ArchetypeName.CONTROL,
        display_name="Control",
        description="Heavy interaction, slow win condition, board wipes",
        clock_turns=12,
        damage_per_turn=3,
        targeted_removal_chance=0.25,
        counterspell_chance=0.20,
        wipe_chance=0.10,
        pressure_start_turn=5,
        max_pressure_turn=9,
        resilience=0.8,
        meta_weight=1.5,
    ),
    ArchetypeName.COMBO: ArchetypeProfile(
        name=ArchetypeName.COMBO,
        display_name="Combo",
        description="Assembles a win condition, then wins instantly",
        clock_turns=7,
        damage_per_turn=40,  # Instant kill when combo fires
        targeted_removal_chance=0.10,
        counterspell_chance=0.15,
        wipe_chance=0.02,
        pressure_start_turn=5,
        max_pressure_turn=7,
        resilience=0.5,
        meta_weight=1.5,
    ),
    ArchetypeName.STAX: ArchetypeProfile(
        name=ArchetypeName.STAX,
        display_name="Stax",
        description="Taxes and locks, slows the game dramatically",
        clock_turns=14,
        damage_per_turn=2,
        targeted_removal_chance=0.10,
        counterspell_chance=0.05,
        wipe_chance=0.05,
        hand_disruption_chance=0.10,
        pressure_start_turn=2,
        max_pressure_turn=5,
        mana_tax=1,
        resilience=0.7,
        meta_weight=1.0,
    ),
    ArchetypeName.ARISTOCRATS: ArchetypeProfile(
        name=ArchetypeName.ARISTOCRATS,
        display_name="Aristocrats",
        description="Sacrifice-based value engine, drain damage",
        clock_turns=8,
        damage_per_turn=4,
        targeted_removal_chance=0.12,
        counterspell_chance=0.02,
        wipe_chance=0.03,
        pressure_start_turn=3,
        max_pressure_turn=6,
        resilience=0.7,
        meta_weight=1.0,
    ),
    ArchetypeName.SPELLSLINGER: ArchetypeProfile(
        name=ArchetypeName.SPELLSLINGER,
        display_name="Spellslinger",
        description="Instants and sorceries, burn and card advantage",
        clock_turns=8,
        damage_per_turn=5,
        targeted_removal_chance=0.20,
        counterspell_chance=0.15,
        wipe_chance=0.05,
        pressure_start_turn=2,
        max_pressure_turn=5,
        resilience=0.5,
        meta_weight=1.0,
    ),
    ArchetypeName.TOKENS: ArchetypeProfile(
        name=ArchetypeName.TOKENS,
        display_name="Tokens / Go-Wide",
        description="Floods the board with tokens, overwhelms with numbers",
        clock_turns=7,
        damage_per_turn=7,
        targeted_removal_chance=0.05,
        counterspell_chance=0.0,
        wipe_chance=0.02,
        pressure_start_turn=3,
        max_pressure_turn=6,
        resilience=0.6,
        meta_weight=1.0,
    ),
    ArchetypeName.VOLTRON: ArchetypeProfile(
        name=ArchetypeName.VOLTRON,
        display_name="Voltron",
        description="Buffs one creature to lethal, commander damage focus",
        clock_turns=6,
        damage_per_turn=8,
        targeted_removal_chance=0.05,
        counterspell_chance=0.05,
        wipe_chance=0.0,
        pressure_start_turn=3,
        max_pressure_turn=5,
        resilience=0.3,
        meta_weight=0.5,
    ),
    ArchetypeName.GROUP_HUG: ArchetypeProfile(
        name=ArchetypeName.GROUP_HUG,
        display_name="Group Hug / Pillowfort",
        description="Minimal threat, gives resources, wins through politics",
        clock_turns=15,
        damage_per_turn=1,
        targeted_removal_chance=0.05,
        counterspell_chance=0.05,
        wipe_chance=0.08,
        pressure_start_turn=8,
        max_pressure_turn=12,
        resilience=0.9,
        meta_weight=0.5,
    ),
    ArchetypeName.TURBO: ArchetypeProfile(
        name=ArchetypeName.TURBO,
        display_name="Turbo / cEDH",
        description="All-in fast combo with heavy protection",
        clock_turns=4,
        damage_per_turn=40,
        targeted_removal_chance=0.05,
        counterspell_chance=0.25,
        wipe_chance=0.0,
        pressure_start_turn=3,
        max_pressure_turn=4,
        resilience=0.4,
        meta_weight=0.5,
    ),
}


def get_default_gauntlet() -> list[ArchetypeProfile]:
    """Return the default gauntlet of archetypes for matchup testing."""
    return list(ARCHETYPES.values())


def get_archetype(name: str) -> ArchetypeProfile | None:
    """Look up an archetype by name."""
    try:
        return ARCHETYPES[ArchetypeName(name.lower())]
    except (ValueError, KeyError):
        return None
