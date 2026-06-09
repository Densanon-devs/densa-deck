"""Deck pricing — total value, per-card breakdown, TCGPlayer search links.

Pricing data comes from Scryfall's `prices.usd` field, ingested into the
`price_usd` column of the cards table. Cards without a known price are
counted separately and never silently treated as $0 — the total deck value
is always "value of cards we have a price for, with N cards unpriced."

Surfaced from:
- CLI `densa-deck analyze` — pricing summary row + `--budget <usd>` flag
- App API `get_deck_value` — for the Build tab's running total
- App API `suggest_deckbuild_additions(budget_usd=...)` — already plumbed
  through to `find_add_candidates`; the UI now passes the user's cap
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import quote_plus

from densa_deck.models import Deck, Zone


TCGPLAYER_SEARCH_BASE = "https://www.tcgplayer.com/search/magic/product"

# Optional partner / affiliate parameter, sourced from env so the binary
# bundle ships with no hardcoded ID. Set DENSA_TCGPLAYER_PARTNER=<id> to
# inject "&partner=<id>" into generated URLs.
_PARTNER_ENV_VAR = "DENSA_TCGPLAYER_PARTNER"


@dataclass
class CardPriceLine:
    """One deck entry priced out."""

    name: str
    quantity: int
    unit_price_usd: float | None  # None when Scryfall has no price for this card

    @property
    def line_total(self) -> float | None:
        if self.unit_price_usd is None:
            return None
        return round(self.unit_price_usd * self.quantity, 2)


@dataclass
class DeckValue:
    """Pricing summary for a deck."""

    total_known_usd: float = 0.0
    unpriced_count: int = 0  # number of card copies with no Scryfall price
    priciest: list[CardPriceLine] = field(default_factory=list)
    zones_included: list[str] = field(default_factory=list)
    lines: list[CardPriceLine] = field(default_factory=list)
    over_budget: list[CardPriceLine] = field(default_factory=list)


_DEFAULT_INCLUDE = (Zone.COMMANDER, Zone.MAINBOARD)


def compute_deck_value(
    deck: Deck,
    *,
    include_zones: Iterable[Zone] = _DEFAULT_INCLUDE,
    top_n: int = 5,
    budget_per_card_usd: float | None = None,
) -> DeckValue:
    """Roll up per-card prices into a deck-level summary.

    Args:
      include_zones: which zones contribute to the total. Defaults to
        commander + mainboard — sideboard and maybeboard are excluded so the
        headline number reflects what's actually on the table.
      top_n: how many of the most expensive cards to surface for the UI.
      budget_per_card_usd: if set, any card whose unit price exceeds this
        is collected into `over_budget` so the Build tab can flag the
        offenders without a second pass.
    """
    include = set(include_zones)
    lines: list[CardPriceLine] = []
    total_known = 0.0
    unpriced_qty = 0
    over: list[CardPriceLine] = []

    for entry in deck.entries:
        if entry.zone not in include:
            continue
        card = entry.card
        name = card.name if card is not None else entry.card_name
        price = card.price_usd if card is not None else None
        line = CardPriceLine(name=name, quantity=entry.quantity, unit_price_usd=price)
        lines.append(line)
        if price is None:
            unpriced_qty += entry.quantity
            continue
        total_known += price * entry.quantity
        if budget_per_card_usd is not None and price > budget_per_card_usd:
            over.append(line)

    priciest = sorted(
        (l for l in lines if l.line_total is not None),
        key=lambda l: l.line_total or 0.0,
        reverse=True,
    )[:top_n]
    # Stable, deterministic ordering for over-budget callouts.
    over.sort(key=lambda l: (-(l.unit_price_usd or 0.0), l.name))

    return DeckValue(
        total_known_usd=round(total_known, 2),
        unpriced_count=unpriced_qty,
        priciest=priciest,
        zones_included=[z.value for z in include],
        lines=lines,
        over_budget=over,
    )


def tcgplayer_search_url(card_name: str) -> str:
    """Build a TCGPlayer Magic-search URL for a card.

    We use search (not a direct product URL) because Scryfall's bulk data
    doesn't include TCGPlayer product IDs, and a search by name is robust
    to set/printing choice — TCG handles the name match and surfaces all
    printings, which is usually what the buyer wants.

    Set the `DENSA_TCGPLAYER_PARTNER` env var to inject a partner ID; with
    no env var, the URL still works, just without the affiliate tag.
    """
    if not card_name:
        return TCGPLAYER_SEARCH_BASE
    q = quote_plus(card_name)
    url = f"{TCGPLAYER_SEARCH_BASE}?q={q}&view=grid&productLineName=magic"
    partner = os.environ.get(_PARTNER_ENV_VAR, "").strip()
    if partner:
        url += f"&partner={quote_plus(partner)}"
    return url


def value_to_dict(value: DeckValue) -> dict:
    """Serialize for the app API and JSON exports."""
    return {
        "total_known_usd": value.total_known_usd,
        "unpriced_count": value.unpriced_count,
        "zones_included": list(value.zones_included),
        "priciest": [
            {
                "name": l.name,
                "quantity": l.quantity,
                "unit_price_usd": l.unit_price_usd,
                "line_total": l.line_total,
                "tcgplayer_url": tcgplayer_search_url(l.name),
            }
            for l in value.priciest
        ],
        "over_budget": [
            {
                "name": l.name,
                "quantity": l.quantity,
                "unit_price_usd": l.unit_price_usd,
                "tcgplayer_url": tcgplayer_search_url(l.name),
            }
            for l in value.over_budget
        ],
    }
