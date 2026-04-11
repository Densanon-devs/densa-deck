"""Benchmark gauntlet: run a deck against a field of archetypes.

Produces a meta positioning report showing win rates per archetype,
overall weighted win rate, and strengths/weaknesses analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console

from mtg_deck_engine.matchup.archetypes import (
    ArchetypeProfile,
    get_default_gauntlet,
)
from mtg_deck_engine.matchup.simulator import MatchupResult, simulate_matchup
from mtg_deck_engine.models import Deck

console = Console()


@dataclass
class GauntletReport:
    """Complete gauntlet results: deck vs the field."""

    deck_name: str = ""
    simulations_per_matchup: int = 0
    total_games: int = 0

    # Per-matchup results
    matchups: list[MatchupResult] = field(default_factory=list)

    # Aggregate scores
    overall_win_rate: float = 0.0
    weighted_win_rate: float = 0.0  # Weighted by meta share

    # Best and worst matchups
    best_matchup: str = ""
    best_win_rate: float = 0.0
    worst_matchup: str = ""
    worst_win_rate: float = 1.0

    # Category scores (0-100)
    speed_score: float = 0.0        # How fast we close games
    resilience_score: float = 0.0    # How well we handle disruption
    interaction_score: float = 0.0   # How well we handle opponent pressure
    consistency_score: float = 0.0   # Variance in performance


def run_gauntlet(
    deck: Deck,
    archetypes: list[ArchetypeProfile] | None = None,
    simulations: int = 500,
    max_turns: int = 12,
    seed: int | None = None,
) -> GauntletReport:
    """Run a deck against a gauntlet of archetypes."""
    if archetypes is None:
        archetypes = get_default_gauntlet()

    report = GauntletReport(
        deck_name=deck.name,
        simulations_per_matchup=simulations,
    )

    console.print(f"[dim]Running gauntlet: {len(archetypes)} archetypes x {simulations} games each...[/dim]")

    matchup_seed = seed
    for arch in archetypes:
        console.print(f"  [dim]vs {arch.display_name}...[/dim]")
        result = simulate_matchup(
            deck, arch,
            simulations=simulations,
            max_turns=max_turns,
            seed=matchup_seed,
        )
        report.matchups.append(result)
        report.total_games += simulations
        if matchup_seed is not None:
            matchup_seed += 1000  # Vary seed per matchup for independence

    # Compute aggregates
    _compute_aggregates(report, archetypes)

    return report


def _compute_aggregates(report: GauntletReport, archetypes: list[ArchetypeProfile]):
    """Calculate overall scores from individual matchup results."""
    if not report.matchups:
        return

    # Simple win rate (unweighted)
    total_wins = sum(m.wins for m in report.matchups)
    total_games = sum(m.simulations for m in report.matchups)
    report.overall_win_rate = total_wins / total_games if total_games > 0 else 0.0

    # Weighted win rate (by meta share)
    weighted_wins = 0.0
    total_weight = 0.0
    arch_map = {a.display_name: a for a in archetypes}

    for m in report.matchups:
        arch = arch_map.get(m.archetype_name)
        weight = arch.meta_weight if arch else 1.0
        weighted_wins += m.win_rate * weight
        total_weight += weight

    report.weighted_win_rate = weighted_wins / total_weight if total_weight > 0 else 0.0

    # Best and worst
    best = max(report.matchups, key=lambda m: m.win_rate)
    worst = min(report.matchups, key=lambda m: m.win_rate)
    report.best_matchup = best.archetype_name
    report.best_win_rate = best.win_rate
    report.worst_matchup = worst.archetype_name
    report.worst_win_rate = worst.win_rate

    # Category scores

    # Speed: based on avg turns to win across matchups where we win
    win_turns = []
    for m in report.matchups:
        for g in m.game_results:
            if g.won:
                win_turns.append(g.turns_played)
    if not win_turns:
        # Estimate from avg_turns
        avg_turns_all = sum(m.avg_turns for m in report.matchups) / len(report.matchups)
        report.speed_score = max(0, min(100, (12 - avg_turns_all) / 12 * 100))
    else:
        avg_win_turn = sum(win_turns) / len(win_turns)
        report.speed_score = round(max(0, min(100, (12 - avg_win_turn) / 8 * 100)), 1)

    # Resilience: win rate against high-interaction archetypes
    high_interaction = [m for m in report.matchups if m.archetype_name in ("Control", "Stax", "Spellslinger")]
    if high_interaction:
        resilience_wr = sum(m.win_rate for m in high_interaction) / len(high_interaction)
        report.resilience_score = round(resilience_wr * 100, 1)
    else:
        report.resilience_score = report.overall_win_rate * 100

    # Interaction: how well we handle aggro/fast decks
    fast_matchups = [m for m in report.matchups if m.archetype_name in ("Aggro", "Voltron", "Turbo / cEDH")]
    if fast_matchups:
        interaction_wr = sum(m.win_rate for m in fast_matchups) / len(fast_matchups)
        report.interaction_score = round(interaction_wr * 100, 1)
    else:
        report.interaction_score = report.overall_win_rate * 100

    # Consistency: inverse of win rate variance
    if len(report.matchups) > 1:
        win_rates = [m.win_rate for m in report.matchups]
        avg_wr = sum(win_rates) / len(win_rates)
        variance = sum((wr - avg_wr) ** 2 for wr in win_rates) / len(win_rates)
        # Lower variance = more consistent
        report.consistency_score = round(max(0, min(100, (1 - variance * 4) * 100)), 1)
    else:
        report.consistency_score = 50.0
