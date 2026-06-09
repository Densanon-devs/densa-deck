"""Generate concrete change proposals for the iteration loop.

A `Proposal` is a single editable change the user can preview and accept:
either CUT a card already in the deck, or ADD a card from the candidate
pool that closes a role gap or completes a near-miss combo.

We re-use the existing analyst infrastructure rather than duplicating its
ranking logic — `rank_cut_candidates` for cuts, `find_add_candidates` plus
the combo near-miss store for adds. The proposal layer's job is to fold
those into a uniform shape so the preview/UI doesn't have to special-case.

This module is deliberately deterministic — no LLM call. The whole point
of the iteration loop is that the user explores many proposals quickly,
so latency per proposal matters more than narration quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from densa_deck.analyst.add_candidates import find_add_candidates
from densa_deck.analyst.candidates import rank_cut_candidates
from densa_deck.analyst.runner import _detect_role_gaps
from densa_deck.analysis.static import analyze_deck as run_static_analysis
from densa_deck.models import CardTag, Deck, Format


ProposalKind = Literal["cut", "add"]


@dataclass
class Proposal:
    """A single change the user can accept or reject.

    `card_name` is the card being cut (kind="cut") or added (kind="add").
    `source` names the layer that surfaced the proposal — "role-gap",
    "combo-completion", or "high-cmc-cut" — so the UI can group by intent.
    `signal` is a short machine-readable reason string ("redundant_ramp",
    "completes_combo", etc.) suitable for filtering.
    """

    kind: ProposalKind
    card_name: str
    reason: str
    source: str
    signal: str = ""
    score: float = 0.0
    role: str = ""  # role tag for adds; empty for cuts


def propose_changes(
    deck: Deck,
    db,
    *,
    format_: Format | None = None,
    protected_card_names: set[str] | None = None,
    combo_completers: set[str] | None = None,
    cut_limit: int = 8,
    add_limit: int = 8,
    budget_usd: float | None = None,
) -> list[Proposal]:
    """Surface up to `cut_limit` cut proposals and `add_limit` add proposals.

    Cuts and adds are derived from the existing analyst rankers. The result
    is the concatenation, sorted: combo-completion adds first (highest
    impact), then high-score cuts, then role-gap adds.

    `protected_card_names` shields combo pieces from being surfaced as cuts.
    `combo_completers` biases add candidates so near-miss combo finishers
    float to the top of their role bucket.
    """
    fmt = format_ or deck.format or Format.COMMANDER
    deck_color_identity = {
        c.value for e in deck.entries if e.card for c in e.card.color_identity
    }
    deck_names = {e.card.name for e in deck.entries if e.card}
    analysis = run_static_analysis(deck)

    proposals: list[Proposal] = []

    # ---- cuts ----
    cuts = rank_cut_candidates(
        deck, limit=cut_limit,
        protected_card_names={n.lower() for n in (protected_card_names or set())},
    )
    for c in cuts:
        card = c.entry.card
        if card is None:
            continue
        signal = "/".join(c.reasons) if c.reasons else "low_signal"
        # Build a human reason — top reason wins, fallback to summary.
        if "vanilla_bloat" in c.reasons:
            reason = f"{card.name} is high-cost with no functional tag — pure curve filler."
        elif any(r.startswith("redundant_") for r in c.reasons):
            reason = f"{card.name} is in an over-provisioned role for this format."
        elif "high_cmc_non_finisher" in c.reasons:
            reason = f"{card.name} sits high on the curve without closing games."
        elif "no_functional_tag" in c.reasons:
            reason = f"{card.name} doesn't slot into a core role for this archetype."
        else:
            reason = f"Cut signals: {signal}"
        proposals.append(Proposal(
            kind="cut",
            card_name=card.name,
            reason=reason,
            source="high-score-cut",
            signal=signal,
            score=c.score,
        ))

    # ---- adds: role-gap ----
    gaps = _detect_role_gaps(analysis)
    completers_lower = {n.lower() for n in (combo_completers or set())}
    seen_add: set[str] = set()
    per_role = max(1, add_limit // max(1, len(gaps) or 1))
    for role in gaps:
        cands = find_add_candidates(
            db=db, role=role, deck_color_identity=deck_color_identity,
            format_=fmt, exclude_names=deck_names | seen_add,
            limit=per_role, budget_usd=budget_usd,
            combo_completers=combo_completers,
        )
        for cand in cands:
            card = cand.card
            if card.name in seen_add:
                continue
            seen_add.add(card.name)
            completes = bool(getattr(cand, "completes_combo", False))
            reason = f"Closes the {role.value.replace('_', ' ')} gap"
            if completes:
                reason += " and completes a known combo line"
            proposals.append(Proposal(
                kind="add",
                card_name=card.name,
                reason=reason,
                source="role-gap-combo" if completes else "role-gap",
                signal=("completes_combo" if completes else f"role:{role.value}"),
                score=10.0 if completes else 5.0,
                role=role.value,
            ))

    # Sort: combo-completion adds first, then cuts by score, then role-gap adds.
    def _order(p: Proposal) -> tuple:
        if p.source == "role-gap-combo":
            return (0, -p.score, p.card_name)
        if p.kind == "cut":
            return (1, -p.score, p.card_name)
        return (2, -p.score, p.card_name)
    proposals.sort(key=_order)
    # Cap so the UI list isn't overwhelming.
    return proposals[: cut_limit + add_limit]
