"""Hypergeometric distribution calculator for MTG probability questions.

The hypergeometric distribution models drawing cards without replacement —
exactly the scenario in MTG. Given a deck of N cards containing K copies of
a target, what is the probability of drawing exactly x copies in n draws?

P(X = x) = C(K, x) * C(N-K, n-x) / C(N, n)
"""

from __future__ import annotations

from math import comb, exp, log


def hypergeometric_pmf(x: int, N: int, K: int, n: int) -> float:
    """Probability of drawing exactly x successes.

    Args:
        x: Number of successes (copies drawn).
        N: Population size (deck size).
        K: Number of successes in population (copies in deck).
        n: Number of draws (cards seen).

    Returns:
        Probability as a float in [0, 1].
    """
    if x < 0 or x > min(K, n) or x < max(0, n - (N - K)):
        return 0.0
    # Use log-space to avoid overflow with large combinatorics
    try:
        log_p = (
            _log_comb(K, x)
            + _log_comb(N - K, n - x)
            - _log_comb(N, n)
        )
        return exp(log_p)
    except (ValueError, OverflowError):
        return 0.0


def hypergeometric_cdf(x: int, N: int, K: int, n: int) -> float:
    """Probability of drawing at most x successes (cumulative)."""
    return sum(hypergeometric_pmf(i, N, K, n) for i in range(x + 1))


def prob_at_least(count: int, N: int, K: int, n: int) -> float:
    """Probability of drawing at least `count` copies in n draws.

    This is the primary question players ask: "What's the chance I see
    at least 1 copy of this card by turn X?"
    """
    if count <= 0:
        return 1.0
    return 1.0 - hypergeometric_cdf(count - 1, N, K, n)


def prob_exactly(count: int, N: int, K: int, n: int) -> float:
    """Probability of drawing exactly `count` copies in n draws."""
    return hypergeometric_pmf(count, N, K, n)


def prob_none(N: int, K: int, n: int) -> float:
    """Probability of drawing zero copies in n draws."""
    return hypergeometric_pmf(0, N, K, n)


def expected_copies(N: int, K: int, n: int) -> float:
    """Expected number of copies drawn in n draws."""
    return n * K / N if N > 0 else 0.0


def cards_seen_by_turn(turn: int, on_play: bool = True) -> int:
    """Number of cards seen by a given turn (opening hand + draws).

    Args:
        turn: The turn number (1-indexed).
        on_play: True if on the play (draw 7, no draw T1), False if on the draw.
    """
    opening_hand = 7
    if on_play:
        draws = max(0, turn - 1)  # No draw on T1 when on the play
    else:
        draws = turn  # Draw on T1 when on the draw
    return opening_hand + draws


def prob_card_by_turn(
    copies: int, deck_size: int, turn: int, on_play: bool = True, at_least: int = 1
) -> float:
    """Probability of seeing at least `at_least` copies of a card by a given turn.

    This is the most common player question: "What's the chance I draw
    my Sol Ring by turn 3?"
    """
    n = cards_seen_by_turn(turn, on_play)
    n = min(n, deck_size)  # Can't see more cards than deck has
    return prob_at_least(at_least, deck_size, copies, n)


def prob_land_by_turn(
    land_count: int, deck_size: int, turn: int, target_lands: int, on_play: bool = True
) -> float:
    """Probability of having at least `target_lands` lands by a given turn."""
    n = cards_seen_by_turn(turn, on_play)
    n = min(n, deck_size)
    return prob_at_least(target_lands, deck_size, land_count, n)


def _log_comb(n: int, k: int) -> float:
    """Log of binomial coefficient, using math.comb for accuracy."""
    c = comb(n, k)
    if c <= 0:
        return float("-inf")
    return log(c)
