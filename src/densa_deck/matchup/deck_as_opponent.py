"""Build a virtual opponent ArchetypeProfile from a concrete Deck.

The existing matchup simulator takes deck-vs-ArchetypeProfile (behavioral
model of a canonical archetype like Aggro / Control / Combo). For
deck-vs-deck duels we want to pit two real decklists against each other
using the same sim engine, which means synthesizing an ArchetypeProfile
for one side from its static analysis.

This is an *approximation*, not a card-by-card simulator:
  - We start from the nearest preset profile (detected archetype) as a
    baseline so behaviour is sane even with sparse data.
  - We overlay deck-specific signals (role counts + power sub-scores +
    mana curve) to tune clock, interaction, and resilience.

That gives results at the same fidelity as the archetype gauntlet (which
is what the user sees today) while letting them compare two of their own
saved decks directly.
"""

from __future__ import annotations

from dataclasses import replace

from densa_deck.analysis.power_level import PowerBreakdown
from densa_deck.matchup.archetypes import (
    ARCHETYPES, ArchetypeName, ArchetypeProfile,
)
from densa_deck.models import AnalysisResult, Deck


_ARCHETYPE_STRING_MAP: dict[str, ArchetypeName] = {
    "aggro": ArchetypeName.AGGRO,
    "midrange": ArchetypeName.MIDRANGE,
    "control": ArchetypeName.CONTROL,
    "combo": ArchetypeName.COMBO,
    "stax": ArchetypeName.STAX,
    "aristocrats": ArchetypeName.ARISTOCRATS,
    "spellslinger": ArchetypeName.SPELLSLINGER,
    "tokens": ArchetypeName.TOKENS,
    "voltron": ArchetypeName.VOLTRON,
    "group_hug": ArchetypeName.GROUP_HUG,
    "turbo": ArchetypeName.TURBO,
}


def deck_to_profile(
    deck: Deck,
    analysis: AnalysisResult,
    power: PowerBreakdown,
    archetype_label: str,
    display_name: str | None = None,
) -> ArchetypeProfile:
    """Synthesize an ArchetypeProfile from a saved deck's static analysis.

    Used by the deck-vs-deck duel mode to create a behavioral opponent
    model for the "other" deck, so simulate_matchup can run unchanged.

    Signal mapping (each PowerBreakdown axis scores 0-10):
      - `archetype_label` -> closest preset profile as baseline
      - `power.speed` -> clock_turns (faster = fewer turns)
      - `power.interaction` -> targeted_removal_chance + counterspell_chance
      - `power.mana_efficiency` -> resilience (consistent mana = rebuilds well)
      - `power.combo_potential` -> damage_per_turn bump + reduced clock
      - `analysis.interaction_count` -> hand_disruption_chance bump
      - `display_name` -> what shows on the duel result UI

    The returned profile is a frozen copy (dataclasses.replace) so we
    don't mutate the ARCHETYPES preset dict that the regular gauntlet
    also reads from.
    """
    baseline_name = _ARCHETYPE_STRING_MAP.get(
        (archetype_label or "").lower(), ArchetypeName.MIDRANGE,
    )
    baseline = ARCHETYPES[baseline_name]

    # All PowerBreakdown axes are 0-10. Normalize to 0-1 for scaling.
    def _norm(v: float) -> float:
        return max(0.0, min(1.0, float(v) / 10.0))

    speed = _norm(power.speed)
    interaction = _norm(power.interaction)
    combo = _norm(power.combo_potential)
    mana = _norm(power.mana_efficiency)

    # Clock: faster deck -> shorter clock. A high combo_potential score
    # also shaves turns (combo decks can win out of nowhere).
    clock_turns = max(4, int(round(12 - speed * 7 - combo * 1.5)))

    # Interaction density translates directly to per-turn removal/counter
    # probabilities. Scales the baseline by 0.5x-1.8x.
    interaction_scale = 0.5 + interaction * 1.3
    targeted = min(0.6, baseline.targeted_removal_chance * interaction_scale)
    counter = min(0.4, baseline.counterspell_chance * interaction_scale)

    # Hand disruption bump from the raw role-count signal (fires for
    # decks that actually ship a lot of removal/interaction cards, which
    # the power score alone doesn't always reflect in Commander).
    disrupt_bump = 0.0
    if analysis.interaction_count >= 10:
        disrupt_bump = 0.08
    elif analysis.interaction_count >= 5:
        disrupt_bump = 0.04
    hand_disrupt = min(0.4, baseline.hand_disruption_chance + disrupt_bump)

    # Resilience: mana_efficiency proxy is "does this deck have the mana
    # to rebuild after a wipe?" — maps to 0.2-0.9 band.
    resilience = 0.2 + mana * 0.7

    # Damage per turn: faster decks hit harder. Scale baseline by the
    # speed percentile so a slow Control-in-Voltron-clothing deck doesn't
    # swing like an Aggro deck purely because of archetype match.
    damage_per_turn = max(3, int(round(baseline.damage_per_turn * (0.6 + speed * 0.8))))

    return replace(
        baseline,
        display_name=display_name or baseline.display_name,
        clock_turns=clock_turns,
        damage_per_turn=damage_per_turn,
        targeted_removal_chance=round(targeted, 3),
        counterspell_chance=round(counter, 3),
        hand_disruption_chance=round(hand_disrupt, 3),
        resilience=round(resilience, 3),
    )
