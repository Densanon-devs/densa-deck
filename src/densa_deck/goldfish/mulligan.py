"""London mulligan implementation for goldfish simulation.

London mulligan rules:
1. Draw 7 cards
2. Evaluate hand — keep or mulligan
3. If mulligan: shuffle hand back, draw 7 again, put N cards on bottom
4. Repeat up to 3 times (keep at 4 cards minimum)

Uses the opening hand evaluator from Phase 2 for keepability scoring.
"""

from __future__ import annotations

import random

from densa_deck.goldfish.state import GameState
from densa_deck.models import Deck
from densa_deck.probability.opening_hand import evaluate_hand


def mulligan_phase(
    state: GameState,
    deck: Deck,
    min_keep_score: float = 40.0,
    *,
    combo_card_names: set[str] | None = None,
) -> int:
    """Execute the mulligan phase. Returns number of mulligans taken.

    Optional `combo_card_names`: lower-cased card names that participate
    in any of the deck's known combo lines. When provided, the bottom-
    cards step preferentially KEEPS those cards (they become combo-piece
    targets) and the keep decision tolerates a slightly lower base score
    when 2+ combo pieces are already in hand. Combo decks frequently
    keep 5-land hands with 2 combo pieces — opening_hand.evaluate_hand
    doesn't know about combos so the floor was too high before.
    """
    mulligans = 0
    max_mulligans = 3
    combo_set = combo_card_names or set()

    for attempt in range(max_mulligans + 1):
        # Draw 7
        hand = state.draw(7)

        # Evaluate
        ev = evaluate_hand(hand, deck)

        # Combo-aware keep override: if the hand already contains 2+
        # combo pieces, the player would generally keep even on a
        # slightly weaker non-combo metric. We require still passing a
        # softened floor (30) so we don't keep a 1-land hand just for
        # combo pieces.
        combo_pieces_in_hand = sum(
            1 for e in hand
            if e.card and e.card.name.lower() in combo_set
        )
        combo_keep = (
            len(combo_set) > 0
            and combo_pieces_in_hand >= 2
            and ev.score >= 30.0
        )

        if ev.keepable or combo_keep or attempt == max_mulligans:
            # Keep — but bottom N cards for mulligans taken
            if mulligans > 0:
                _bottom_cards(state, mulligans, combo_card_names=combo_set)
            state.mulligans_taken = mulligans
            return mulligans

        # Mulligan — put hand back and reshuffle
        mulligans += 1
        state.hand.clear()
        state.library = hand + state.library
        random.shuffle(state.library)

    return mulligans


def _bottom_cards(state: GameState, count: int, *, combo_card_names: set[str] | None = None):
    """Put the N worst cards from hand on the bottom of the library.

    Bottoming strategy:
    - Bottom highest-CMC spells first (can't cast them early anyway)
    - Never bottom the last land
    - Never bottom ramp if we have few lands
    - When combo_card_names is set, NEVER bottom a combo piece (these
      are the deck's win condition; mulled-down hands still want to
      keep their combo cards)
    """
    if count <= 0 or not state.hand:
        return

    combo_set = combo_card_names or set()
    hand = list(state.hand)
    lands_in_hand = sum(1 for e in hand if e.card and e.card.is_land)

    # Score each card: lower score = more likely to bottom
    scored = []
    for entry in hand:
        card = entry.card
        if card is None:
            scored.append((entry, -100.0))  # Bottom unknown cards first
            continue

        keep_score = 0.0

        # Lands are valuable
        if card.is_land:
            if lands_in_hand <= 2:
                keep_score += 100.0  # Never bottom if we're land-light
            else:
                keep_score += 30.0

        # Low-cost spells are more keepable
        keep_score += max(0, 8 - card.display_cmc()) * 5.0

        # Ramp is very keepable
        from densa_deck.models import CardTag
        if card.tags and (CardTag.RAMP in card.tags or CardTag.MANA_ROCK in card.tags):
            keep_score += 25.0

        # Card draw is keepable
        if card.tags and CardTag.CARD_DRAW in card.tags:
            keep_score += 15.0

        # Combo pieces — pinned. Use a score above the "never bottom"
        # threshold for lands so combo pieces always rank ahead of
        # everything else when we sort.
        if combo_set and card.name.lower() in combo_set:
            keep_score += 200.0

        scored.append((entry, keep_score))

    # Sort by keep score ascending — bottom the lowest-scored cards
    scored.sort(key=lambda x: x[1])

    bottomed = 0
    for entry, _ in scored:
        if bottomed >= count:
            break
        state.hand.remove(entry)
        state.library.append(entry)
        bottomed += 1
