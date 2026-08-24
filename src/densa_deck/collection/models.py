"""Types for the physical collection.

The distinction that drives the whole design:

    Card             "Skithiryx, the Blight Dragon"      -> cards table
    Printing         Scars of Mirrodin #79               -> card_printings table
    CollectionItem   1 NM nonfoil copy of that printing  -> collection_items

Quantity is a property of a *stack of identical physical copies*, never of a
card. Two copies of Sol Ring can be a $1.50 Commander Masters reprint and a
$22 Secret Lair foil; collapsing them onto the card would make every valuation
and every trade decision wrong.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field


class Condition(str, enum.Enum):
    """Standard TCG condition grades, best to worst."""

    NM = "NM"  # Near Mint
    LP = "LP"  # Lightly Played
    MP = "MP"  # Moderately Played
    HP = "HP"  # Heavily Played
    DMG = "DMG"  # Damaged


class Finish(str, enum.Enum):
    NONFOIL = "nonfoil"
    FOIL = "foil"
    ETCHED = "etched"


# Rough multipliers applied to a printing's market price to estimate what a
# played copy is worth. Deliberately conservative and deliberately visible:
# real condition pricing varies by card, era and vendor, so these are an
# estimate the user can see and reason about, not a claim of market truth.
CONDITION_MULTIPLIERS: dict[Condition, float] = {
    Condition.NM: 1.00,
    Condition.LP: 0.85,
    Condition.MP: 0.70,
    Condition.HP: 0.50,
    Condition.DMG: 0.30,
}


class CollectionItem(BaseModel):
    """A stack of identical physical copies.

    Identity is (printing, finish, condition, language, location, collection)
    — the same card in a different box is a different stack, because that is
    how people actually store and find cardboard, and the same card can also
    sit in two named collections at once.

    `oracle_id` and `card_name` are denormalized from the printing on purpose.
    The collection lives in its own database and must stay readable if the
    card database is deleted, rebuilt, or never downloaded at all. Cross-file
    SQLite has no foreign keys to lean on anyway.
    """

    item_id: int = 0
    printing_id: str
    oracle_id: str = ""
    card_name: str
    finish: Finish = Finish.NONFOIL
    condition: Condition = Condition.NM
    language: str = "en"
    quantity: int = 0
    location: str = ""
    # Which named collection this stack belongs to. Ownership is independent
    # of it: the master collection is every stack, whatever its collection.
    collection_id: int = 0
    notes: str = ""
    acquired_at: str | None = None
    unit_cost_usd: float | None = None
    acquisition_id: int | None = None
    created_at: str = ""
    updated_at: str = ""

    # Populated by the store when joined against card_printings. Absent when
    # printings haven't been downloaded — the stack still counts and still
    # displays, it just can't show its set or its price.
    set_code: str = ""
    set_name: str = ""
    collector_number: str = ""
    rarity: str = ""
    unit_price_usd: float | None = None

    @property
    def condition_adjusted_price(self) -> float | None:
        """Estimated unit value after the condition multiplier.

        None (not 0.0) when the price is unknown — an unpriced card is not a
        free card, and letting it total as zero would understate a collection
        silently. 8.6% of paper printings carry no price at all.
        """
        if self.unit_price_usd is None:
            return None
        return round(self.unit_price_usd * CONDITION_MULTIPLIERS[self.condition], 2)

    @property
    def stack_value_usd(self) -> float | None:
        unit = self.condition_adjusted_price
        if unit is None:
            return None
        return round(unit * self.quantity, 2)


class CollectionSummary(BaseModel):
    """Roll-up for the collection header."""

    total_cards: int = 0
    unique_cards: int = 0
    unique_printings: int = 0
    total_value_usd: float = 0.0
    unpriced_items: int = 0
    unpriced_cards: int = 0
    by_finish: dict[str, int] = Field(default_factory=dict)
    by_condition: dict[str, int] = Field(default_factory=dict)
    prices_synced_at: str = ""
    prices_stale: bool = True


class OwnershipRow(BaseModel):
    """How many copies of one oracle card exist, and how many are spoken for.

    Allocation defaults to the oracle level: a deck slot says "Sol Ring", not
    "this specific Sol Ring", so `committed` counts every saved deck that
    calls for the card. `available` is what you could sleeve into something
    new tonight without unsleeving anything.
    """

    oracle_id: str = ""
    card_name: str
    owned: int = 0
    committed: int = 0

    @property
    def available(self) -> int:
        # Clamped at zero: over-commitment is a real state (three decks each
        # wanting the same Sol Ring) and it is reported as a shortfall, not
        # as negative availability.
        return max(0, self.owned - self.committed)

    @property
    def shortfall(self) -> int:
        return max(0, self.committed - self.owned)
