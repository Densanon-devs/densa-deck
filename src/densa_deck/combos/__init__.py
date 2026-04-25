"""Combo detection — Commander Spellbook integration.

Densa Deck integrates the Commander Spellbook combo dataset
(https://commanderspellbook.com, MIT-licensed, © 2023 Commander-Spellbook,
data via https://backend.commanderspellbook.com/variants/) so deck analysis
can surface "your deck has these N combos" alongside the static numbers.

Architecture:
- `data.py` fetches and caches the combo dataset to ~/.densa-deck/combos.db
  (SQLite, indexed by card name). Refreshable on demand.
- `matcher.py` walks the cached combos and returns the ones whose required
  cards are all present in the deck (or within `tutor_distance` cards of it,
  for combo-pieces-tutored-rather-than-drawn).
- `models.py` defines a slim Combo dataclass that's easy to JSON-serialize
  for the desktop app's bridge.

Combo data is FAN CONTENT under Wizards of the Coast's Fan Content Policy.
Densa Deck's existing WotC disclaimer covers this; the About panel surfaces
attribution to Commander Spellbook on top of that.
"""

from densa_deck.combos.matcher import detect_combos, MatchedCombo
from densa_deck.combos.models import Combo
from densa_deck.combos.data import (
    ComboStore,
    DEFAULT_COMBO_DB_PATH,
    refresh_combo_snapshot,
)

__all__ = [
    "Combo",
    "ComboStore",
    "DEFAULT_COMBO_DB_PATH",
    "MatchedCombo",
    "detect_combos",
    "refresh_combo_snapshot",
]
