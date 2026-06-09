"""Iteration loop — close the gap between analysis and deckbuilding action.

Three stages, each independently useful:

  1. propose_changes(deck, db, ctx)
        Surfaces concrete change proposals (cut N, add M) from the existing
        analyst infrastructure. Each Proposal carries the WHY (signal +
        source layer) so the user can rank against their own taste.

  2. preview_change(deck, proposal, db)
        Apply the proposal in-memory, re-run static + combos + power, and
        return a ChangePreview with before/after metrics + deltas. Fast
        enough to chain ("preview each top-5 proposal" runs in <1s on
        warm DB) — no goldfish, no LLM call.

  3. record_iteration(...) / iteration_history(...)
        Persistent log of accepted/rejected changes so the user can see
        "you cut 8 cards over the last week, power went 7.2 → 6.8."
"""

from densa_deck.iteration.proposals import Proposal, propose_changes
from densa_deck.iteration.preview import (
    ChangePreview,
    apply_proposal,
    preview_change,
)
from densa_deck.iteration.storage import IterationStore, IterationRecord

__all__ = [
    "Proposal",
    "propose_changes",
    "ChangePreview",
    "apply_proposal",
    "preview_change",
    "IterationStore",
    "IterationRecord",
]
