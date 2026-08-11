"""Colour-weighted mana curve: do we get the lands we need, when we need them?

An ordinary mana curve counts how many two-drops and three-drops a deck has.
It cannot tell you that your three-drops are {B}{B}{B} and you are playing
nine black sources. This module answers the colour-weighted version of the
question, measured from the simulation rather than approximated:

  For each turn, what colours do the cards at that cost demand, how many
  sources of each colour were actually on the battlefield by then, and what
  share of games could pay for them?

Measured, not modelled. A hypergeometric estimate (see
`analysis.castability`) assumes independent draws and no interference; this
counts what happened across real simulated games, so mulligans, ramp spells
that fetch lands, lands that enter tapped, and card draw are all already
baked into the numbers.

The key metric is deliberately *hypothetical* castability: at turn N, could
the board have paid for this card, whether or not we happened to draw it.
That isolates the mana base from draw luck, which is the whole point — "did
I draw my bomb" is a different question from "could I have cast it".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from densa_deck.goldfish.mana import ManaCost, card_mana_cost, can_pay, source_colors
from densa_deck.models import Deck, Zone

# On-curve hit rates above/below which a colour is called solid or short.
SOLID_THRESHOLD = 0.90
THIN_THRESHOLD = 0.75

# Games sampled for the colour analysis. The colour maths is per-turn and
# per-card, so sampling keeps a 10k-game batch from paying for it 10k times;
# a few hundred games is already tight enough for a percentage.
DEFAULT_RELIABILITY_GAMES = 300

_COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}


@dataclass
class CurvePoint:
    """One cost step of the colour-weighted curve."""

    turn: int
    cards_at_cost: int = 0
    requirement: dict[str, int] = field(default_factory=dict)
    avg_sources: dict[str, float] = field(default_factory=dict)
    castable_rate: float = 0.0  # share of games able to pay for cards at this cost
    verdict: str = "solid"


@dataclass
class ColorLine:
    """Per-colour summary across the whole deck."""

    color: str
    name: str = ""
    sources_in_deck: int = 0
    peak_requirement: int = 0  # deepest single-card demand, e.g. {B}{B}{B} -> 3
    avg_sources_by_turn: dict[int, float] = field(default_factory=dict)
    on_curve_hit_rate: float = 0.0
    verdict: str = "solid"
    recommendation: str = ""


@dataclass
class ManaReliabilityReport:
    """Colour-weighted mana curve plus per-card on-curve castability."""

    games_analyzed: int = 0
    colors: list[ColorLine] = field(default_factory=list)
    curve: list[CurvePoint] = field(default_factory=list)
    card_on_curve: list[tuple[str, int, float]] = field(default_factory=list)
    unreliable_cards: list[tuple[str, int, float]] = field(default_factory=list)
    # Share of card/turn checks where there was enough total mana but the
    # colours didn't line up. This is colour screw, isolated from mana screw.
    color_screw_rate: float = 0.0
    overall_on_curve_rate: float = 0.0

    @property
    def over_extended(self) -> bool:
        """Three or more short colours can't be fixed by adding sources.

        There are only so many lands; when most of the deck's colours miss,
        the costs are the problem, not the mana base.
        """
        return sum(1 for c in self.colors if c.verdict == "short") >= 3

    def summary_line(self) -> str:
        short = [c.color for c in self.colors if c.verdict == "short"]
        if self.over_extended:
            names = ", ".join(_COLOR_NAMES.get(c, c) for c in short)
            return (
                f"Over-extended on colour — {names} all miss on curve. "
                "Cut double-pip costs rather than chasing more sources."
            )
        if short:
            return f"Short on {', '.join(_COLOR_NAMES.get(c, c) for c in short)}"
        thin = [c.color for c in self.colors if c.verdict == "thin"]
        if thin:
            return f"Thin on {', '.join(_COLOR_NAMES.get(c, c) for c in thin)}"
        return "Mana base supports the curve"


class ReliabilityCollector:
    """Accumulates per-turn colour observations across simulated games.

    One instance is shared across the sampled games of a batch; `observe` is
    called once per turn with the board as it stood that turn.
    """

    def __init__(self, deck: Deck, max_turns: int):
        self.max_turns = max_turns
        self.games = 0
        # colour -> turn -> total sources seen (divided by games at the end)
        self._sources: dict[str, Counter] = defaultdict(Counter)
        # card name -> [checks, successes] at its on-curve turn
        self._card_checks: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        self._screw_checks = 0
        self._screw_hits = 0

        # Unique nonland cards grouped by the turn they'd be cast on curve.
        self.costs: dict[str, ManaCost] = {}
        self.by_turn: dict[int, list[str]] = defaultdict(list)
        self.deck_sources: Counter = Counter()

        seen: set[str] = set()
        for entry in deck.entries:
            card = entry.card
            if card is None or entry.zone in (Zone.MAYBEBOARD, Zone.SIDEBOARD):
                continue
            if card.is_land or _produces_mana(card):
                for color in source_colors(card):
                    self.deck_sources[color] += entry.quantity
            if card.is_land or card.name in seen:
                continue
            seen.add(card.name)
            cost = card_mana_cost(card)
            if cost.total == 0:
                continue
            self.costs[card.name] = cost
            on_curve_turn = min(max(1, cost.total), max_turns)
            self.by_turn[on_curve_turn].append(card.name)

    def observe(self, state, turn: int):
        """Record what this turn's board could produce."""
        if turn > self.max_turns:
            return
        units = _potential_units(state)
        for color, count in state.color_sources_in_play().items():
            self._sources[color][turn] += count

        for name in self.by_turn.get(turn, ()):
            cost = self.costs[name]
            checks = self._card_checks[name]
            checks[0] += 1
            payable = can_pay(cost, units)
            if payable:
                checks[1] += 1
            # Enough total mana but unpayable => colour screw, not mana screw.
            if not payable and len(units) >= cost.total:
                self._screw_hits += 1
            self._screw_checks += 1

    def finish(self) -> ManaReliabilityReport:
        report = ManaReliabilityReport(games_analyzed=self.games)
        if self.games == 0:
            return report

        peak: Counter = Counter()
        for cost in self.costs.values():
            for color, count in cost.pips.items():
                peak[color] = max(peak[color], count)

        # Per-card on-curve castability.
        rows: list[tuple[str, int, float]] = []
        for name, (checks, hits) in self._card_checks.items():
            if checks == 0:
                continue
            rate = hits / checks
            rows.append((name, self.costs[name].total, round(rate, 4)))
        rows.sort(key=lambda r: r[2])
        report.card_on_curve = rows
        report.unreliable_cards = [r for r in rows if r[2] < THIN_THRESHOLD]
        if rows:
            report.overall_on_curve_rate = round(sum(r[2] for r in rows) / len(rows), 4)
        if self._screw_checks:
            report.color_screw_rate = round(self._screw_hits / self._screw_checks, 4)

        # Per-colour lines.
        colors_used = sorted({c for cost in self.costs.values() for c in cost.pips})
        for color in colors_used:
            line = ColorLine(
                color=color,
                name=_COLOR_NAMES.get(color, color),
                sources_in_deck=self.deck_sources.get(color, 0),
                peak_requirement=peak.get(color, 0),
            )
            for turn in range(1, self.max_turns + 1):
                line.avg_sources_by_turn[turn] = round(
                    self._sources[color][turn] / self.games, 2
                )
            relevant = [
                rate for name, _cmc, rate in rows if color in self.costs[name].pips
            ]
            line.on_curve_hit_rate = round(sum(relevant) / len(relevant), 4) if relevant else 1.0
            line.verdict = _verdict(line.on_curve_hit_rate)
            report.colors.append(line)

        # Recommendations are written after every colour is scored, because
        # the right advice for one short colour ("add sources") is the wrong
        # advice when four are short ("you're in too many colours").
        short_count = sum(1 for c in report.colors if c.verdict == "short")
        for line in report.colors:
            line.recommendation = _recommend(line, short_count)

        # Colour-weighted curve.
        rate_by_name = {name: rate for name, _cmc, rate in rows}
        for turn in sorted(self.by_turn):
            names = self.by_turn[turn]
            requirement: dict[str, int] = {}
            for name in names:
                for color, count in self.costs[name].pips.items():
                    requirement[color] = max(requirement.get(color, 0), count)
            rates = [rate_by_name[n] for n in names if n in rate_by_name]
            point = CurvePoint(
                turn=turn,
                cards_at_cost=len(names),
                requirement=requirement,
                avg_sources={
                    color: round(self._sources[color][turn] / self.games, 2)
                    for color in requirement
                },
                castable_rate=round(sum(rates) / len(rates), 4) if rates else 1.0,
            )
            point.verdict = _verdict(point.castable_rate)
            report.curve.append(point)

        return report


def _verdict(rate: float) -> str:
    if rate >= SOLID_THRESHOLD:
        return "solid"
    if rate >= THIN_THRESHOLD:
        return "thin"
    return "short"


def _recommend(line: ColorLine, short_colors: int = 1) -> str:
    """Plain-language advice for one colour.

    When three or more colours are short there is no land count that fixes
    it — the deck wants more colours than a mana base can serve — so the
    advice switches from "add sources" to "cut the demanding costs".
    """
    if line.verdict == "solid":
        return ""
    demand = max(1, line.peak_requirement)
    if short_colors >= 3:
        return (
            f"cut {line.name.lower()} cards needing {demand}+ pips — "
            "too many colours to support all of them"
        )
    # Rough scaling: each missing 10% of on-curve reliability is worth about
    # one more source at typical Commander deck sizes. Deliberately coarse —
    # it points a direction, it does not pretend to be a precise table.
    shortfall = max(1, round((SOLID_THRESHOLD - line.on_curve_hit_rate) * 20))
    return (
        f"add ~{shortfall} more {line.name.lower()} source"
        f"{'s' if shortfall != 1 else ''} "
        f"(deepest demand is {demand} pip{'s' if demand != 1 else ''})"
    )


def _produces_mana(card) -> bool:
    text = (card.oracle_text or "").lower()
    return "{t}: add" in text or "add one mana" in text


def _potential_units(state) -> list[frozenset]:
    """Every mana this board could make on a turn, ignoring what got tapped.

    Tapped-ness reflects what we chose to cast, not what the mana base is
    capable of, so a mana-base diagnostic should look past it.
    """
    units: list[frozenset] = []
    for perm in state.battlefield:
        if not perm.produces_mana():
            continue
        if perm.is_creature() and perm.summoning_sick:
            continue
        amount = max(1, perm.effects.mana_produced)
        units.extend([perm.produced_colors] * amount)
    from densa_deck.goldfish.state import ANY_COLOR

    units.extend([ANY_COLOR] * state.treasures)
    return units
