"""Slim Combo model — what the matcher returns and what the desktop app
serializes over the bridge.

We deliberately keep this much smaller than the upstream
backend.commanderspellbook.com `/variants/{id}/` shape because the desktop
app only renders a few fields per combo: the cards involved, what it
produces (e.g. "Infinite colorless mana"), the description, and a link
back to Commander Spellbook for the full breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Combo:
    """One Commander Spellbook variant, projected down to what we render."""

    combo_id: str                      # upstream id, used as the spellbook URL slug
    cards: list[str] = field(default_factory=list)        # canonical card names (uses[].card.name)
    templates: list[str] = field(default_factory=list)    # named templates ("Permanent that can be cast using {C}")
    produces: list[str] = field(default_factory=list)     # feature names ("Infinite colorless mana")
    color_identity: str = ""            # e.g. "WUBG"
    bracket_tag: str = ""               # "C" / "M" / "U" / "E" — Commander Spellbook's combo tier
    description: str = ""               # human-readable steps
    popularity: int = 0                 # decklists running this combo on EDHREC, per spellbook
    legal_in_commander: bool = True
    spellbook_url: str = ""             # https://commanderspellbook.com/combo/<id>/
    mana_value_needed: float = 0.0
    notable_prerequisites: str = ""

    def short_label(self) -> str:
        """One-line label suitable for a Discord paste / Rule-0 worksheet."""
        cards_part = " + ".join(self.cards[:4])
        if len(self.cards) > 4:
            cards_part += f" + {len(self.cards) - 4} more"
        if self.produces:
            return f"{cards_part} → {self.produces[0]}"
        return cards_part
