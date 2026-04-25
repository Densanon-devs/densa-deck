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


@dataclass
class NearMissCombo:
    """Combo where the deck has all-but-N concrete cards.

    Surfaces the "you're 1 card away from Thoracle + Demonic Consultation"
    insight that turns combo detection from a passive read into an
    active deckbuilding tool. The `missing_cards` list is exactly what
    the user would need to add to complete the combo line.
    """
    combo: Combo
    in_deck_cards: list[str] = field(default_factory=list)
    missing_cards: list[str] = field(default_factory=list)
    missing_count: int = 0
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


def detect_near_miss_combos(
    *,
    store: ComboStore,
    deck_card_names: list[str],
    deck_color_identity: list[str] | None = None,
    max_missing: int = 1,
    require_color_subset: bool = True,
    limit: int | None = 25,
) -> list[NearMissCombo]:
    """Return combos the deck is `max_missing` or fewer cards away from
    completing — the "you're 1 card from Thoracle + Demonic Consultation"
    surface.

    Algorithm
    - Same combo-id fan-out as `detect_combos`, but instead of requiring
      ALL cards present we only require `len(combo.cards) - max_missing`
      to be in the deck.
    - Skips combos with > 6 cards total (those near-misses are noise).
    - Color-subset filter still applies — won't suggest you complete a
      Mardu combo in a Bant deck.
    - Sorted by (missing_count ASC, popularity DESC) so the most-popular
      single-card-away combos float to the top.
    """
    if not deck_card_names:
        return []
    if max_missing < 1:
        max_missing = 1

    deck_set = {n for n in deck_card_names if n}
    deck_lower = {n.lower() for n in deck_set}
    deck_colors = set((deck_color_identity or []))

    # Fan out from each deck card to candidate combo IDs.
    candidate_ids: set[str] = set()
    for name in deck_set:
        for cid in store.lookup_combos_for_card(name):
            candidate_ids.add(cid)

    near: list[NearMissCombo] = []
    for cid in candidate_ids:
        combo = store.get_combo(cid)
        if combo is None:
            continue
        if len(combo.cards) > 6:
            continue
        if require_color_subset and combo.color_identity and deck_colors:
            combo_colors = set(combo.color_identity)
            if not combo_colors.issubset(deck_colors):
                continue
        present = [c for c in combo.cards if c.lower() in deck_lower]
        missing = [c for c in combo.cards if c.lower() not in deck_lower]
        # Skip "0 missing" (those are fully-detected combos, not near misses)
        # and "all missing" (deck shares zero cards with the combo).
        if not (1 <= len(missing) <= max_missing):
            continue
        near.append(NearMissCombo(
            combo=combo,
            in_deck_cards=present,
            missing_cards=missing,
            missing_count=len(missing),
            unsatisfied_templates=len(combo.templates),
        ))

    near.sort(key=lambda n: (n.missing_count, -n.combo.popularity))
    if limit is not None:
        near = near[:limit]
    return near
