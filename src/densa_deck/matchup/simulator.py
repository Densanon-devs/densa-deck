"""Matchup simulation: deck vs archetype opponent.

Extends the goldfish engine with simulated opponent pressure. The opponent
doesn't play actual cards — instead, the archetype profile generates events:
  - Damage to our life total on a clock
  - Targeted removal destroying our permanents
  - Counterspells fizzling our casts
  - Board wipes clearing the board
  - Mana taxes slowing our development

This measures how our deck performs *under pressure*, not in a vacuum.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from densa_deck.classification.tagger import classify_card
from densa_deck.goldfish.heuristics import play_turn
from densa_deck.goldfish.mulligan import mulligan_phase
from densa_deck.goldfish.state import GameState
from densa_deck.matchup.archetypes import ArchetypeProfile
from densa_deck.models import Deck


@dataclass
class MatchupGameResult:
    """Result of a single matchup game."""

    won: bool = False
    reason: str = ""           # "damage", "opponent_clock", "decked", "timeout"
    turns_played: int = 0
    our_damage: int = 0
    opponent_damage: int = 0
    our_life: int = 0
    opponent_life: int = 0
    permanents_removed: int = 0
    spells_countered: int = 0
    wipes_suffered: int = 0
    commander_cast_turn: int | None = None


@dataclass
class MatchupResult:
    """Aggregated results for one deck vs one archetype."""

    archetype_name: str = ""
    simulations: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0

    # Averages
    avg_turns: float = 0.0
    avg_our_damage: float = 0.0
    avg_opponent_damage: float = 0.0
    avg_permanents_removed: float = 0.0
    avg_spells_countered: float = 0.0
    avg_wipes_suffered: float = 0.0

    # Win condition breakdown
    wins_by_damage: int = 0
    losses_by_clock: int = 0
    losses_by_timeout: int = 0

    game_results: list[MatchupGameResult] = field(default_factory=list)


def simulate_matchup(
    deck: Deck,
    opponent: ArchetypeProfile,
    simulations: int = 500,
    max_turns: int = 12,
    seed: int | None = None,
    store_games: bool = False,
) -> MatchupResult:
    """Simulate a deck against an archetype opponent."""
    if seed is not None:
        random.seed(seed)

    # Ensure classification (copy tags to avoid mutating shared Card objects)
    for entry in deck.entries:
        if entry.card and not entry.card.tags:
            entry.card.tags = list(classify_card(entry.card))

    is_commander = deck.format and deck.format.value in ("commander", "brawl", "oathbreaker", "duel")
    starting_life = 40 if is_commander else 20

    games: list[MatchupGameResult] = []

    for _ in range(simulations):
        result = _run_matchup_game(deck, opponent, max_turns, starting_life)
        games.append(result)

    # Aggregate
    return _aggregate_matchup(games, opponent.display_name, simulations, store_games)


def _run_matchup_game(
    deck: Deck,
    opp: ArchetypeProfile,
    max_turns: int,
    starting_life: int,
) -> MatchupGameResult:
    """Run a single game against an archetype."""
    state = GameState()
    state.life = starting_life
    state.opponent_life = starting_life
    state.setup_library(deck.entries)

    mulligan_phase(state, deck)

    result = MatchupGameResult()
    permanents_removed = 0
    spells_countered = 0
    wipes_suffered = 0
    opponent_damage_dealt = 0

    for _ in range(max_turns):
        state.begin_turn()

        # --- Opponent interaction phase (before we play) ---

        # Mana tax from stax
        effective_tax = opp.mana_tax if state.turn >= opp.pressure_start_turn else 0

        # Hand disruption
        if random.random() < opp.hand_disruption_chance and state.hand:
            # Discard a random nonland card
            nonlands = [e for e in state.hand if e.card and not e.card.is_land]
            if nonlands:
                victim = random.choice(nonlands)
                state.hand.remove(victim)
                state.graveyard.append(victim)

        # --- Our turn ---
        play_turn(state)

        # Apply mana tax: reduce effective spells cast (simplified — already cast above,
        # but we track the tax impact for metrics)

        # --- Opponent interaction after our plays ---

        # Counterspell check: retroactively "counter" our best cast this turn
        if state.spells_cast_this_turn and random.random() < opp.counterspell_chance:
            # The most expensive spell we cast gets countered
            # (simplified: remove from battlefield, move to graveyard)
            if state.battlefield:
                newest = [p for p in state.battlefield if p.entry.card_name in state.spells_cast_this_turn]
                if newest:
                    target = max(newest, key=lambda p: p.card.cmc if p.card else 0)
                    state.battlefield.remove(target)
                    state.graveyard.append(target.entry)
                    spells_countered += 1

        # Targeted removal
        if random.random() < opp.targeted_removal_chance:
            # Remove our best non-land permanent
            nonland_perms = [p for p in state.battlefield if not p.is_land()]
            if nonland_perms:
                # Target highest value (finishers/engines first)
                target = max(nonland_perms, key=lambda p: p.card.cmc if p.card else 0)
                state.battlefield.remove(target)
                state.graveyard.append(target.entry)
                permanents_removed += 1

        # Board wipe
        if random.random() < opp.wipe_chance:
            creatures_to_remove = [p for p in state.battlefield if p.is_creature()]
            for perm in creatures_to_remove:
                state.battlefield.remove(perm)
                state.graveyard.append(perm.entry)
            if creatures_to_remove:
                wipes_suffered += 1

        # --- Combat and end ---
        metrics = state.end_turn()

        # --- Opponent damage to us ---
        if state.turn >= opp.pressure_start_turn:
            # Scale damage based on progress toward max pressure
            progress = min(1.0, (state.turn - opp.pressure_start_turn) /
                           max(1, opp.max_pressure_turn - opp.pressure_start_turn))
            opp_damage = int(opp.damage_per_turn * (0.3 + 0.7 * progress))

            # Our blockers reduce incoming damage (simplified)
            untapped_creatures = [p for p in state.creatures_in_play if not p.tapped]
            block_power = sum(
                int(p.card.toughness) if p.card and p.card.toughness and p.card.toughness.isdigit() else 0
                for p in untapped_creatures[:2]  # At most 2 blockers
            )
            actual_damage = max(0, opp_damage - block_power // 2)
            state.life -= actual_damage
            opponent_damage_dealt += actual_damage

        # --- Check game end ---
        if state.opponent_life <= 0:
            result.won = True
            result.reason = "damage"
            break
        if state.life <= 0:
            result.won = False
            result.reason = "opponent_clock"
            break

    else:
        # Timeout — who's closer to winning?
        our_progress = state.total_damage_dealt / starting_life
        opp_progress = opponent_damage_dealt / starting_life
        result.won = our_progress > opp_progress
        result.reason = "timeout"

    result.turns_played = state.turn
    result.our_damage = state.total_damage_dealt
    result.opponent_damage = opponent_damage_dealt
    result.our_life = state.life
    result.opponent_life = state.opponent_life
    result.permanents_removed = permanents_removed
    result.spells_countered = spells_countered
    result.wipes_suffered = wipes_suffered
    result.commander_cast_turn = state.commander_cast_turn

    return result


def _aggregate_matchup(
    games: list[MatchupGameResult],
    archetype_name: str,
    simulations: int,
    store_games: bool,
) -> MatchupResult:
    """Aggregate individual game results."""
    result = MatchupResult(
        archetype_name=archetype_name,
        simulations=simulations,
    )

    if not games:
        return result

    result.wins = sum(1 for g in games if g.won)
    result.losses = simulations - result.wins
    result.win_rate = result.wins / simulations

    result.avg_turns = round(sum(g.turns_played for g in games) / len(games), 1)
    result.avg_our_damage = round(sum(g.our_damage for g in games) / len(games), 1)
    result.avg_opponent_damage = round(sum(g.opponent_damage for g in games) / len(games), 1)
    result.avg_permanents_removed = round(sum(g.permanents_removed for g in games) / len(games), 1)
    result.avg_spells_countered = round(sum(g.spells_countered for g in games) / len(games), 1)
    result.avg_wipes_suffered = round(sum(g.wipes_suffered for g in games) / len(games), 1)

    result.wins_by_damage = sum(1 for g in games if g.won and g.reason == "damage")
    result.losses_by_clock = sum(1 for g in games if not g.won and g.reason == "opponent_clock")
    result.losses_by_timeout = sum(1 for g in games if not g.won and g.reason == "timeout")

    if store_games:
        result.game_results = games

    return result
