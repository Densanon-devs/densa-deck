"""Apply a proposal and re-score the deck so the user sees deltas before
committing.

Deliberately fast: re-runs static analysis + power_level + (optional)
combo detection only. No goldfish, no LLM. Order-of-magnitude: 5-50ms
per proposal on a warm card DB. That budget lets the UI preview a row
of 8 proposals concurrently.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from densa_deck.analysis.power_level import estimate_power_level
from densa_deck.analysis.pricing import compute_deck_value
from densa_deck.analysis.static import analyze_deck as run_static_analysis
from densa_deck.iteration.proposals import Proposal
from densa_deck.models import Deck, DeckEntry, Zone


@dataclass
class ChangePreview:
    """Before/after snapshot for a single proposal."""

    proposal: Proposal
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    deltas: dict = field(default_factory=dict)
    new_deck_text: str = ""  # the resulting decklist as plain text
    error: str = ""           # populated when the change can't be applied


def apply_proposal(deck: Deck, proposal: Proposal, db=None) -> Deck:
    """Return a new Deck with `proposal` applied. Doesn't mutate the input.

    For "cut" proposals we drop the entry if quantity is 1, else decrement.
    For "add" proposals we look the card up in `db` and append a 1x entry to
    mainboard. If the card isn't in the DB the entry is created with no
    resolved Card — static analysis still sees the slot count, but the card
    contributes nothing to power/tag metrics. We surface that as a warning
    in the ChangePreview, not a hard error, so the UI can still show the
    structural impact.
    """
    new = copy.deepcopy(deck)
    target = proposal.card_name.strip().lower()
    if proposal.kind == "cut":
        for i, entry in enumerate(new.entries):
            name = (entry.card.name if entry.card else entry.card_name).lower()
            if name == target:
                if entry.quantity > 1:
                    entry.quantity -= 1
                else:
                    new.entries.pop(i)
                return new
        # Cut against a card that isn't in the deck — return the deck unchanged.
        # The preview layer flags this as an error.
        return new
    if proposal.kind == "add":
        # Decrement against an exact match first so re-adding bumps qty,
        # not duplicate the entry. (Important for Basic Land adds.)
        for entry in new.entries:
            name = (entry.card.name if entry.card else entry.card_name).lower()
            if name == target:
                entry.quantity += 1
                return new
        card = db.lookup_by_name(proposal.card_name) if db is not None else None
        new.entries.append(DeckEntry(
            card_name=proposal.card_name,
            quantity=1,
            zone=Zone.MAINBOARD,
            card=card,
        ))
        return new
    return new


def preview_change(deck: Deck, proposal: Proposal, db=None) -> ChangePreview:
    """Compute before/after metrics for applying `proposal` to `deck`.

    `db` is optional. When provided, add proposals resolve against the card
    DB so power/tag metrics reflect the new card; without it the slot is
    counted but the card contributes nothing to tag-driven scores. Cuts
    don't need the DB at all (the cut card is already in `deck.entries`).
    """
    proposal_card_exists_in_deck = any(
        ((e.card.name if e.card else e.card_name).lower() == proposal.card_name.strip().lower())
        for e in deck.entries
    )

    if proposal.kind == "cut" and not proposal_card_exists_in_deck:
        return ChangePreview(
            proposal=proposal,
            error=f"Cannot cut '{proposal.card_name}' — not in the deck.",
        )

    new = apply_proposal(deck, proposal, db=db)
    if proposal.kind == "add" and db is not None:
        added = next(
            (e for e in new.entries if (e.card.name if e.card else e.card_name).lower() == proposal.card_name.lower()),
            None,
        )
        if added and added.card is None:
            # The card wasn't in the DB. We still compute structural metrics
            # (slot count, total_cards) but warn the user via `error`. UI can
            # render this as a soft warning, not a blocking failure.
            err_suffix = " (card not in DB — power/tag deltas may be inaccurate)"
        else:
            err_suffix = ""
    else:
        err_suffix = ""

    before = _metrics(deck)
    after = _metrics(new)
    deltas = _delta(before, after)
    return ChangePreview(
        proposal=proposal,
        before=before,
        after=after,
        deltas=deltas,
        new_deck_text=_deck_to_text(new),
        error=err_suffix.strip() if err_suffix else "",
    )


def _metrics(deck: Deck) -> dict:
    """The compact metrics dict the iteration layer diffs against."""
    analysis = run_static_analysis(deck)
    power = estimate_power_level(deck)
    value = compute_deck_value(deck)
    return {
        "power_overall": float(power.overall),
        "power_tier": power.tier,
        "interaction_count": int(analysis.interaction_count),
        "ramp_count": int(analysis.ramp_count),
        "draw_count": int(analysis.draw_engine_count),
        "land_count": int(analysis.land_count),
        "average_cmc": float(analysis.average_cmc),
        "total_cards": int(analysis.total_cards),
        "total_value_usd": float(value.total_known_usd),
        "unpriced_count": int(value.unpriced_count),
    }


def _delta(before: dict, after: dict) -> dict:
    out: dict = {}
    for key in before:
        if isinstance(before[key], (int, float)) and isinstance(after.get(key), (int, float)):
            diff = round(after[key] - before[key], 2)
            out[key] = diff
        else:
            out[key] = None
    return out


def _deck_to_text(deck: Deck) -> str:
    """Render the post-change deck as plain text the user can paste/save.

    Mirrors the standard "Commander / Mainboard / Sideboard" format the
    project's parser accepts on the input side.
    """
    sections: dict[str, list[str]] = {}
    for e in deck.entries:
        zone_name = e.zone.value if hasattr(e.zone, "value") else str(e.zone)
        zone_label = {
            "commander": "Commander",
            "mainboard": "Mainboard",
            "sideboard": "Sideboard",
            "maybeboard": "Maybeboard",
            "companion": "Companion",
        }.get(zone_name, zone_name.title())
        sections.setdefault(zone_label, []).append(
            f"{e.quantity} {e.card.name if e.card else e.card_name}"
        )
    lines: list[str] = []
    order = ["Commander", "Mainboard", "Sideboard", "Companion", "Maybeboard"]
    seen = set()
    for label in order:
        if label in sections:
            lines.append(label)
            lines.extend(sections[label])
            lines.append("")
            seen.add(label)
    # Any non-canonical zones get tacked on at the end.
    for label, rows in sections.items():
        if label in seen:
            continue
        lines.append(label)
        lines.extend(rows)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
