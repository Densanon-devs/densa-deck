"""Physical collection tracking — what cardboard you actually own.

Local-only by design: collection data lives in `~/.densa-deck/collection.db`
and never leaves the machine, same as playgroup and iteration data.

The layering that matters:

    cards            one row per unique card       (oracle_cards bulk)
    card_printings   one row per physical printing (default_cards bulk, opt-in)
    collection_items one row per stack you own     (this package)

Deck analysis only ever needed the first. Owning cardboard needs all three.
"""

from densa_deck.collection.models import (
    CONDITION_MULTIPLIERS,
    CollectionItem,
    CollectionSummary,
    Condition,
    Finish,
    OwnershipRow,
)
from densa_deck.collection.ownership import (
    committed_by_name,
    ownership_for_deck,
    ownership_rows,
)
from densa_deck.collection.prices import (
    PRICE_ATTRIBUTION,
    STALE_AFTER_HOURS,
    PriceProvider,
    ScryfallBulkProvider,
    capture_price_snapshot,
    is_stale,
    price_age_hours,
    price_history_for_printing,
    value_collection,
    value_deltas,
)
from densa_deck.collection.storage import CollectionStore

__all__ = [
    "CONDITION_MULTIPLIERS",
    "PRICE_ATTRIBUTION",
    "STALE_AFTER_HOURS",
    "CollectionItem",
    "CollectionStore",
    "CollectionSummary",
    "Condition",
    "Finish",
    "OwnershipRow",
    "PriceProvider",
    "ScryfallBulkProvider",
    "capture_price_snapshot",
    "committed_by_name",
    "is_stale",
    "ownership_for_deck",
    "ownership_rows",
    "price_age_hours",
    "price_history_for_printing",
    "value_collection",
    "value_deltas",
]
