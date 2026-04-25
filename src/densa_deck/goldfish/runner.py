"""Batch goldfish simulation runner.

Runs N goldfish games, aggregates results into a comprehensive report
covering damage curves, mana development, objective pass rates, and
per-turn metrics.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from rich.console import Console

from densa_deck.classification.tagger import classify_card
# Combo import goes through .models (not the package root) so we don't
# pull in httpx and the refresh-snapshot walker — goldfish should stay
# importable without network deps.
from densa_deck.combos.models import Combo
from densa_deck.goldfish.heuristics import play_turn
from densa_deck.goldfish.mulligan import mulligan_phase
from densa_deck.goldfish.objectives import (
    Objective,
    check_objectives,
    default_objectives,
)
from densa_deck.goldfish.state import GameState, TurnMetrics
from densa_deck.models import Deck

console = Console()

MAX_TURNS = 10


@dataclass
class GameResult:
    """Result of a single goldfish game."""

    mulligans_taken: int = 0
    turns_played: int = 0
    total_damage: int = 0
    kill_turn: int | None = None  # Turn opponent reached 0
    commander_cast_turn: int | None = None
    total_spells_cast: int = 0
    total_lands_played: int = 0
    total_mana_spent: int = 0
    turn_metrics: list[TurnMetrics] = field(default_factory=list)
    objectives_met: dict[str, bool] = field(default_factory=dict)
    objectives_met_turn: dict[str, int | None] = field(default_factory=dict)
    # Combo-aware tracking — populated when run_goldfish_batch was given
    # a `combos` list. combo_win_turn = first turn where every card of
    # any matching combo is in possession (battlefield + hand + graveyard
    # all together), simulating "the player can assemble + fire the
    # combo this turn." combo_id is whichever combo fired first.
    combo_win_turn: int | None = None
    combo_id_fired: str | None = None


@dataclass
class GoldfishReport:
    """Aggregated results from a batch of goldfish games."""

    simulations: int = 0
    max_turns: int = MAX_TURNS

    # Mulligan stats
    average_mulligans: float = 0.0
    mulligan_distribution: dict[int, float] = field(default_factory=dict)

    # Damage stats
    average_damage_by_turn: dict[int, float] = field(default_factory=dict)
    average_kill_turn: float = 0.0
    kill_rate: float = 0.0  # % of games that dealt 40+ damage
    kill_turn_distribution: dict[int, float] = field(default_factory=dict)

    # Board development
    average_creatures_by_turn: dict[int, float] = field(default_factory=dict)
    average_lands_by_turn: dict[int, float] = field(default_factory=dict)
    average_mana_spent_by_turn: dict[int, float] = field(default_factory=dict)
    average_cards_cast_by_turn: dict[int, float] = field(default_factory=dict)

    # Commander stats
    commander_cast_rate: float = 0.0
    average_commander_turn: float = 0.0

    # Spells
    average_spells_cast: float = 0.0
    most_cast_spells: list[tuple[str, int]] = field(default_factory=list)

    # Objectives
    objective_pass_rates: dict[str, float] = field(default_factory=dict)

    # Combo wins (only populated when run_goldfish_batch was given combos)
    combos_evaluated: int = 0  # how many combo lines we checked against
    combo_win_rate: float = 0.0  # % of games where any combo was assembled
    average_combo_win_turn: float = 0.0
    combo_win_turn_distribution: dict[int, float] = field(default_factory=dict)
    # Top combo lines by frequency, with their hit-rate. Each tuple is
    # (combo_id, short_label, count, rate). Useful for surfacing
    # "your most-fired combo across 1000 games was X" in the UI.
    top_combo_lines: list[tuple[str, str, int, float]] = field(default_factory=list)

    # Per-game results (for advanced analysis)
    game_results: list[GameResult] = field(default_factory=list)


def run_goldfish_batch(
    deck: Deck,
    simulations: int = 1000,
    max_turns: int = MAX_TURNS,
    objectives: list[Objective] | None = None,
    seed: int | None = None,
    store_games: bool = False,
    combos: list[Combo] | None = None,
) -> GoldfishReport:
    """Run a batch of goldfish simulations and aggregate results.

    `combos` (optional): list of Commander Spellbook combos to track
    during simulation. When provided, each game also records the
    earliest turn at which all cards of any combo are in the player's
    possession (battlefield + hand + graveyard) — a fair proxy for
    "the deck could have fired this combo by this turn." When omitted
    or empty, the report's combo_* fields stay at their defaults.
    """
    if seed is not None:
        random.seed(seed)

    # Ensure cards are classified (copy tags to avoid mutating shared Card objects)
    for entry in deck.entries:
        if entry.card and not entry.card.tags:
            entry.card.tags = list(classify_card(entry.card))

    # Generate default objectives if none provided
    if objectives is None:
        objectives = default_objectives(deck)

    # Pre-filter combos to those whose pieces all appear in this deck —
    # nothing else can possibly fire, and skipping them up front avoids a
    # per-turn check loop over irrelevant entries on every simulation.
    deck_card_names = {e.card.name for e in deck.entries if e.card}
    relevant_combos: list[Combo] = []
    if combos:
        for c in combos:
            if c.cards and all(name in deck_card_names for name in c.cards):
                relevant_combos.append(c)

    results: list[GameResult] = []
    spell_counter: Counter[str] = Counter()

    for _ in range(simulations):
        # Reset objectives for this game
        game_objectives = [
            Objective(
                name=o.name,
                type=o.type,
                target_turn=o.target_turn,
                target_value=o.target_value,
            )
            for o in objectives
        ]

        result = _run_single_game(deck, max_turns, game_objectives, relevant_combos)
        results.append(result)

        # Track spell frequency
        for tm in result.turn_metrics:
            for spell in tm.spells_cast:
                spell_counter[spell] += 1

    # Aggregate
    report = _aggregate_results(results, simulations, max_turns, objectives, spell_counter)
    report.combos_evaluated = len(relevant_combos)
    _aggregate_combo_results(report, results, simulations, relevant_combos)
    if store_games:
        report.game_results = results

    return report


def _run_single_game(
    deck: Deck,
    max_turns: int,
    objectives: list[Objective],
    combos: list[Combo] | None = None,
) -> GameResult:
    """Run a single goldfish game.

    When `combos` is non-empty, after each turn we check whether all
    cards of any tracked combo are in the player's possession (in
    battlefield, hand, or graveyard). The first turn this is true for
    any combo is recorded as combo_win_turn — simulating "by this
    turn the player could fire the combo line." We record only the
    FIRST combo to fire because the goldfish doesn't have a real
    decision model for which combo a player would prefer; surfacing
    "the earliest one" matches the kill-turn convention.
    """
    state = GameState()
    is_commander = deck.format and deck.format.value in ("commander", "brawl", "oathbreaker", "duel")
    state.life = 40 if is_commander else 20
    state.opponent_life = 40 if is_commander else 20

    # Setup library
    state.setup_library(deck.entries)

    # Pre-build a map from combo to the lower-cased card-name set so the
    # per-turn check is a single subset comparison instead of iterating
    # combo.cards each call. Skipped when combos is empty.
    combo_index: list[tuple[Combo, frozenset[str]]] = []
    combo_card_names: set[str] = set()
    if combos:
        combo_index = [(c, frozenset(name.lower() for name in c.cards)) for c in combos]
        for c in combos:
            for name in c.cards:
                combo_card_names.add(name.lower())

    # Mulligan phase — combo-aware so a deck with infinite combos prefers
    # to keep hands containing combo pieces (and never bottoms them).
    mulls = mulligan_phase(state, deck, combo_card_names=combo_card_names or None)

    combo_win_turn: int | None = None
    combo_id_fired: str | None = None

    # Play turns
    for _ in range(max_turns):
        state.begin_turn()
        play_turn(state)
        metrics = state.end_turn()

        # Check objectives
        check_objectives(state, objectives)

        # Check combo assembly — only if we haven't already fired one this
        # game. "In possession" = battlefield + hand + graveyard. We don't
        # check the command zone separately because the commander is
        # always treated as "available" via casting; if a combo requires
        # the commander, casting it puts it on the battlefield where this
        # check picks it up.
        if combo_index and combo_win_turn is None:
            possessed = _possessed_card_names(state)
            for combo, combo_set in combo_index:
                if combo_set.issubset(possessed):
                    combo_win_turn = state.turn
                    combo_id_fired = combo.combo_id
                    break

        if state.game_over:
            break

    # Build result
    result = GameResult(
        mulligans_taken=mulls,
        turns_played=state.turn,
        total_damage=state.total_damage_dealt,
        kill_turn=state.turn if state.opponent_life <= 0 else None,
        commander_cast_turn=state.commander_cast_turn,
        total_spells_cast=sum(m.cards_cast for m in state.turn_history),
        total_lands_played=sum(1 for m in state.turn_history if m.land_played),
        total_mana_spent=sum(m.mana_spent for m in state.turn_history),
        turn_metrics=list(state.turn_history),
        objectives_met={o.name: o.met for o in objectives},
        objectives_met_turn={o.name: o.met_on_turn for o in objectives},
        combo_win_turn=combo_win_turn,
        combo_id_fired=combo_id_fired,
    )

    return result


def _possessed_card_names(state: GameState) -> frozenset[str]:
    """Lowercased card-name set for everything currently in the player's
    possession (battlefield + hand + graveyard).

    Used by the combo-aware turn check. Excluded zones: library (unknown
    to the player) and exile (out of reach for combo assembly without
    further effects).
    """
    names: set[str] = set()
    for p in state.battlefield:
        if p.card:
            names.add(p.card.name.lower())
    for entry in state.hand:
        if entry.card:
            names.add(entry.card.name.lower())
    for entry in state.graveyard:
        if entry.card:
            names.add(entry.card.name.lower())
    return frozenset(names)


def _aggregate_combo_results(
    report: GoldfishReport,
    results: list[GameResult],
    simulations: int,
    combos: list[Combo],
) -> None:
    """Fold combo-fire data into the report. No-op when combos is empty."""
    if not combos or simulations <= 0:
        return
    fired = [r.combo_win_turn for r in results if r.combo_win_turn is not None]
    if not fired:
        return
    report.combo_win_rate = round(len(fired) / simulations, 4)
    report.average_combo_win_turn = round(sum(fired) / len(fired), 2)
    dist: Counter[int] = Counter(fired)
    report.combo_win_turn_distribution = {k: round(v / simulations, 4) for k, v in sorted(dist.items())}

    # Top combo lines by frequency. Build a lookup so we can resolve
    # combo_id → short label without hitting the combo store again.
    label_for: dict[str, str] = {}
    for c in combos:
        label_for[c.combo_id] = c.short_label()
    fire_counter: Counter[str] = Counter(
        r.combo_id_fired for r in results if r.combo_id_fired
    )
    report.top_combo_lines = [
        (cid, label_for.get(cid, cid), count, round(count / simulations, 4))
        for cid, count in fire_counter.most_common(5)
    ]


def _aggregate_results(
    results: list[GameResult],
    simulations: int,
    max_turns: int,
    objectives: list[Objective],
    spell_counter: Counter,
) -> GoldfishReport:
    """Aggregate individual game results into a report."""
    report = GoldfishReport(simulations=simulations, max_turns=max_turns)

    if not results:
        return report

    # Mulligan stats
    mull_counts = [r.mulligans_taken for r in results]
    report.average_mulligans = sum(mull_counts) / len(mull_counts)
    mull_dist: Counter[int] = Counter(mull_counts)
    report.mulligan_distribution = {k: v / simulations for k, v in sorted(mull_dist.items())}

    # Per-turn aggregation
    for turn in range(1, max_turns + 1):
        damages = []
        creatures = []
        lands = []
        mana_spent = []
        cards_cast = []

        for r in results:
            if turn <= len(r.turn_metrics):
                tm = r.turn_metrics[turn - 1]
                damages.append(tm.cumulative_damage)
                creatures.append(tm.creatures_in_play)
                lands.append(tm.lands_in_play)
                mana_spent.append(tm.mana_spent)
                cards_cast.append(tm.cards_cast)
            else:
                # Game ended before this turn
                if r.turn_metrics:
                    last = r.turn_metrics[-1]
                    damages.append(last.cumulative_damage)
                    creatures.append(last.creatures_in_play)
                    lands.append(last.lands_in_play)

        if damages:
            report.average_damage_by_turn[turn] = round(sum(damages) / len(damages), 1)
        if creatures:
            report.average_creatures_by_turn[turn] = round(sum(creatures) / len(creatures), 1)
        if lands:
            report.average_lands_by_turn[turn] = round(sum(lands) / len(lands), 1)
        if mana_spent:
            report.average_mana_spent_by_turn[turn] = round(sum(mana_spent) / len(mana_spent), 1)
        if cards_cast:
            report.average_cards_cast_by_turn[turn] = round(sum(cards_cast) / len(cards_cast), 1)

    # Kill stats
    kill_turns = [r.kill_turn for r in results if r.kill_turn is not None]
    report.kill_rate = len(kill_turns) / simulations
    if kill_turns:
        report.average_kill_turn = round(sum(kill_turns) / len(kill_turns), 1)
        kill_dist: Counter[int] = Counter(kill_turns)
        report.kill_turn_distribution = {k: v / simulations for k, v in sorted(kill_dist.items())}

    # Commander stats
    cmd_turns = [r.commander_cast_turn for r in results if r.commander_cast_turn is not None]
    report.commander_cast_rate = len(cmd_turns) / simulations
    if cmd_turns:
        report.average_commander_turn = round(sum(cmd_turns) / len(cmd_turns), 1)

    # Spell stats
    all_spells = [r.total_spells_cast for r in results]
    report.average_spells_cast = round(sum(all_spells) / len(all_spells), 1)
    report.most_cast_spells = spell_counter.most_common(10)

    # Objective pass rates
    for obj in objectives:
        passed = sum(1 for r in results if r.objectives_met.get(obj.name, False))
        report.objective_pass_rates[obj.name] = round(passed / simulations, 4)

    return report
