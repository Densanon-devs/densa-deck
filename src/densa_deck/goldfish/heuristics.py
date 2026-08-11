"""Spell casting and land play heuristics for goldfish simulation.

These are simplified decision-making rules that approximate reasonable
play without implementing full MTG rules. The goal is to measure deck
consistency, not to play optimally.

Priority system:
  T1-T2: Ramp > low-cost threats > card draw
  T3-T4: Engines/draw > threats > interaction (goldfish = no opponent)
  T5+:   Finishers > threats > draw > anything castable
"""

from __future__ import annotations

from densa_deck.goldfish.effects import parse_effects
from densa_deck.goldfish.mana import ManaCost, card_mana_cost
from densa_deck.goldfish.state import GameState
from densa_deck.models import CardTag, DeckEntry


def play_turn(state: GameState):
    """Execute a full main phase: play land, tap mana, cast spells."""
    _play_best_land(state)
    _cast_spells(state)


def _play_best_land(state: GameState):
    """Choose and play every land drop we're entitled to this turn.

    Exploration and Azusa grant additional drops, so this is a loop rather
    than a single play guarded by a boolean.
    """
    while state.land_drops_this_turn < state.land_drops_allowed:
        lands = [e for e in state.hand if e.card and e.card.is_land]
        if not lands:
            return
        # Prefer untapped lands, then lands that produce needed colors
        lands.sort(key=lambda e: _land_score(e, state), reverse=True)
        state.play_land(lands[0])


def _land_score(entry: DeckEntry, state: GameState) -> float:
    """Score a land for play priority."""
    card = entry.card
    if card is None:
        return 0.0

    score = 10.0  # Base

    # Untapped is much better
    ot = card.oracle_text.lower()
    enters_tapped = "enters tapped" in ot or "enters the battlefield tapped" in ot
    if "you may pay" in ot:
        enters_tapped = False  # Shock lands — assume player pays
    if "search your library" in ot:
        enters_tapped = False  # Fetch lands

    if not enters_tapped:
        score += 20.0

    # Multi-color production is better
    produced = set(card.produced_mana)
    for face in card.faces:
        produced.update(face.produced_mana)
    color_count = len(produced & {"W", "U", "B", "R", "G"})
    score += color_count * 3.0

    # Utility lands are lower priority early
    if card.tags and CardTag.UTILITY_LAND in card.tags:
        score -= 5.0

    return score


# Upper bound on casts per turn. Rituals and draw spells feed the loop
# below more mana and more cards, so it needs a termination guard.
MAX_CASTS_PER_TURN = 40


def effective_cost(card, state: GameState) -> int:
    """Total mana cost after cost-reduction permanents (Goblin Electromancer)."""
    return max(0, int(card.display_cmc()) - state.cost_reduction)


def _count_for_descriptor(state: GameState, descriptor: str) -> int:
    """Count whatever an affinity-style cost scales with.

    Handles the descriptors that actually appear on cards — artifacts,
    creatures and lands you control, and cards in your graveyard. Anything
    we can't read counts as zero, which leaves the spell at full price
    rather than inventing a discount.
    """
    d = descriptor.lower()
    if "graveyard" in d:
        if "creature" in d:
            return sum(1 for e in state.graveyard if e.card and e.card.is_creature)
        if "instant" in d or "sorcery" in d:
            return sum(
                1 for e in state.graveyard
                if e.card and (e.card.is_instant or e.card.is_sorcery)
            )
        return len(state.graveyard)
    if "artifact" in d:
        return sum(
            1 for p in state.battlefield if p.card and p.card.is_artifact)
    if "creature" in d:
        return len(state.creatures_in_play)
    if "land" in d:
        return len(state.lands_in_play)
    return 0


def cost_discount(card, state: GameState) -> int:
    """Generic mana this card's own cost-reducing keywords can cover.

    Delve, convoke, improvise and affinity all shave the generic portion of
    a cost using a resource the board already has. The resources are spent
    for real when the spell is cast — see `GameState.pay_alternative_costs`
    — so convoking with every creature really does cost you the attack.
    """
    fx = parse_effects(card)
    discount = 0
    if fx.delve:
        discount += len(state.graveyard)
    if fx.convoke:
        discount += sum(
            1 for p in state.creatures_in_play if not p.tapped)
    if fx.improvise:
        discount += sum(
            1 for p in state.battlefield
            if p.card and p.card.is_artifact and not p.tapped)
    if fx.cost_less_amount and fx.cost_less_per:
        discount += fx.cost_less_amount * _count_for_descriptor(state, fx.cost_less_per)
    return discount


def effective_mana_cost(card, state: GameState) -> ManaCost:
    """Colour-aware cost after every reduction that applies.

    Reductions only ever touch the generic portion — {1} less does not make
    a {B}{B} spell any easier to cast off two Islands, and modelling it
    otherwise would hide exactly the colour problems this is here to expose.
    """
    cost = card_mana_cost(card)
    reduction = state.cost_reduction + cost_discount(card, state)
    if not reduction:
        return cost
    return ManaCost(
        generic=max(0, cost.generic - reduction),
        pips=cost.pips.copy(),
        hybrid=list(cost.hybrid),
    )


def _castable_now(entry, state: GameState) -> bool:
    """Can we pay for this card right now, colours included?

    Falls back to the quantity-only check for cards with no recorded mana
    cost (tokens, tests, malformed data) so missing data never blocks a cast.
    """
    card = entry.card
    if card is None or card.is_land:
        return False
    cost = effective_mana_cost(card, state)
    if cost.total == 0 and not card.mana_cost:
        return effective_cost(card, state) <= state.mana_pool
    return state.can_pay_for(cost)


def _cast_spells(state: GameState):
    """Cast as many spells as possible, prioritized by game phase.

    Re-evaluates after every cast rather than working from one snapshot of
    the hand: a ritual adds mana, a draw spell adds cards, and a mana rock
    adds a source — all of which can make a previously uncastable spell
    castable on the same turn.
    """
    state.tap_for_mana(state.available_mana)

    # Try to cast commander from command zone first (if affordable)
    _try_cast_commander(state)

    for _ in range(MAX_CASTS_PER_TURN):
        # Pick up any mana source that arrived since the last cast.
        state.tap_for_mana(state.available_mana)

        castable = [e for e in state.hand if _castable_now(e, state)]
        if not castable:
            return

        entry = max(castable, key=lambda e: _spell_priority(e, state))
        if entry.card is None:
            return
        # How much of the discount we actually leaned on — only the part
        # that reduced the bill gets charged to the board.
        printed = card_mana_cost(entry.card)
        cost = effective_mana_cost(entry.card, state)
        discount_used = max(0, printed.generic - cost.generic - state.cost_reduction)

        if not state.pay_for(cost):
            # Colours don't line up after all — pay what we can by quantity so
            # a cost we failed to parse can't wedge the loop.
            if not state.spend_mana(effective_cost(entry.card, state)):
                return
        state.pay_alternative_costs(entry.card, discount_used)
        state.cast_spell(entry)


def _try_cast_commander(state: GameState):
    """Try to cast commander from command zone."""
    if not state.command_zone:
        return

    cmd = state.command_zone[0]
    if cmd.card is None:
        return

    # Commander tax is generic, so it stacks onto the generic portion.
    cost = effective_mana_cost(cmd.card, state)
    taxed = ManaCost(
        generic=cost.generic + state.commander_tax,
        pips=cost.pips.copy(),
        hybrid=list(cost.hybrid),
    )
    if taxed.total == 0 and not cmd.card.mana_cost:
        legacy = max(0, int(cmd.card.cmc) - state.cost_reduction) + state.commander_tax
        if legacy <= state.mana_pool:
            state.spend_mana(legacy)
            state.cast_spell(cmd, from_command_zone=True)
        return
    if state.pay_for(taxed):
        state.cast_spell(cmd, from_command_zone=True)


def _spell_priority(entry: DeckEntry, state: GameState) -> float:
    """Score a spell for casting priority based on current turn and game state."""
    card = entry.card
    if card is None:
        return 0.0

    score = 0.0
    turn = state.turn
    tags = card.tags or []

    # --- Early game (T1-T3): prioritize ramp and setup ---
    if turn <= 3:
        if CardTag.MANA_ROCK in tags or CardTag.MANA_DORK in tags or CardTag.RAMP in tags:
            score += 50.0  # Ramp is king early
        if CardTag.CARD_DRAW in tags:
            score += 30.0
        if CardTag.ENGINE in tags:
            score += 25.0
        if CardTag.THREAT in tags:
            score += 15.0

    # --- Mid game (T4-T6): threats, engines, draw ---
    elif turn <= 6:
        if CardTag.ENGINE in tags:
            score += 45.0
        if CardTag.CARD_DRAW in tags:
            score += 40.0
        if CardTag.THREAT in tags:
            score += 35.0
        if CardTag.FINISHER in tags:
            score += 50.0
        if CardTag.MANA_ROCK in tags or CardTag.RAMP in tags:
            score += 15.0  # Still ok but lower priority

    # --- Late game (T7+): finishers and haymakers ---
    else:
        if CardTag.FINISHER in tags:
            score += 60.0
        if CardTag.THREAT in tags:
            score += 40.0
        if CardTag.ENGINE in tags:
            score += 35.0
        if CardTag.CARD_DRAW in tags:
            score += 30.0

    # Interaction is low priority in goldfish (no opponent)
    if CardTag.TARGETED_REMOVAL in tags or CardTag.COUNTERSPELL in tags or CardTag.BOARD_WIPE in tags:
        score += 5.0  # Cast if nothing better, just for the body if any

    # Prefer spending all mana: bonus for cards that cost exactly remaining mana
    cost = int(card.display_cmc())
    if cost == state.mana_pool:
        score += 10.0  # Perfect curve-out bonus

    # Slight bonus for higher CMC (bigger impact)
    score += card.display_cmc() * 1.5

    # Creatures get bonus for attacking
    if card.is_creature and card.power and card.power.isdigit():
        score += int(card.power) * 2.0

    return score
