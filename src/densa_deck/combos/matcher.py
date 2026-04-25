"""Match a deck against the cached combo dataset.

Given a `Deck` (resolved cards) and a `ComboStore`, return the combos
whose `cards` requirement is FULLY satisfied by the deck. We don't try
to match `templates` (free-form Scryfall queries) here — those are
opaque to a deck-list matcher. A combo with templates is only matched
if its concrete cards alone are enough.

Color-identity check: only return combos whose color_identity is a
subset of the deck's color identity. This avoids surfacing a Mardu combo
in a Bant deck just because both decks happen to run a generic artifact
piece.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from densa_deck.combos.data import ComboStore
from densa_deck.combos.models import Combo


@dataclass
class MatchedCombo:
    combo: Combo
    in_deck_cards: list[str] = field(default_factory=list)
    # `unsatisfied_templates` is the count of upstream `requires:` entries
    # we couldn't verify (because templates are free-form Scryfall queries).
    # Surface this so the UI can mark a combo as "templates may apply" vs
    # "fully concrete".
    unsatisfied_templates: int = 0


def detect_combos(
    *,
    store: ComboStore,
    deck_card_names: list[str],
    deck_color_identity: list[str] | None = None,
    require_full_match: bool = True,
    require_color_subset: bool = True,
    limit: int | None = None,
) -> list[MatchedCombo]:
    """Return combos satisfied by the given deck.

    Parameters
    - deck_card_names: list of card names in the deck (any zone — typical
      callers pass mainboard + commander).
    - deck_color_identity: list like ["W", "U", "B"]. Used when
      require_color_subset=True to filter mismatched-color combos.
    - require_full_match: when True (default), ALL combo.cards must be in
      the deck. When False, partial matches are returned with the missing
      cards listed in MatchedCombo.in_deck_cards.
    - require_color_subset: when True (default), drop combos whose
      color_identity contains a color the deck doesn't have.
    - limit: cap the result count (None = unbounded).

    Algorithm
    - Iterate combo IDs reachable from any deck card via the
      combo_card_index. Dedupe. For each candidate, fetch the full combo
      and check the full-match / color rules.
    """
    if not deck_card_names:
        return []
    deck_set = {n for n in deck_card_names if n}
    deck_lower = {n.lower() for n in deck_set}

    deck_colors = set((deck_color_identity or []))

    # Fan out from each deck card to candidate combo IDs, dedup'd.
    candidate_ids: set[str] = set()
    for name in deck_set:
        for cid in store.lookup_combos_for_card(name):
            candidate_ids.add(cid)

    matched: list[MatchedCombo] = []
    for cid in candidate_ids:
        combo = store.get_combo(cid)
        if combo is None:
            continue
        # Color subset
        if require_color_subset and combo.color_identity and deck_colors:
            combo_colors = set(combo.color_identity)
            if not combo_colors.issubset(deck_colors):
                continue
        # Card match
        present = [c for c in combo.cards if c.lower() in deck_lower]
        if require_full_match and len(present) < len(combo.cards):
            continue
        matched.append(MatchedCombo(
            combo=combo,
            in_deck_cards=present,
            unsatisfied_templates=len(combo.templates),
        ))

    # Sort by popularity (descending) — most-played combos first.
    matched.sort(key=lambda m: -m.combo.popularity)
    if limit is not None:
        matched = matched[:limit]
    return matched
