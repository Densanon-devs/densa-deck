"""Per-card castability analysis: can I cast this card on curve?

For each card with colored pip requirements, calculates the turn-by-turn
probability of having the right colors available. Flags demanding cards
that are unreliable given the current mana base.

**Two answers exist to this question and they are not the same.** This
module computes a *hypergeometric estimate*: fast, needs no simulation, and
assumes each colour's sources are drawn independently of the others. The
goldfish simulator computes a *measured* rate from real games, which
accounts for mulligans, ramp spells that fetch lands, lands entering
tapped, and the actual casting sequence — see `goldfish.reliability`.

The estimate is what `analyze` can afford; the measurement is what
`goldfish` produces. Where a caller has both, pass the measured rates into
`analyze_castability(measured_rates=...)` and they win. Every
`CardCastability` carries a `source` field saying which one it is, so the
two numbers can never be presented as if they were interchangeable.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from densa_deck.models import Deck, DeckEntry, Zone
from densa_deck.probability.hypergeometric import cards_seen_by_turn, prob_at_least

_PIP_PATTERN = re.compile(r"\{([WUBRG])\}")
_COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}

# Deliberately narrow: castability asks "how often will this card be stuck in hand?"
# Hybrid {W/U}, Phyrexian {W/P}, and twobrid {2/W} pips are all cast-any-side flexible —
# counting them as strict demand would false-positive hybrid cards like Kitchen Finks as
# unreliable. Strict castability = strict pip requirements only. For deck-wide pip
# density (which wants a picture of total color pressure), see advanced.analyze_pips.


@dataclass
class CardCastability:
    """Castability analysis for a single card."""

    name: str
    mana_cost: str = ""
    cmc: int = 0
    pip_requirements: dict[str, int] = field(default_factory=dict)  # color -> count
    on_curve_probability: float = 0.0  # Chance of casting on the turn equal to CMC
    castable_by_turn: dict[int, float] = field(default_factory=dict)
    bottleneck_color: str = ""  # Which color is hardest to produce
    reliable: bool = True  # >75% on curve
    # "estimated" (hypergeometric) or "measured" (from simulated games).
    # Never let these two be read as the same number.
    source: str = "estimated"


@dataclass
class CastabilityReport:
    """Castability analysis for all demanding cards in a deck."""

    cards: list[CardCastability] = field(default_factory=list)
    unreliable_cards: list[CardCastability] = field(default_factory=list)
    color_bottlenecks: dict[str, int] = field(default_factory=dict)  # color -> count of unreliable cards
    # "estimated" when every number came from the hypergeometric model,
    # "measured" when they all came from simulation, "mixed" otherwise.
    source: str = "estimated"


def analyze_castability(
    deck: Deck,
    color_sources: dict[str, int] | None = None,
    max_turn: int = 10,
    on_play: bool = True,
    reliability_threshold: float = 0.75,
    measured_rates: dict[str, float] | None = None,
) -> CastabilityReport:
    """Analyze castability for all cards with colored pip requirements.

    `measured_rates` (optional): card name -> observed on-curve castability
    from a goldfish batch (`GoldfishReport.mana_reliability.card_on_curve`).
    Any card present there uses the measured number instead of the
    hypergeometric estimate, and is marked `source="measured"`. Simulation
    beats approximation whenever we have paid for it.
    """
    report = CastabilityReport()
    active = [e for e in deck.entries if e.zone not in (Zone.MAYBEBOARD, Zone.SIDEBOARD) and e.card]
    deck_size = sum(e.quantity for e in active)

    if deck_size == 0 or not color_sources:
        return report

    # Deduplicate: analyze each unique card once
    seen: set[str] = set()

    for entry in active:
        card = entry.card
        if card is None or card.is_land or card.name in seen:
            continue
        seen.add(card.name)

        mana_cost = card.mana_cost
        if not mana_cost and card.faces:
            mana_cost = card.faces[0].mana_cost

        pips = _PIP_PATTERN.findall(mana_cost)
        if not pips:
            continue  # Colorless or no cost

        pip_counts = Counter(pips)
        measured = (measured_rates or {}).get(card.name)

        # Only *estimate* demanding costs (2+ pips of a colour, or 2+
        # colours) — estimating a single pip is noise. A measurement is
        # never noise, though: a lone {U} that simulation says is castable
        # 0% of the time is the most severe mana problem a deck can have,
        # and applying the estimate's filter to it would hide exactly that.
        if measured is None:
            max_pip = max(pip_counts.values()) if pip_counts else 0
            if max_pip < 2 and len(pip_counts) < 2:
                continue  # Single pip of one color is trivial to estimate

        cc = CardCastability(
            name=card.name,
            mana_cost=mana_cost,
            cmc=max(1, int(card.cmc)),
            pip_requirements=dict(pip_counts),
        )

        # Calculate per-turn castability
        # The bottleneck is the hardest color to assemble
        for turn in range(1, max_turn + 1):
            n = cards_seen_by_turn(turn, on_play)
            n = min(n, deck_size)

            # Probability of having all required colors
            turn_prob = 1.0
            for color, needed in pip_counts.items():
                sources = color_sources.get(color, 0)
                if sources == 0:
                    turn_prob = 0.0
                    break
                p = prob_at_least(needed, deck_size, sources, n)
                turn_prob *= p  # Independence approximation

            cc.castable_by_turn[turn] = round(turn_prob, 4)

        # On-curve probability. A measured rate from simulation supersedes
        # the estimate — it already accounts for mulligans, ramp and tapped
        # lands, none of which the hypergeometric model can see.
        on_curve_turn = cc.cmc
        if measured is not None:
            cc.on_curve_probability = measured
            cc.castable_by_turn[on_curve_turn] = measured
            cc.source = "measured"
        else:
            cc.on_curve_probability = cc.castable_by_turn.get(on_curve_turn, 0.0)

        # Find bottleneck color
        worst_ratio = float("inf")
        for color, needed in pip_counts.items():
            sources = color_sources.get(color, 0)
            ratio = sources / max(1, needed)
            if ratio < worst_ratio:
                worst_ratio = ratio
                cc.bottleneck_color = color

        cc.reliable = cc.on_curve_probability >= reliability_threshold

        report.cards.append(cc)
        if not cc.reliable:
            report.unreliable_cards.append(cc)
            bc = cc.bottleneck_color
            report.color_bottlenecks[bc] = report.color_bottlenecks.get(bc, 0) + 1

    # Sort: most demanding (lowest on-curve probability) first
    report.cards.sort(key=lambda c: c.on_curve_probability)
    report.unreliable_cards.sort(key=lambda c: c.on_curve_probability)

    sources = {c.source for c in report.cards}
    report.source = sources.pop() if len(sources) == 1 else ("mixed" if sources else "estimated")

    return report
