"""Game state model for goldfish simulation.

Tracks all zones, mana, life, and per-turn metrics during a solo game.
This is deliberately simplified — no stack, no priority, no opponent actions.
The goal is to approximate how the deck functions, not to be rules-complete.
"""

from __future__ import annotations

import random
import re as _re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from densa_deck.goldfish.effects import CardEffects, parse_effects
from densa_deck.goldfish.mana import (
    ALL_COLORS,
    BASIC_TYPE_COLORS,
    COLORLESS,
    ManaCost,
    pay_cost,
    source_colors,
)
from densa_deck.goldfish.mana import produces_mana as card_produces_mana
from densa_deck.models import Card, CardTag, DeckEntry, Zone

# A Treasure, or any "add one mana of any colour" source, can pay for
# anything — including a colourless {C} requirement.
ANY_COLOR = frozenset(ALL_COLORS | {COLORLESS})

# How deep a cascade chain may go before we stop resolving it.
MAX_CASCADE_DEPTH = 8


def _remove_identity(zone: list, entry: DeckEntry) -> bool:
    """Remove `entry` from a zone by identity rather than equality.

    DeckEntry is a Pydantic model, so `list.remove` compares field by field
    across every card in the zone — measurable in a hot loop, and it can
    pick a different-but-equal copy than the one we meant.
    """
    for i, held in enumerate(zone):
        if held is entry:
            del zone[i]
            return True
    return False


def _printed_power(entry: DeckEntry) -> int:
    card = entry.card
    if card is None or not card.power or not card.power.isdigit():
        return 0
    return int(card.power)


class Phase(str, Enum):
    UNTAP = "untap"
    DRAW = "draw"
    MAIN = "main"
    END = "end"


_NO_EFFECTS = CardEffects()


@dataclass
class Permanent:
    """A card on the battlefield.

    The card behind a permanent never changes, so everything derived from it
    — its effects, whether it taps for mana, what colours it makes — is
    resolved once on first use and kept. Profiling a 400-game batch showed
    `effects` alone being recomputed 741k times.
    """

    entry: DeckEntry
    tapped: bool = False
    summoning_sick: bool = True  # Can't tap for mana/attack until next turn
    counters: int = 0

    # Lazily-filled caches. Not part of the permanent's identity.
    _effects: CardEffects | None = field(default=None, repr=False, compare=False)
    _produces: bool | None = field(default=None, repr=False, compare=False)
    _colors: frozenset | None = field(default=None, repr=False, compare=False)

    @property
    def card(self) -> Card | None:
        return self.entry.card

    @property
    def name(self) -> str:
        return self.entry.card_name

    def is_land(self) -> bool:
        card = self.entry.card
        return card is not None and card.is_land

    def is_creature(self) -> bool:
        card = self.entry.card
        return card is not None and card.is_creature

    def produces_mana(self) -> bool:
        if self._produces is None:
            self._produces = self._compute_produces_mana()
        return self._produces

    def _compute_produces_mana(self) -> bool:
        card = self.entry.card
        if card is None:
            return False
        if card.is_land:
            # Not every land taps for mana — Maze of Ith and friends are
            # permanents that happen to be lands.
            return card_produces_mana(card)
        if card.tags and (CardTag.MANA_ROCK in card.tags or CardTag.MANA_DORK in card.tags):
            return True
        return False

    @property
    def effects(self) -> CardEffects:
        if self._effects is None:
            card = self.entry.card
            self._effects = _NO_EFFECTS if card is None else parse_effects(card)
        return self._effects

    @property
    def produced_colors(self) -> frozenset:
        """Colours this permanent's mana ability can make."""
        if self._colors is None:
            self._colors = source_colors(self.entry.card)
        return self._colors

    def available_mana(self) -> int:
        """Mana this permanent can produce.

        Read from the card's '{T}: Add ...' ability rather than assumed to
        be 1 — Sol Ring makes two, bounce lands make two, Ancient Tomb
        makes two. Falls back to 1 when the text can't be parsed, which is
        the old behaviour and the right default for a basic land.
        """
        if self.tapped or not self.produces_mana():
            return 0
        if self.is_creature() and self.summoning_sick:
            return 0  # Dorks can't tap with summoning sickness
        return max(1, self.effects.mana_produced)

    def power_value(self) -> int:
        """Printed power plus any +1/+1 counters we've put on it."""
        if self.card is None or not self.card.power or not self.card.power.isdigit():
            return 0
        return int(self.card.power) + self.counters


@dataclass
class TurnMetrics:
    """Metrics captured at the end of each turn."""

    turn: int = 0
    lands_in_play: int = 0
    mana_available: int = 0
    mana_spent: int = 0
    cards_in_hand: int = 0
    cards_cast: int = 0
    creatures_in_play: int = 0
    total_power: int = 0
    damage_dealt: int = 0
    cumulative_damage: int = 0
    land_played: bool = False
    spells_cast: list[str] = field(default_factory=list)


@dataclass
class GameState:
    """Complete state of a goldfish game."""

    # Zones
    library: list[DeckEntry] = field(default_factory=list)
    hand: list[DeckEntry] = field(default_factory=list)
    battlefield: list[Permanent] = field(default_factory=list)
    graveyard: list[DeckEntry] = field(default_factory=list)
    command_zone: list[DeckEntry] = field(default_factory=list)

    # Game state
    turn: int = 0
    phase: Phase = Phase.UNTAP
    life: int = 40
    opponent_life: int = 40
    land_played_this_turn: bool = False
    land_drops_this_turn: int = 0
    mana_pool: int = 0
    # Colour detail for the floating mana counted by `mana_pool`. Kept as a
    # parallel list rather than replacing the int so callers (and tests) that
    # set `mana_pool` directly keep working — see `mana_units()`.
    floating_colors: list[frozenset] = field(default_factory=list)
    mana_spent_this_turn: int = 0
    cards_cast_this_turn: int = 0
    commander_tax: int = 0  # +2 for each previous cast from command zone
    on_play: bool = True
    mulligans_taken: int = 0

    # Treasure tokens: one-shot mana, cracked after permanents are tapped.
    treasures: int = 0
    # Creature tokens are tracked in aggregate rather than as real cards —
    # they have no DeckEntry, and only their count and power matter here.
    token_count: int = 0
    token_power: int = 0
    # Tokens that were already in play when this turn began, and so are
    # not summoning sick. Snapshotted in begin_turn().
    attacking_token_count: int = 0
    attacking_token_power: int = 0
    # Damage from pump spells cast this turn, added to the combat step.
    bonus_damage_this_turn: int = 0
    # Extra turns owed from Time Warp effects.
    pending_extra_turns: int = 0
    # Guards against a cascade chain recursing without end.
    _cascade_depth: int = 0
    # Extra generic mana an opponent's stax effects add to each of our
    # spells. Set by the matchup simulator; zero in a goldfish, which has
    # no opponent.
    cost_increase: int = 0

    # Per-turn history
    turn_history: list[TurnMetrics] = field(default_factory=list)
    spells_cast_this_turn: list[str] = field(default_factory=list)

    # Tracking
    total_damage_dealt: int = 0
    commander_cast_turn: int | None = None
    game_over: bool = False

    # --- Zone queries ---

    @property
    def lands_in_play(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.is_land()]

    @property
    def creatures_in_play(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.is_creature()]

    @property
    def mana_sources(self) -> list[Permanent]:
        return [p for p in self.battlefield if p.produces_mana()]

    @property
    def anthem_bonus(self) -> int:
        """Total +N/+N your creatures get from anthem effects in play."""
        return sum(p.effects.anthem_power for p in self.battlefield)

    @property
    def creature_count(self) -> int:
        """Creatures on the battlefield, tokens included."""
        return len(self.creatures_in_play) + self.token_count

    @property
    def total_power(self) -> int:
        bonus = self.anthem_bonus
        card_power = sum(p.power_value() + bonus for p in self.creatures_in_play)
        return card_power + self.token_power + self.token_count * bonus

    @property
    def cost_reduction(self) -> int:
        """Net generic cost change from permanents in play.

        Reductions (Goblin Electromancer) net against any tax an opponent is
        applying (Sphere of Resistance), so a stax opponent genuinely makes
        our spells more expensive rather than being tracked and ignored.
        """
        reduction = sum(p.effects.cost_reduction for p in self.battlefield)
        return reduction - self.cost_increase

    @property
    def extra_land_drops(self) -> int:
        """Additional land drops granted by permanents (Exploration, Azusa)."""
        return sum(p.effects.extra_land_drops for p in self.battlefield)

    @property
    def land_drops_allowed(self) -> int:
        return 1 + self.extra_land_drops

    @property
    def mana_multiplier(self) -> int:
        """Highest mana multiplier in play (Mana Reflection). Multipliers
        don't stack multiplicatively in this model — the best one wins."""
        return max([1] + [p.effects.mana_multiplier for p in self.battlefield])

    @property
    def available_mana(self) -> int:
        from_permanents = sum(p.available_mana() for p in self.battlefield)
        return from_permanents * self.mana_multiplier + self.treasures

    # --- Zone operations ---

    def draw(self, count: int = 1) -> list[DeckEntry]:
        """Draw cards from library to hand."""
        drawn = []
        for _ in range(count):
            if not self.library:
                break
            card = self.library.pop(0)
            self.hand.append(card)
            drawn.append(card)
        return drawn

    def play_land(self, entry: DeckEntry):
        """Play a land from hand to battlefield."""
        if entry in self.hand:
            self.hand.remove(entry)
        perm = Permanent(entry=entry, tapped=False, summoning_sick=False)
        # Conditional lands read the board they're entering, so this is
        # evaluated before the new land joins the battlefield.
        if entry.card and _enters_tapped(entry.card, self):
            perm.tapped = True
        self.battlefield.append(perm)
        self.land_played_this_turn = True
        self.land_drops_this_turn += 1

    def cast_spell(self, entry: DeckEntry, from_command_zone: bool = False):
        """Cast a nonland spell from hand (or command zone) to battlefield/graveyard."""
        if from_command_zone:
            _remove_identity(self.command_zone, entry)
            self.commander_tax += 2
            if self.commander_cast_turn is None:
                self.commander_cast_turn = self.turn
        else:
            _remove_identity(self.hand, entry)

        card = entry.card
        if card is None:
            self.graveyard.append(entry)
            return

        # Permanents go to battlefield, instants/sorceries to graveyard
        if card.is_instant or card.is_sorcery:
            self.graveyard.append(entry)
        else:
            perm = Permanent(entry=entry, tapped=False, summoning_sick=True)
            # Haste from an anthem-style permanent already in play lets this
            # creature attack the turn it lands.
            if self.grants_haste():
                perm.summoning_sick = False
            # Creatures that arrive already carrying counters (Kalonian Hydra
            # enters as a 4/4 with four counters, so it swings as an 8/8).
            entering = parse_effects(card).enters_with_counters
            if entering:
                perm.counters += entering * self.counter_multiplier
            self.battlefield.append(perm)

        self.spells_cast_this_turn.append(entry.card_name)
        self.cards_cast_this_turn += 1

        # Resolve what the card actually does. Without this the simulator
        # treats every spell as a blank that only costs mana.
        fx = parse_effects(card)
        self.apply_immediate_effects(fx)
        if fx.cascade:
            self.resolve_cascade(card.display_cmc(), fx.cascade)

    def grants_haste(self) -> bool:
        return any(p.effects.grants_haste for p in self.battlefield)

    def apply_immediate_effects(self, fx: CardEffects):
        """Resolve a card's one-shot effects at the moment it resolves."""
        if fx.draw:
            self.draw(fx.draw)

        # A tutor puts a card in hand; without a real "which card do I want"
        # model, drawing one is the honest approximation of the card advantage
        # (it deliberately does NOT model the selection quality).
        if fx.tutor_to_hand:
            self.draw(fx.tutor_to_hand)

        if fx.scry:
            self.scry(fx.scry)

        # Land ramp: pull lands out of the library onto the battlefield.
        for _ in range(fx.lands_to_battlefield):
            land_entry = self._take_land_from_library()
            if land_entry is None:
                break
            perm = Permanent(entry=land_entry, tapped=fx.lands_enter_tapped,
                             summoning_sick=False)
            self.battlefield.append(perm)
        for _ in range(fx.lands_to_hand):
            land_entry = self._take_land_from_library()
            if land_entry is None:
                break
            self.hand.append(land_entry)

        if fx.ritual_mana:
            if fx.ritual_colors:
                for color in fx.ritual_colors:
                    self.add_mana(frozenset({color}))
                # "Add {B}{B}{B} and one mana of any colour" style remainders.
                leftover = fx.ritual_mana - len(fx.ritual_colors)
                if leftover > 0:
                    self.add_mana(ANY_COLOR, leftover)
            else:
                # Colour-agnostic wording ("add two mana of any one colour").
                self.add_mana(ANY_COLOR, fx.ritual_mana)
        if fx.treasure_tokens:
            self.treasures += fx.treasure_tokens

        if fx.creature_tokens:
            self.token_power += fx.creature_tokens * max(1, fx.creature_token_power)
            self.token_count += fx.creature_tokens

        multiplier = self.counter_multiplier
        if fx.counters_added:
            # Put counters on our biggest creature — the choice a player
            # making a clock would usually make.
            creatures = self.creatures_in_play
            if creatures:
                best = max(creatures, key=lambda p: p.power_value())
                best.counters += fx.counters_added * multiplier

        if fx.counters_each:
            for perm in self.creatures_in_play:
                perm.counters += fx.counters_each * multiplier

        if fx.proliferate:
            self.proliferate(fx.proliferate)

        if fx.mill:
            self.mill(fx.mill)
        if fx.reanimate:
            self.reanimate(fx.reanimate)

        if fx.direct_damage:
            self.opponent_life -= fx.direct_damage
            self.total_damage_dealt += fx.direct_damage
        if fx.pump_power:
            self.bonus_damage_this_turn += fx.pump_power
        if fx.extra_turns:
            self.pending_extra_turns += fx.extra_turns

    @property
    def counter_multiplier(self) -> int:
        """Highest counter doubler in play (Branching Evolution).

        Doublers don't stack multiplicatively in this model — the best one
        wins, which matches how the mana multiplier is handled.
        """
        return max([1] + [p.effects.counter_multiplier for p in self.battlefield])

    def pay_alternative_costs(self, card: Card, discount_used: int):
        """Spend the resources an alternative cost consumed.

        Convoke taps creatures — which is why convoking your whole board
        means not attacking with it — and delve exiles the graveyard cards
        it ate. Charging nothing would make both keywords strictly free,
        which is exactly the sort of flattery this simulator should avoid.
        """
        if discount_used <= 0:
            return
        fx = parse_effects(card)
        remaining = discount_used

        if fx.delve and remaining > 0:
            eaten = min(remaining, len(self.graveyard))
            del self.graveyard[:eaten]
            remaining -= eaten

        if fx.convoke and remaining > 0:
            for perm in self.creatures_in_play:
                if remaining <= 0:
                    break
                if not perm.tapped:
                    perm.tapped = True
                    remaining -= 1

        if fx.improvise and remaining > 0:
            for perm in self.battlefield:
                if remaining <= 0:
                    break
                if perm.card and perm.card.is_artifact and not perm.tapped:
                    perm.tapped = True
                    remaining -= 1

    def mill(self, count: int):
        """Move cards from the top of the library into the graveyard.

        Matters beyond flavour: the graveyard feeds delve, and the combo
        checker already counts graveyard cards as being in your possession.
        """
        for _ in range(min(count, len(self.library))):
            self.graveyard.append(self.library.pop(0))

    def reanimate(self, count: int = 1):
        """Return creatures from the graveyard to the battlefield."""
        for _ in range(count):
            best = None
            for entry in self.graveyard:
                if entry.card and entry.card.is_creature:
                    if best is None or _printed_power(entry) > _printed_power(best):
                        best = entry
            if best is None:
                return
            self.graveyard.remove(best)
            perm = Permanent(entry=best, tapped=False, summoning_sick=True)
            if self.grants_haste():
                perm.summoning_sick = False
            self.battlefield.append(perm)

    def _biggest_creature(self) -> "Permanent | None":
        creatures = self.creatures_in_play
        return max(creatures, key=lambda p: p.power_value()) if creatures else None

    def proliferate(self, times: int = 1):
        """Add one counter to each permanent that already has one."""
        for _ in range(times):
            for perm in self.battlefield:
                if perm.counters > 0:
                    perm.counters += 1

    def resolve_cascade(self, source_cmc: float, times: int = 1):
        """Exile from the top until a cheaper nonland card turns up, cast it free.

        Bounded by `_cascade_depth` so a cascade that hits another cascade
        can't recurse without end — real decks chain these deliberately.
        """
        if self._cascade_depth >= MAX_CASCADE_DEPTH:
            return
        for _ in range(times):
            hit_index = None
            for i, entry in enumerate(self.library):
                card = entry.card
                if card is None or card.is_land:
                    continue
                if card.display_cmc() < source_cmc:
                    hit_index = i
                    break
            if hit_index is None:
                return
            entry = self.library.pop(hit_index)
            # Everything exiled above the hit is gone from the library; the
            # simulator treats exile as removal, so those cards just leave.
            del self.library[:hit_index]
            self._cascade_depth += 1
            try:
                # Free cast: no mana changes hands.
                self.cast_spell(entry)
            finally:
                self._cascade_depth -= 1

    def _take_land_from_library(self) -> DeckEntry | None:
        """Pull the first land out of the library (a ramp spell's search)."""
        for i, entry in enumerate(self.library):
            if entry.card and entry.card.is_land:
                return self.library.pop(i)
        return None

    def scry(self, count: int):
        """Bottom top-of-library cards we don't want.

        Keeps lands while we're short of them and spells once we're not —
        a crude read of what a player scrying actually does, but far closer
        than ignoring scry entirely.
        """
        want_lands = len(self.lands_in_play) < 4
        for _ in range(min(count, len(self.library))):
            top = self.library[0]
            is_land = bool(top.card and top.card.is_land)
            if is_land == want_lands:
                break  # Happy with the top card; stop looking.
            self.library.append(self.library.pop(0))

    def mana_units(self) -> list[frozenset]:
        """Floating mana as individual units, each with its usable colours.

        When `mana_pool` exceeds the recorded colour detail — which happens
        only if a caller set the pool directly — the surplus is treated as
        any-colour rather than silently blocking casts.
        """
        units = list(self.floating_colors)
        shortfall = self.mana_pool - len(units)
        if shortfall > 0:
            units.extend([ANY_COLOR] * shortfall)
        return units[: self.mana_pool] if self.mana_pool < len(units) else units

    def add_mana(self, colors: frozenset, amount: int = 1):
        """Add floating mana of a given colour set to the pool."""
        for _ in range(amount):
            self.floating_colors.append(colors)
        self.mana_pool += amount

    def tap_for_mana(self, count: int) -> int:
        """Tap mana sources to produce mana. Returns amount actually produced.

        Permanents are tapped first and Treasures cracked only for the
        shortfall, since a Treasure is spent permanently. Each unit records
        the colours its source can make, so colour screw is now visible.
        """
        multiplier = self.mana_multiplier
        produced = 0
        for p in self.mana_sources:
            if produced >= count:
                break
            avail = p.available_mana()
            if avail > 0 and not p.tapped:
                p.tapped = True
                gained = avail * multiplier
                self.floating_colors.extend([p.produced_colors] * gained)
                produced += gained
        while produced < count and self.treasures > 0:
            self.treasures -= 1
            self.floating_colors.append(ANY_COLOR)
            produced += 1
        self.mana_pool += produced
        return produced

    def pay_for(self, cost: ManaCost) -> bool:
        """Spend floating mana to pay a cost, colours included.

        Returns False and spends nothing when the colours don't line up,
        which is exactly the colour-screw case the old integer pool missed.
        """
        units = self.mana_units()
        chosen = pay_cost(cost, units)
        if chosen is None:
            return False
        for index in sorted(chosen, reverse=True):
            if index < len(self.floating_colors):
                self.floating_colors.pop(index)
        spent = len(chosen)
        self.mana_pool -= spent
        self.mana_spent_this_turn += spent
        return True

    def can_pay_for(self, cost: ManaCost) -> bool:
        return pay_cost(cost, self.mana_units()) is not None

    def color_sources_in_play(self) -> Counter:
        """How many untapped-or-tapped permanents can make each colour.

        Counts sources, not mana: a dual land counts for both its colours,
        which is what "how many black sources do I have" means to a player.
        """
        counts: Counter = Counter()
        for p in self.battlefield:
            if not p.produces_mana():
                continue
            for color in p.produced_colors:
                counts[color] += 1
        return counts

    def spend_mana(self, amount: int) -> bool:
        """Spend mana from pool, ignoring colour. Returns True if enough was available.

        Colour-blind by design: this is the quantity-only path kept for
        callers that don't have a parsed cost. `pay_for` is the colour-aware
        equivalent and is what the casting heuristics use.
        """
        if self.mana_pool >= amount:
            self.mana_pool -= amount
            self.mana_spent_this_turn += amount
            # Drop the least flexible units first so what remains stays useful.
            self.floating_colors.sort(key=len)
            del self.floating_colors[: min(amount, len(self.floating_colors))]
            return True
        return False

    def attack_with_all(self) -> int:
        """Attack with all non-sick creatures. Returns damage dealt.

        Includes +1/+1 counters, anthem bonuses, creature tokens, and any
        pump cast this turn — all of which the old version ignored.
        """
        bonus = self.anthem_bonus
        damage = 0
        for p in self.creatures_in_play:
            if p.summoning_sick:
                continue
            damage += p.power_value() + bonus
            p.tapped = True
        # Tokens made on an earlier turn can attack; ones made this turn
        # can't, which we approximate by only swinging with tokens that
        # survived to the start of this turn.
        damage += self.attacking_token_power + self.attacking_token_count * bonus
        damage += self.bonus_damage_this_turn
        self.opponent_life -= damage
        self.total_damage_dealt += damage
        return damage

    # --- Turn structure ---

    def begin_turn(self):
        """Start a new turn: untap, upkeep, draw."""
        self.turn += 1
        self.phase = Phase.UNTAP
        self.land_played_this_turn = False
        self.land_drops_this_turn = 0
        self.mana_pool = 0
        self.floating_colors = []
        self.mana_spent_this_turn = 0
        self.cards_cast_this_turn = 0
        self.spells_cast_this_turn = []
        self.bonus_damage_this_turn = 0
        # Tokens present now have lost summoning sickness and can attack.
        self.attacking_token_count = self.token_count
        self.attacking_token_power = self.token_power

        # Untap
        for p in self.battlefield:
            p.tapped = False
            p.summoning_sick = False  # Creatures that survived a full turn cycle

        # Upkeep: recurring effects from permanents already in play
        # (Phyrexian Arena's extra card, Smothering Tithe's Treasures).
        multiplier = self.counter_multiplier
        for p in list(self.battlefield):
            fx = p.effects
            if fx.treasure_per_turn:
                self.treasures += fx.treasure_per_turn
            if fx.counters_per_turn:
                # The permanent grows itself if it's a creature, otherwise
                # it feeds our biggest threat.
                target = p if p.is_creature() else self._biggest_creature()
                if target is not None:
                    target.counters += fx.counters_per_turn * multiplier
        proliferations = sum(p.effects.proliferate_per_turn for p in self.battlefield)
        if proliferations:
            self.proliferate(proliferations)

        # Draw (skip T1 on the play)
        self.phase = Phase.DRAW
        if not (self.turn == 1 and self.on_play):
            self.draw(1)
        extra_draw = sum(p.effects.draw_per_turn for p in self.battlefield)
        if extra_draw:
            self.draw(extra_draw)

        self.phase = Phase.MAIN

    def end_turn(self) -> TurnMetrics:
        """End the turn: discard to hand size, record metrics."""
        self.phase = Phase.END

        # Discard to 7 (simplified)
        while len(self.hand) > 7 and self.hand:
            # Discard highest-CMC card
            worst = max(self.hand, key=lambda e: e.card.cmc if e.card else 0)
            self.hand.remove(worst)
            self.graveyard.append(worst)

        # Record metrics
        damage_this_turn = self.attack_with_all() if self.turn >= 2 else 0

        metrics = TurnMetrics(
            turn=self.turn,
            lands_in_play=len(self.lands_in_play),
            mana_available=self.available_mana + self.mana_spent_this_turn,
            mana_spent=self.mana_spent_this_turn,
            cards_in_hand=len(self.hand),
            cards_cast=self.cards_cast_this_turn,
            creatures_in_play=self.creature_count,
            total_power=self.total_power,
            damage_dealt=damage_this_turn,
            cumulative_damage=self.total_damage_dealt,
            land_played=self.land_played_this_turn,
            spells_cast=list(self.spells_cast_this_turn),
        )
        self.turn_history.append(metrics)

        if self.opponent_life <= 0:
            self.game_over = True

        return metrics

    # --- Setup ---

    def setup_library(self, entries: list[DeckEntry], shuffle: bool = True):
        """Build and shuffle the library from deck entries."""
        pool: list[DeckEntry] = []
        for entry in entries:
            if entry.zone == Zone.COMMANDER:
                self.command_zone.append(entry)
                continue
            if entry.zone in (Zone.MAYBEBOARD, Zone.SIDEBOARD):
                continue
            for _ in range(entry.quantity):
                pool.append(entry)
        if shuffle:
            random.shuffle(pool)
        self.library = pool

    def draw_opening_hand(self, size: int = 7) -> list[DeckEntry]:
        """Draw an opening hand."""
        return self.draw(size)


# Grab the whole condition clause, then scan it — "a Plains or an Island"
# names two types and a single non-greedy match would only ever see the first.
_UNLESS_CLAUSE_RE = _re.compile(r"enters tapped unless ([^.]*)")
_BASIC_TYPE_RE = _re.compile(r"\b(plains|island|swamp|mountain|forest)s?\b")
_UNLESS_FEWER_RE = _re.compile(
    r"enters tapped unless you control (\w+) or fewer other lands")
_UNLESS_BASICS_RE = _re.compile(
    r"enters tapped unless you control (\w+) or more basic lands")
_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _enters_tapped(card: Card, state: "GameState | None" = None) -> bool:
    """Does this land enter tapped, given the board it's entering?

    Conditional lands — check lands ("unless you control a Plains or an
    Island"), fast lands ("unless you control two or fewer other lands") —
    are the common case and were previously treated as always tapped, which
    understates every mana base built on them. When the condition can't be
    read, we stay conservative and assume tapped.
    """
    ot = card.oracle_text.lower()
    if not card.is_land:
        return False
    # Fetch lands don't enter tapped themselves
    if "search your library" in ot:
        return False
    # Shock lands: optional
    if "you may pay" in ot and "enters" in ot:
        return False  # Assume player pays life

    if "enters tapped unless" in ot:
        if state is None:
            return True
        lands = state.lands_in_play

        match = _UNLESS_FEWER_RE.search(ot)
        if match:
            limit = _WORD_NUMS.get(match.group(1), 99) if not match.group(1).isdigit() \
                else int(match.group(1))
            # "other lands" — this one isn't on the battlefield yet.
            return len(lands) > limit

        match = _UNLESS_BASICS_RE.search(ot)
        if match:
            needed = _WORD_NUMS.get(match.group(1), 99) if not match.group(1).isdigit() \
                else int(match.group(1))
            basics = sum(
                1 for p in lands
                if p.card and "basic" in (p.card.type_line or "").lower()
            )
            return basics < needed

        clause_match = _UNLESS_CLAUSE_RE.search(ot)
        clause = clause_match.group(1) if clause_match else ""
        types = _BASIC_TYPE_RE.findall(clause)
        if types:
            wanted = {BASIC_TYPE_COLORS[t] for t in types if t in BASIC_TYPE_COLORS}
            have = set()
            for perm in lands:
                have |= perm.produced_colors
            return not (wanted & have)

        return True  # Unreadable condition — assume the slow case.

    # Explicit ETB tapped
    if "enters tapped" in ot or "enters the battlefield tapped" in ot:
        return True
    # Triomes and similar
    if "cycling" in ot and card.produced_mana and len(set(card.produced_mana) & {"W", "U", "B", "R", "G"}) >= 3:
        return True
    return False
