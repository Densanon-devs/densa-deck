"""Commander bracket framework (1-precon ... 5-cedh).

WotC-aligned brackets that emerged 2024-2025 as the lingua franca for
casual-vs-competitive deck conversations. Densa Deck classifies a deck
into a bracket using its existing `PowerBreakdown` + a few structural
signals, and then surfaces concrete deltas to fit a target bracket.

The five-bracket convention (terms standardized in r/EDH 2024):
  1 — Precon out of the box
  2 — Upgraded precon, no infinite combos, no fast mana
  3 — Optimized casual, may include combos but expects 5+ turn games
  4 — High-power, fast mana + tutors + multiple win conditions
  5 — cEDH

This module is deterministic — no LLM call. It maps to the same labels
the Rule 0 worksheet uses and exposes a `bracket_fit(deck, target)`
that returns the prose-ready "fits / over-pitches / under-delivers"
verdict + a punch-list of concrete additions to close the gap.

Why a separate module:
- power_level produces a 1-10 score; brackets are buckets with their
  own structural rules (e.g. "bracket 2 forbids infinite combos" is a
  binary constraint, not a power score).
- Goldfish/Gauntlet aren't bracket-aware today; this module is the
  shared bracket vocabulary for everything downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from densa_deck.models import Deck, Zone


# Bracket label → human-readable name + power-score window
BRACKETS: list[tuple[str, str, float, float]] = [
    # (label, display_name, power_min_inclusive, power_max_inclusive)
    ("1-precon",      "Precon",        0.0, 3.0),
    ("2-upgraded",    "Upgraded",      3.0, 5.5),
    ("3-optimized",   "Optimized",     5.5, 7.5),
    ("4-high-power",  "High-power",    7.5, 9.0),
    ("5-cedh",        "cEDH",          9.0, 10.5),
]


@dataclass
class BracketFit:
    """Result of `bracket_fit(deck, target)` — both prose and structured."""

    detected_label: str           # what bracket this deck IS
    detected_name: str
    target_label: str             # what bracket the user is aiming for
    target_name: str
    verdict: str                  # "fits" | "over-pitches" | "under-delivers"
    headline: str                 # one-sentence summary the UI surfaces
    delta: int                    # bracket steps from detected → target (signed)
    # Structured signals — exact reasons the verdict went the way it did.
    over_signals: list[str] = field(default_factory=list)   # why we'd over-pitch (combos / fast mana / tutors)
    under_signals: list[str] = field(default_factory=list)  # why we'd under-deliver (no interaction / weak finishers)
    # Punch list — concrete recommendations to move toward target.
    recommendations: list[str] = field(default_factory=list)


# Bracket constraints (what's expected / forbidden at each bracket).
# These are heuristics distilled from WotC's bracket talking points and
# r/EDH community consensus; they're calibrated against the existing
# Densa Deck tag taxonomy (CardTag.RAMP / TUTOR / FINISHER / etc.).
_BRACKET_RULES: dict[str, dict] = {
    "1-precon": {
        "max_combos": 0,
        "max_tutors": 1,
        "fast_mana_max": 0,    # no Mana Crypt / Mana Vault / Sol Ring*
        "expected_interaction_min": 4,
        "expected_ramp_min": 8,
    },
    "2-upgraded": {
        "max_combos": 0,
        "max_tutors": 3,
        "fast_mana_max": 1,    # Sol Ring is OK in bracket 2; Crypt/Vault aren't
        "expected_interaction_min": 6,
        "expected_ramp_min": 9,
    },
    "3-optimized": {
        "max_combos": 3,       # combos OK if game-ending plan is around turn 6-8
        "max_tutors": 6,
        "fast_mana_max": 3,
        "expected_interaction_min": 8,
        "expected_ramp_min": 10,
    },
    "4-high-power": {
        "max_combos": 999,     # no combo cap
        "max_tutors": 999,
        "fast_mana_max": 999,
        "expected_interaction_min": 10,
        "expected_ramp_min": 12,
    },
    "5-cedh": {
        "max_combos": 999,
        "max_tutors": 999,
        "fast_mana_max": 999,
        "expected_interaction_min": 12,
        "expected_ramp_min": 12,
    },
}


# Cards that count as "fast mana" for bracket gating. We deliberately
# undercount here — being too aggressive would catch every Sol Ring and
# wrongly bump every casual deck out of bracket 1. Sol Ring is excluded
# from the strict list because it's effectively in every Commander deck.
_FAST_MANA_CARDS = frozenset({
    "Mana Crypt",
    "Mana Vault",
    "Mox Diamond",
    "Mox Opal",
    "Chrome Mox",
    "Lotus Petal",
    "Jeweled Lotus",
    "Grim Monolith",
    "Ancient Tomb",
    "Lion's Eye Diamond",
})


def _bracket_for_power(overall: float) -> tuple[str, str]:
    """Map a 1-10 power score to (label, name).

    Same buckets the Rule 0 worksheet uses, but returned with the
    display name too. Mirrors `analyst.phase6._bracket_for_power` —
    we duplicate rather than import to keep brackets independent of
    the LLM/analyst module and avoid a circular import.
    """
    for label, name, lo, hi in BRACKETS:
        if lo <= overall < hi:
            return label, name
    # Above the topmost upper bound = cEDH; below the bottom = precon.
    return BRACKETS[-1][0], BRACKETS[-1][1]


def _bracket_index(label: str) -> int:
    """0-based index of a bracket label, used for delta math."""
    for i, b in enumerate(BRACKETS):
        if b[0] == label:
            return i
    return 2  # default to optimized — the median-deck bucket


def detect_deck_brackets(deck: Deck, power_overall: float) -> tuple[str, str, dict]:
    """Classify a deck into a bracket. Returns (label, name, signals).

    Signals dict carries the structural counts the bracket-fit logic
    uses to write its punch list:
      tutor_count — `tutor` tag entries
      fast_mana_count — cards in `_FAST_MANA_CARDS`
      combo_pieces_count — known infinite-combo enablers (rough proxy)
      interaction_count — passed in via PowerBreakdown's analysis
    Power score remains the primary classifier; signals adjust the
    classification when constraints are violated (e.g., a power-5
    deck running 3 fast-mana pieces gets bumped up).
    """
    label, name = _bracket_for_power(power_overall)
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]

    tutor_count = 0
    fast_mana_count = 0
    for e in active:
        if e.card is None:
            continue
        for t in (e.card.tags or []):
            if t.value == "tutor":
                tutor_count += e.quantity
                break
        if e.card.name in _FAST_MANA_CARDS:
            fast_mana_count += e.quantity

    signals = {
        "tutor_count": tutor_count,
        "fast_mana_count": fast_mana_count,
    }

    # Power-bucket label is the baseline; bump UP one bracket if structural
    # signals exceed bracket-1/2/3's caps. We don't bump DOWN — a slow deck
    # is still slow even if it has Mana Crypt; the power score already
    # captures speed correctly.
    rules = _BRACKET_RULES.get(label, {})
    if (
        fast_mana_count > rules.get("fast_mana_max", 999)
        or tutor_count > rules.get("max_tutors", 999)
    ):
        idx = _bracket_index(label)
        if idx < len(BRACKETS) - 1:
            label = BRACKETS[idx + 1][0]
            name = BRACKETS[idx + 1][1]

    return label, name, signals


def bracket_fit(
    deck: Deck,
    target_label: str,
    *,
    power_overall: float,
    interaction_count: int,
    ramp_count: int,
    detected_combo_count: int = 0,
    combo_lines: list[str] | None = None,
) -> BracketFit:
    """Assess how a deck fits a target bracket and recommend deltas.

    `target_label` is one of the BRACKETS labels (e.g. "3-optimized").
    `detected_combo_count` is the count of full combo lines surfaced
    by `densa_deck.combos.detect_combos` — passed in rather than
    detected here so this module stays independent of the combo store.
    `combo_lines` (optional): list of human-readable combo line labels
    so over-pitched bracket recommendations can name SPECIFIC lines
    to drop (e.g. "drop the Thoracle + Consultation line"), not just
    "you have too many combos."
    """
    detected_label, detected_name, signals = detect_deck_brackets(deck, power_overall)
    target_name = next((b[1] for b in BRACKETS if b[0] == target_label), target_label)

    delta = _bracket_index(target_label) - _bracket_index(detected_label)

    over_signals: list[str] = []
    under_signals: list[str] = []
    recommendations: list[str] = []

    target_rules = _BRACKET_RULES.get(target_label, {})
    fast_mana_max = target_rules.get("fast_mana_max", 999)
    tutor_max = target_rules.get("max_tutors", 999)
    combo_max = target_rules.get("max_combos", 999)
    interaction_min = target_rules.get("expected_interaction_min", 0)
    ramp_min = target_rules.get("expected_ramp_min", 0)

    # OVER signals — we have things the target bracket disallows.
    if signals["fast_mana_count"] > fast_mana_max:
        over = signals["fast_mana_count"] - fast_mana_max
        over_signals.append(
            f"{signals['fast_mana_count']} fast-mana piece(s) — bracket {target_label} caps at {fast_mana_max}",
        )
        recommendations.append(
            f"Cut {over} fast-mana piece(s) (Mana Crypt / Mana Vault / Mox*) to fit bracket {target_label}.",
        )
    if signals["tutor_count"] > tutor_max:
        over = signals["tutor_count"] - tutor_max
        over_signals.append(
            f"{signals['tutor_count']} tutor(s) — bracket {target_label} caps at {tutor_max}",
        )
        recommendations.append(
            f"Cut {over} tutor(s) so the deck reads as bracket {target_label} rather than over-pitched.",
        )
    if detected_combo_count > combo_max:
        over_signals.append(
            f"{detected_combo_count} combo line(s) detected — bracket {target_label} caps at {combo_max}",
        )
        # Name specific combo lines to drop when we have them, otherwise
        # fall back to the generic recommendation.
        if combo_lines:
            excess = detected_combo_count - combo_max
            named = combo_lines[:excess + 1] if excess >= 0 else combo_lines[:2]
            for line in named[:3]:
                recommendations.append(
                    f"Drop the combo line '{line}' to fit bracket {target_label}."
                )
            if not named:
                recommendations.append(
                    f"Disclose or cut combo lines — bracket {target_label} expects ≤ {combo_max}.",
                )
        else:
            recommendations.append(
                f"Disclose or cut combo lines — bracket {target_label} expects ≤ {combo_max}.",
            )

    # UNDER signals — we're missing the floor.
    if interaction_count < interaction_min:
        under_signals.append(
            f"only {interaction_count} interaction pieces — bracket {target_label} floor is {interaction_min}",
        )
        recommendations.append(
            f"Add {interaction_min - interaction_count} interaction piece(s) (removal / counters / wipes).",
        )
    if ramp_count < ramp_min:
        under_signals.append(
            f"only {ramp_count} ramp pieces — bracket {target_label} floor is {ramp_min}",
        )
        recommendations.append(
            f"Add {ramp_min - ramp_count} ramp piece(s) so the curve actually casts.",
        )

    # Verdict + headline
    if delta == 0 and not over_signals and not under_signals:
        verdict = "fits"
        headline = f"This deck reads as bracket {detected_label}, which is what you're targeting — looks good."
    elif over_signals:
        verdict = "over-pitches"
        headline = f"This deck reads ABOVE bracket {target_label} — the table will feel out-paced unless you trim."
    elif under_signals:
        verdict = "under-delivers"
        headline = f"This deck reads BELOW bracket {target_label} — the table will out-pace it unless you add speed/interaction."
    else:
        verdict = "fits"
        headline = f"This deck reads as bracket {detected_label}; target was {target_label}."

    return BracketFit(
        detected_label=detected_label,
        detected_name=detected_name,
        target_label=target_label,
        target_name=target_name,
        verdict=verdict,
        headline=headline,
        delta=delta,
        over_signals=over_signals,
        under_signals=under_signals,
        recommendations=recommendations,
    )
