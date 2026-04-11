"""Mana development probability calculator.

Answers the core mana consistency questions:
- Can I hit my land drops on turns 1-5?
- Can I cast my commander on curve?
- What's the chance of mana screw / flood?
- How does ramp affect my effective mana development?
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mtg_deck_engine.models import CardTag, Deck, Format, Zone
from mtg_deck_engine.probability.hypergeometric import (
    expected_copies,
    prob_at_least,
    prob_land_by_turn,
    cards_seen_by_turn,
)


@dataclass
class ManaDevelopmentReport:
    """Mana development probabilities across turns 1-10."""

    deck_size: int = 0
    land_count: int = 0
    ramp_count: int = 0
    mana_source_count: int = 0

    # Core probabilities
    land_by_turn: dict[int, float] = field(default_factory=dict)
    mana_screw_rate: float = 0.0   # <2 lands by T3
    mana_flood_rate: float = 0.0   # 6+ lands by T6
    commander_on_curve: float = 0.0
    commander_cmc: float = 0.0

    # Turn-by-turn expected mana
    expected_lands_by_turn: dict[int, float] = field(default_factory=dict)
    expected_mana_by_turn: dict[int, float] = field(default_factory=dict)

    # Milestone probabilities
    two_lands_by_t2: float = 0.0
    three_lands_by_t3: float = 0.0
    four_mana_by_t4: float = 0.0
    five_mana_by_t5: float = 0.0


def analyze_mana_development(deck: Deck, on_play: bool = True) -> ManaDevelopmentReport:
    """Calculate mana development probabilities for a deck."""
    report = ManaDevelopmentReport()
    is_commander = deck.format in (Format.COMMANDER, Format.BRAWL, Format.OATHBREAKER, Format.DUEL)

    # Count lands and mana sources
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD)]
    report.deck_size = sum(e.quantity for e in active)

    for entry in active:
        card = entry.card
        if card is None:
            continue
        if card.is_land:
            report.land_count += entry.quantity
        if card.tags:
            if any(t in card.tags for t in (CardTag.RAMP, CardTag.MANA_ROCK, CardTag.MANA_DORK)):
                report.ramp_count += entry.quantity

    report.mana_source_count = report.land_count + report.ramp_count

    if report.deck_size == 0:
        return report

    N = report.deck_size
    K_land = report.land_count
    K_ramp = report.ramp_count

    # Land-drop probabilities by turn (hitting at least N lands by turn N)
    for turn in range(1, 11):
        n = cards_seen_by_turn(turn, on_play)
        n = min(n, N)
        # Chance of having at least `turn` lands by turn `turn`
        target_lands = min(turn, K_land)
        p = prob_at_least(target_lands, N, K_land, n)
        report.land_by_turn[turn] = round(p, 4)

    # Expected lands by turn
    for turn in range(1, 11):
        n = cards_seen_by_turn(turn, on_play)
        n = min(n, N)
        report.expected_lands_by_turn[turn] = round(expected_copies(N, K_land, n), 2)

    # Expected mana by turn (lands + ramp approximation)
    # Simplified: ramp adds ~1 mana if drawn by the turn it can be cast (assume T2 for rocks, T1 for dorks)
    for turn in range(1, 11):
        n = cards_seen_by_turn(turn, on_play)
        n = min(n, N)
        exp_lands = expected_copies(N, K_land, n)
        # Ramp available by this turn: expect some fraction of ramp pieces
        if turn >= 2 and K_ramp > 0:
            # Cards seen by turn-1 (when ramp could have been cast)
            n_prev = cards_seen_by_turn(max(1, turn - 1), on_play)
            n_prev = min(n_prev, N)
            exp_ramp = expected_copies(N, K_ramp, n_prev)
            report.expected_mana_by_turn[turn] = round(exp_lands + exp_ramp * 0.8, 2)
        else:
            report.expected_mana_by_turn[turn] = round(exp_lands, 2)

    # Key milestones
    n_t2 = min(cards_seen_by_turn(2, on_play), N)
    n_t3 = min(cards_seen_by_turn(3, on_play), N)
    n_t4 = min(cards_seen_by_turn(4, on_play), N)
    n_t5 = min(cards_seen_by_turn(5, on_play), N)

    report.two_lands_by_t2 = round(prob_at_least(2, N, K_land, n_t2), 4)
    report.three_lands_by_t3 = round(prob_at_least(3, N, K_land, n_t3), 4)

    # Four mana by T4: either 4 lands, or 3 lands + 1 ramp (simplified)
    p_4_lands = prob_at_least(4, N, K_land, n_t4)
    # P(3+ lands by T4) * P(1+ ramp by T3) for ramp-assisted
    p_3_lands_t4 = prob_at_least(3, N, K_land, n_t4)
    n_t3_for_ramp = min(cards_seen_by_turn(3, on_play), N)
    p_ramp_by_t3 = prob_at_least(1, N, K_ramp, n_t3_for_ramp) if K_ramp > 0 else 0.0
    # Union approximation: P(4 lands) + P(3 lands AND ramp) - overlap
    report.four_mana_by_t4 = round(min(1.0, p_4_lands + p_3_lands_t4 * p_ramp_by_t3 * 0.7), 4)

    # Five mana by T5: similar approach
    p_5_lands = prob_at_least(5, N, K_land, n_t5)
    p_4_lands_t5 = prob_at_least(4, N, K_land, n_t5)
    n_t4_for_ramp = min(cards_seen_by_turn(4, on_play), N)
    p_ramp_by_t4 = prob_at_least(1, N, K_ramp, n_t4_for_ramp) if K_ramp > 0 else 0.0
    report.five_mana_by_t5 = round(min(1.0, p_5_lands + p_4_lands_t5 * p_ramp_by_t4 * 0.7), 4)

    # Mana screw: fewer than 2 lands by turn 3
    report.mana_screw_rate = round(1.0 - prob_at_least(2, N, K_land, n_t3), 4)

    # Mana flood: 6+ lands in first 10 cards (roughly T3-T4 range)
    n_10 = min(10, N)
    report.mana_flood_rate = round(prob_at_least(6, N, K_land, n_10), 4)

    # Commander on curve
    if deck.commanders:
        cmd = deck.commanders[0]
        if cmd.card:
            cmd_cmc = int(cmd.card.cmc)
            report.commander_cmc = cmd.card.cmc
            if cmd_cmc > 0:
                # Need cmd_cmc mana by turn cmd_cmc
                n_cmd = min(cards_seen_by_turn(cmd_cmc, on_play), N)
                # Probability of having enough mana (lands + ramp)
                p_lands = prob_at_least(cmd_cmc, N, K_land, n_cmd)
                # With ramp assist
                p_lands_minus1 = prob_at_least(cmd_cmc - 1, N, K_land, n_cmd)
                n_ramp_window = min(cards_seen_by_turn(max(1, cmd_cmc - 1), on_play), N)
                p_ramp = prob_at_least(1, N, K_ramp, n_ramp_window) if K_ramp > 0 else 0.0
                report.commander_on_curve = round(
                    min(1.0, p_lands + p_lands_minus1 * p_ramp * 0.7), 4
                )

    return report
