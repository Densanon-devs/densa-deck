"""Tests for hypergeometric probability calculator."""

import pytest

from mtg_deck_engine.probability.hypergeometric import (
    cards_seen_by_turn,
    expected_copies,
    hypergeometric_cdf,
    hypergeometric_pmf,
    prob_at_least,
    prob_card_by_turn,
    prob_land_by_turn,
    prob_none,
)


def test_pmf_basic():
    """Drawing 1 of 4 copies from 60 cards in 7 draws."""
    p = hypergeometric_pmf(1, 60, 4, 7)
    assert 0.3 < p < 0.4  # ~0.356


def test_pmf_impossible():
    """Can't draw 5 copies when only 4 exist."""
    assert hypergeometric_pmf(5, 60, 4, 7) == 0.0


def test_pmf_zero():
    """Probability of drawing 0 of 4 in 7 from 60."""
    p = hypergeometric_pmf(0, 60, 4, 7)
    assert 0.5 < p < 0.7  # ~0.603


def test_cdf_sums_to_pmf():
    """CDF at max should equal sum of all PMFs."""
    total = sum(hypergeometric_pmf(x, 60, 4, 7) for x in range(5))
    cdf = hypergeometric_cdf(4, 60, 4, 7)
    assert abs(total - cdf) < 1e-10


def test_prob_at_least_one():
    """Chance of seeing at least 1 of 4 copies in 7 cards from 60."""
    p = prob_at_least(1, 60, 4, 7)
    expected = 1.0 - hypergeometric_pmf(0, 60, 4, 7)
    assert abs(p - expected) < 1e-10


def test_prob_at_least_zero_always_one():
    """At least 0 should always be 1.0."""
    assert prob_at_least(0, 60, 4, 7) == 1.0


def test_prob_none():
    """prob_none should match pmf(0)."""
    assert prob_none(60, 4, 7) == hypergeometric_pmf(0, 60, 4, 7)


def test_expected_copies():
    """Expected copies = n * K / N."""
    e = expected_copies(60, 4, 7)
    assert abs(e - 7 * 4 / 60) < 1e-10


def test_cards_seen_by_turn_on_play():
    """On play: 7 cards T1, 8 T2, 9 T3."""
    assert cards_seen_by_turn(1, on_play=True) == 7
    assert cards_seen_by_turn(2, on_play=True) == 8
    assert cards_seen_by_turn(3, on_play=True) == 9


def test_cards_seen_by_turn_on_draw():
    """On draw: 8 cards T1, 9 T2, 10 T3."""
    assert cards_seen_by_turn(1, on_play=False) == 8
    assert cards_seen_by_turn(2, on_play=False) == 9
    assert cards_seen_by_turn(3, on_play=False) == 10


def test_prob_card_by_turn():
    """Sol Ring (1 copy) by turn 1 in 99-card Commander deck."""
    p = prob_card_by_turn(1, 99, 1, on_play=True)
    # 7 cards seen / 99 in deck = ~7.07%
    assert 0.06 < p < 0.08


def test_prob_card_by_turn_4_copies():
    """4 copies by turn 1 in 60-card deck."""
    p = prob_card_by_turn(4, 60, 1, on_play=True)
    # 1 - P(0 of 4 in 7 from 60) ≈ 39.7%
    assert 0.35 < p < 0.45


def test_prob_land_by_turn():
    """24 lands in 60-card deck, chance of 2+ by turn 2 on play."""
    p = prob_land_by_turn(24, 60, 2, 2, on_play=True)
    # Should be high (>85%)
    assert p > 0.80


def test_prob_land_by_turn_screw():
    """18 lands in 60-card deck, chance of 3+ by turn 3 on play."""
    p = prob_land_by_turn(18, 60, 3, 3, on_play=True)
    # Should be moderate with only 18 lands
    assert 0.3 < p < 0.7


def test_commander_singleton():
    """1 copy in 99 cards, chance of seeing it by turn 5 on play."""
    p = prob_card_by_turn(1, 99, 5, on_play=True)
    # 11 cards seen / 99 ≈ 11.1%
    assert 0.10 < p < 0.13
