"""Oracle-text effect parsing for the goldfish simulator.

The simulator used to move a resolved spell straight to the battlefield or
graveyard without doing anything else, so Cultivate never fetched a land,
Divination never drew a card, and Sol Ring produced one mana. Every deck
was therefore measured as if half its cards were blanks.

This module closes that gap the same way `classification/tagger.py` does:
deterministic phrase and regex matching over oracle text, no model, no
network. `parse_effects()` turns a card into a `CardEffects` record that
`goldfish/state.py` applies at the right moment (on resolution, while on
the battlefield, or at the start of each turn).

Scope is deliberately bounded to effects that move the numbers this
simulator reports — mana, cards, board presence, and the damage clock.
A rules-complete engine is explicitly *not* the goal; see the module
docstring in `state.py`. Effects we choose not to model (conditional
"whenever" triggers, targeted interaction, graveyard recursion) are
listed in UNMODELLED at the bottom of this file so the gap stays visible
instead of becoming folklore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from densa_deck.models import Card

# --- Number words -----------------------------------------------------------

NUM_WORDS: dict[str, int] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# "Draw X cards" / "Add X mana" are variable. Modelling them as 0 would
# understate every X-spell deck; modelling them as huge would flatter it.
# 2 is the conservative middle and is applied consistently.
VARIABLE_X_VALUE = 2

# "For each land you control, create a Treasure token" scales with the board.
# Counting it as 1 understates these cards badly; this is a deliberately
# conservative stand-in for a typical mid-game board, not a real count.
FOR_EACH_NOMINAL = 3

BASIC_LAND_WORDS = ("land", "forest", "plains", "island", "swamp", "mountain")


def _word_to_int(word: str | None, default: int = 1) -> int:
    """Convert 'two' / '2' / 'x' to an int, falling back to `default`."""
    if not word:
        return default
    word = word.strip().lower()
    if word.isdigit():
        return int(word)
    if word == "x":
        return VARIABLE_X_VALUE
    return NUM_WORDS.get(word, default)


@dataclass
class CardEffects:
    """Simulatable effects parsed from one card's oracle text.

    Grouped by *when* the simulator applies them:
      - immediate: on resolution of the spell / ETB of the permanent
      - static:    continuously, while the permanent is on the battlefield
      - recurring: once at the start of each of your turns
    """

    # --- immediate (on resolution / enters-the-battlefield) ---
    draw: int = 0
    lands_to_battlefield: int = 0
    lands_enter_tapped: bool = True
    lands_to_hand: int = 0
    ritual_mana: int = 0
    # Which colours the ritual makes, e.g. Dark Ritual -> ['B','B','B'].
    # Empty means "we know the amount but not the colours", which the
    # simulator treats as generic.
    ritual_colors: list[str] = field(default_factory=list)
    treasure_tokens: int = 0
    creature_tokens: int = 0
    creature_token_power: int = 0
    direct_damage: int = 0
    tutor_to_hand: int = 0
    extra_turns: int = 0
    scry: int = 0
    counters_added: int = 0
    # Counters spread over every creature you control, rather than one.
    counters_each: int = 0
    # Counters this permanent enters the battlefield already carrying — a
    # 4/4 that "enters with four +1/+1 counters on it" attacks as an 8/8.
    enters_with_counters: int = 0
    # Proliferate adds one to every permanent that already has a counter.
    proliferate: int = 0
    pump_power: int = 0

    # --- cost modifiers (resolved against the board when casting) ---
    # These make a spell cheaper than its printed cost, so they can't be
    # folded into a static number at parse time — a delve spell costs less
    # the fuller your graveyard is.
    delve: bool = False
    convoke: bool = False
    improvise: bool = False
    # "costs {1} less to cast for each artifact you control" ->
    # cost_less_per="artifact you control", cost_less_amount=1
    cost_less_per: str = ""
    cost_less_amount: int = 0

    # --- cast-more-spells ---
    cascade: int = 0

    # --- graveyard ---
    mill: int = 0
    reanimate: int = 0

    # --- static (while on the battlefield) ---
    mana_produced: int = 0          # 0 = "unparsed", callers fall back to 1
    extra_land_drops: int = 0
    cost_reduction: int = 0
    anthem_power: int = 0
    mana_multiplier: int = 1
    # Branching Evolution and friends: counters placed are doubled.
    counter_multiplier: int = 1
    grants_haste: bool = False

    # --- recurring (start of each of your turns) ---
    draw_per_turn: int = 0
    treasure_per_turn: int = 0
    proliferate_per_turn: int = 0
    counters_per_turn: int = 0

    # Names of the families that matched, for coverage reporting.
    matched: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True when nothing about this card is modelled by the simulator."""
        return not self.matched

    def has_immediate(self) -> bool:
        return bool(
            self.draw or self.lands_to_battlefield or self.lands_to_hand
            or self.ritual_mana or self.treasure_tokens or self.creature_tokens
            or self.direct_damage or self.tutor_to_hand or self.extra_turns
            or self.scry or self.counters_added or self.pump_power
        )

    def has_recurring(self) -> bool:
        return bool(self.draw_per_turn or self.treasure_per_turn)


# --- Clause splitting -------------------------------------------------------

# A "recurring" clause fires every turn on its own; an ETB clause fires once
# when the card resolves. Everything else on a spell resolves immediately.
# Deliberately loose about whose step it is and how the possessive is
# written — "at the beginning of each player's draw step" (Howling Mine)
# and "at the beginning of combat on your turn" are both recurring.
_RECURRING_PREFIX = re.compile(
    r"^at the beginning of .{0,40}?(?:upkeep|draw step|end step|main phase|combat)",
)
_CONDITIONAL_PREFIX = re.compile(r"^(?:whenever|each time)\b")

# Triggers the player controls, or that a goldfish always satisfies. There is
# no blocker in a goldfish, so an attacking creature's combat-damage trigger
# fires every turn; casting a spell and playing a land are our own choices.
# These are counted at a deliberately conservative once per turn.
_SELF_TRIGGER = re.compile(
    r"^whenever (?:you cast|you play|this creature deals combat damage|"
    r"a land you control enters|you draw)"
    r"|^landfall"
)


_REMINDER_TEXT_RE = re.compile(r"\([^)]*\)")


def _clauses(oracle: str) -> list[str]:
    """Split oracle text into sentence-ish clauses for trigger classification."""
    parts = re.split(r"(?<=[.;])\s+|\n+", oracle)
    return [p.strip() for p in parts if p.strip()]


def _clause_kind(clause: str) -> str:
    """Classify a clause as 'recurring', 'conditional', or 'immediate'.

    'When ... enters the battlefield' counts as immediate: for a simulator
    that resolves a card exactly once, an ETB trigger and a spell effect
    are the same event.
    """
    if _RECURRING_PREFIX.match(clause):
        return "recurring"
    if _SELF_TRIGGER.match(clause):
        return "self_trigger"
    if _CONDITIONAL_PREFIX.match(clause):
        return "conditional"
    return "immediate"


# --- Individual effect families --------------------------------------------

_DRAW_RE = re.compile(r"draws? (a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|\d+) cards?")
# "draws an additional card" (Howling Mine) and bare "draw a card" variants
# that the counted form above doesn't reach.
_DRAW_ADDITIONAL_RE = re.compile(r"draws? an additional card")

# Impulse-style card selection: look at the top N, take one. The card ends up
# in hand exactly like a draw, so the simulator counts it as one — the
# selection quality is what separates them, and we don't model that.
_LOOK_TOP_RE = re.compile(
    r"look at the top (a|an|one|two|three|four|five|six|seven|\d+) cards? of your library"
    r".{0,140}?put (?:one|that card|it)[^.]{0,60}?into your hand",
    re.DOTALL,
)
# Exile-and-play (Light Up the Stage, Wrenn's Resolve): the cards become
# castable this turn or next, which is closer to a draw than to nothing.
_EXILE_PLAY_RE = re.compile(
    r"exiles? the top (a|an|one|two|three|four|five|\d+) cards? of your library"
    r".{0,180}?you may play",
    re.DOTALL,
)


def _parse_draw(clause: str) -> int:
    """Cards drawn by this clause. 'Draw a card' => 1, 'draw three cards' => 3."""
    total = sum(_word_to_int(m.group(1)) for m in _DRAW_RE.finditer(clause))
    total += len(_DRAW_ADDITIONAL_RE.findall(clause))
    return total


def _parse_selection_draw(oracle: str) -> int:
    """Impulse-style card selection, scanned across the whole oracle text.

    These span a sentence break — "Look at the top four cards of your
    library. Put one of them into your hand." — so they can't be matched
    per-clause. Collectively ~1000 cards in the Oracle, the single largest
    family the simulator used to treat as blanks.
    """
    total = len(_LOOK_TOP_RE.findall(oracle))
    for m in _EXILE_PLAY_RE.finditer(oracle):
        total += _word_to_int(m.group(1))
    return total


_LAND_SEARCH_RE = re.compile(
    r"search your library for (?:up to )?"
    r"(a|an|one|two|three|four|\d+)?\s*"
    r"(?:basic )?(?:[a-z]+ )?(land|forest|plains|island|swamp|mountain)"
)


def _parse_land_ramp(clause: str, effects: CardEffects) -> bool:
    """Land-fetch ramp: Rampant Growth, Cultivate, Nature's Lore, Skyshroud Claim."""
    if "search your library" not in clause or "onto the battlefield" not in clause:
        return False
    m = _LAND_SEARCH_RE.search(clause)
    if not m:
        return False

    count = _word_to_int(m.group(1))
    # Cultivate / Kodama's Reach: one land hits play, the rest go to hand.
    if "into your hand" in clause and count > 1:
        effects.lands_to_battlefield += 1
        effects.lands_to_hand += count - 1
    else:
        effects.lands_to_battlefield += count

    # Nature's Lore and Skyshroud Claim put lands in untapped; most basic-land
    # ramp puts them in tapped. Read it off the text rather than assuming.
    if "onto the battlefield tapped" not in clause:
        effects.lands_enter_tapped = False
    return True


_TREASURE_RE = re.compile(
    r"creates? (a|an|one|two|three|four|five|\d+)? ?treasure tokens?"
)


def _parse_treasure(clause: str) -> int:
    total = sum(_word_to_int(m.group(1)) for m in _TREASURE_RE.finditer(clause))
    if total and "for each" in clause:
        total *= FOR_EACH_NOMINAL
    return total


_CREATURE_TOKEN_RE = re.compile(
    r"creates? (a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|\d+)? ?"
    r"(\d+|x)/(\d+|x)[^.]{0,60}?creature tokens?"
)


def _parse_creature_tokens(clause: str) -> tuple[int, int]:
    """(token count, power each) summed across the clause."""
    count = 0
    power = 0
    for m in _CREATURE_TOKEN_RE.finditer(clause):
        n = _word_to_int(m.group(1))
        p = _word_to_int(m.group(2), default=1)
        count += n
        power = max(power, p)
    if count and "for each" in clause:
        count *= FOR_EACH_NOMINAL
    return count, power


_MANA_SYMBOL_RE = re.compile(r"\{[wubrgc\d]\}")
_ADD_CLAUSE_RE = re.compile(r"add ([^.;]*)")
_ADD_WORDS_RE = re.compile(r"add (one|two|three|four|five|\d+) mana")


def _count_added_mana(clause: str) -> int:
    """How much mana an 'add ...' clause produces."""
    total = 0
    m = _ADD_WORDS_RE.search(clause)
    if m:
        total = _word_to_int(m.group(1))
    else:
        add = _ADD_CLAUSE_RE.search(clause)
        if add:
            symbols = _MANA_SYMBOL_RE.findall(add.group(1))
            total = len(symbols)
    return total


_COLOR_SYMBOL_RE = re.compile(r"\{([wubrgc])\}")


def _added_mana_colors(clause: str) -> list[str]:
    """Colours named in an 'add ...' clause, e.g. 'Add {B}{B}{B}' -> BBB.

    Returns an empty list for colour-agnostic wordings like "add one mana of
    any colour" — the caller treats that as flexible rather than colourless.
    """
    add = _ADD_CLAUSE_RE.search(clause)
    if not add:
        return []
    return [c.upper() for c in _COLOR_SYMBOL_RE.findall(add.group(1))]


_TAP_FOR_MANA_RE = re.compile(r"\{t\}[^:]{0,40}:\s*add ([^.;]*)")


def _parse_mana_production(oracle: str) -> int:
    """Mana produced by a permanent's '{T}: Add ...' ability.

    Sol Ring's '{T}: Add {C}{C}' is 2, not the flat 1 the simulator
    previously assumed for every source.
    """
    best = 0
    for m in _TAP_FOR_MANA_RE.finditer(oracle):
        best = max(best, _count_added_mana("add " + m.group(1)))
    return best


_EXTRA_LAND_RE = re.compile(
    r"(?:you may )?play (an additional land|(?:up to )?(one|two|three|\d+) additional lands)"
)


def _parse_extra_land_drops(oracle: str) -> int:
    m = _EXTRA_LAND_RE.search(oracle)
    if not m:
        return 0
    if m.group(1) and m.group(1).startswith("an additional"):
        return 1
    return _word_to_int(m.group(2))


_COST_REDUCTION_RE = re.compile(r"cost \{(\d+)\} less to cast")
_ANTHEM_RE = re.compile(r"creatures you control get \+(\d+)/\+(\d+)")
_DIRECT_DAMAGE_RE = re.compile(
    r"deals? (\d+|x) damage to (?:target player|target opponent|any target|"
    r"each opponent|target player or planeswalker)"
)
_EXTRA_TURN_RE = re.compile(r"takes? an extra turn")
_SCRY_RE = re.compile(r"(?:scry|surveil) (\d+)")
_COUNTER_RE = re.compile(
    r"puts? (a|an|one|two|three|four|five|\d+) \+1/\+1 counters? on"
)
# Board-wide counter placement — one on every creature, not one total.
_COUNTER_EACH_RE = re.compile(
    r"puts? (a|an|one|two|three|\d+) \+1/\+1 counters? on each creature you control"
)
_COUNTER_DISTRIBUTE_RE = re.compile(
    r"distributes? (a|an|one|two|three|four|five|\d+) \+1/\+1 counters?"
)
_ENTERS_WITH_COUNTERS_RE = re.compile(
    r"enters with (a|an|one|two|three|four|five|six|seven|x|\d+) \+1/\+1 counters?"
)
_PROLIFERATE_RE = re.compile(r"\bproliferate\b")

# Keyword costs. These appear as a bare keyword plus reminder text, and the
# reminder text is stripped before we get here, so a word match is enough.
_DELVE_RE = re.compile(r"\bdelve\b")
_CONVOKE_RE = re.compile(r"\bconvoke\b")
_IMPROVISE_RE = re.compile(r"\bimprovise\b")
_AFFINITY_RE = re.compile(r"affinity for ([a-z ]+?)(?:\.|$|\n)")
_COST_LESS_PER_RE = re.compile(
    r"costs? \{(\d+)\} less to cast for each ([^.\n]{0,60})"
)
_CASCADE_RE = re.compile(r"\bcascade\b")
# "Mill three cards" — self-mill. "Target player mills" is someone else's
# library and does nothing for us, so it's excluded.
_MILL_RE = re.compile(r"(?<!target player )\bmills? (a|an|one|two|three|four|five|six|seven|eight|ten|x|\d+) cards?")
# Reanimation is templated two ways — "return ... to the battlefield" and
# "put ... onto the battlefield" (Reanimate itself uses the latter).
_REANIMATE_RE = re.compile(
    r"(?:returns?|puts?) (?:target |a |an |one |two |\d+ )?"
    r"(?:creature|permanent) cards? from (?:your|a|their) graveyard "
    r"(?:to|onto) the battlefield"
)
_COUNTER_DOUBLER_RE = re.compile(
    r"(?:twice that many|double the number of)[^.]{0,40}counter"
)
_PUMP_RE = re.compile(r"gets? \+(\d+)/\+\d+ until end of turn")
_HASTE_RE = re.compile(r"creatures you control (?:have|gain) haste")
_MANA_DOUBLER_RE = re.compile(
    r"(?:whenever you tap a[^.]{0,30}for mana, add an additional|"
    r"triggers? an additional time|produces? twice|"
    r"add an additional \{[wubrgc]\}|it produces (?:twice|three times) as much)"
)
_TUTOR_TO_HAND_RE = re.compile(r"search your library for a[^.]{0,60}card[^.]{0,60}into your hand")


# --- Public API -------------------------------------------------------------

_CACHE: dict[str, CardEffects] = {}


def parse_effects(card: Card) -> CardEffects:
    """Parse a card's oracle text into simulatable effects.

    Results are cached by card name — oracle text is stable per name, and
    a 1000-game batch would otherwise re-parse the same 100 cards 1000x.
    """
    cached = _CACHE.get(card.name)
    if cached is not None:
        return cached

    effects = _parse_effects_uncached(card)
    _CACHE[card.name] = effects
    return effects


def _parse_effects_uncached(card: Card) -> CardEffects:
    oracle = (card.oracle_text or "").lower()
    # Double-faced cards: the front face is what gets cast, but Scryfall
    # leaves oracle_text empty on some layouts, so fall back to the faces.
    if not oracle and card.faces:
        oracle = (card.faces[0].oracle_text or "").lower()

    # Reminder text restates rules in parentheses and is a rich source of
    # false matches — a Treasure token's reminder text contains "add one
    # mana", which would otherwise read as a mana ability on the spell.
    oracle = _REMINDER_TEXT_RE.sub(" ", oracle)

    effects = CardEffects()
    if not oracle:
        return effects

    matched: list[str] = []

    # --- static abilities (whole-text scan; no clause context needed) ---
    if not card.is_land:
        produced = _parse_mana_production(oracle)
        if produced:
            effects.mana_produced = produced
            if produced > 1:
                matched.append("mana_production")
    else:
        # Lands that tap for more than one mana (Ancient Tomb, bounce lands).
        produced = _parse_mana_production(oracle)
        if produced > 1:
            effects.mana_produced = produced
            matched.append("mana_production")

    extra_lands = _parse_extra_land_drops(oracle)
    if extra_lands:
        effects.extra_land_drops = extra_lands
        matched.append("extra_land_drops")

    m = _COST_REDUCTION_RE.search(oracle)
    if m:
        effects.cost_reduction = int(m.group(1))
        matched.append("cost_reduction")

    m = _ANTHEM_RE.search(oracle)
    if m:
        effects.anthem_power = int(m.group(1))
        matched.append("anthem")

    if _HASTE_RE.search(oracle):
        effects.grants_haste = True
        matched.append("haste")

    if _MANA_DOUBLER_RE.search(oracle):
        effects.mana_multiplier = 2
        matched.append("mana_multiplier")

    # Keyword and scaling costs — static properties of the card, applied
    # against the board at cast time by heuristics.effective_mana_cost.
    if _DELVE_RE.search(oracle):
        effects.delve = True
        matched.append("delve")
    if _CONVOKE_RE.search(oracle):
        effects.convoke = True
        matched.append("convoke")
    if _IMPROVISE_RE.search(oracle):
        effects.improvise = True
        matched.append("improvise")

    m_aff = _AFFINITY_RE.search(oracle)
    if m_aff:
        effects.cost_less_per = m_aff.group(1).strip()
        effects.cost_less_amount = 1
        matched.append("affinity")
    else:
        m_less = _COST_LESS_PER_RE.search(oracle)
        if m_less:
            effects.cost_less_amount = int(m_less.group(1))
            effects.cost_less_per = m_less.group(2).strip()
            matched.append("cost_less_per")

    if _CASCADE_RE.search(oracle):
        effects.cascade += 1
        matched.append("cascade")

    m_enters = _ENTERS_WITH_COUNTERS_RE.search(oracle)
    if m_enters:
        effects.enters_with_counters = _word_to_int(m_enters.group(1))
        matched.append("enters_with_counters")

    if _COUNTER_DOUBLER_RE.search(oracle):
        effects.counter_multiplier = 2
        matched.append("counter_multiplier")

    if _TUTOR_TO_HAND_RE.search(oracle):
        effects.tutor_to_hand = 1
        matched.append("tutor")

    selection = _parse_selection_draw(oracle)
    if selection:
        effects.draw += selection
        matched.append("selection_draw")

    # --- clause-scoped effects, split by when they fire ---
    for clause in _clauses(oracle):
        kind = _clause_kind(clause)

        drawn = _parse_draw(clause)
        if drawn:
            if kind == "recurring":
                effects.draw_per_turn += drawn
                matched.append("recurring_draw")
            elif kind == "immediate":
                effects.draw += drawn
                matched.append("draw")
            # conditional ("whenever ...") draws are deliberately unmodelled

        # Self-triggered board growth (proliferate on cast, counters on
        # combat damage, landfall) is modelled at once per turn. Draw and
        # token payoffs on the same triggers are deliberately left out: a
        # wrong rate there compounds through the whole game, while a wrong
        # counter rate only nudges the clock.
        if kind == "self_trigger":
            if _PROLIFERATE_RE.search(clause):
                effects.proliferate_per_turn += 1
                matched.append("proliferate_per_turn")
            m_self = _COUNTER_RE.search(clause) or _COUNTER_EACH_RE.search(clause)
            if m_self:
                effects.counters_per_turn += _word_to_int(m_self.group(1))
                matched.append("counters_per_turn")

        if kind not in ("conditional", "self_trigger"):
            if _parse_land_ramp(clause, effects):
                matched.append("land_ramp")

            treasures = _parse_treasure(clause)
            if treasures:
                if kind == "recurring":
                    effects.treasure_per_turn += treasures
                    matched.append("recurring_treasure")
                else:
                    effects.treasure_tokens += treasures
                    matched.append("treasure")

            tokens, power = _parse_creature_tokens(clause)
            if tokens:
                effects.creature_tokens += tokens
                effects.creature_token_power = max(effects.creature_token_power, power)
                matched.append("creature_tokens")

            dmg = _DIRECT_DAMAGE_RE.search(clause)
            if dmg:
                effects.direct_damage += _word_to_int(dmg.group(1))
                matched.append("direct_damage")

            if _EXTRA_TURN_RE.search(clause):
                effects.extra_turns += 1
                matched.append("extra_turn")

            m_scry = _SCRY_RE.search(clause)
            if m_scry:
                effects.scry += int(m_scry.group(1))
                matched.append("scry")

            # "on each creature you control" is checked first — the generic
            # pattern also matches it, but spreading N counters across the
            # board is a much bigger effect than putting N on one creature.
            m_each = _COUNTER_EACH_RE.search(clause)
            if m_each:
                effects.counters_each += _word_to_int(m_each.group(1))
                matched.append("counters_each")
            else:
                m_ctr = _COUNTER_RE.search(clause) or _COUNTER_DISTRIBUTE_RE.search(clause)
                if m_ctr:
                    effects.counters_added += _word_to_int(m_ctr.group(1))
                    matched.append("counters")

            if _PROLIFERATE_RE.search(clause):
                effects.proliferate += 1
                matched.append("proliferate")

            m_pump = _PUMP_RE.search(clause)
            if m_pump:
                effects.pump_power = max(effects.pump_power, int(m_pump.group(1)))
                matched.append("pump")

            m_mill = _MILL_RE.search(clause)
            if m_mill:
                effects.mill += _word_to_int(m_mill.group(1))
                matched.append("mill")

            if _REANIMATE_RE.search(clause):
                effects.reanimate += 1
                matched.append("reanimate")

    # Rituals: a one-shot 'Add {B}{B}{B}' on an instant or sorcery. Permanents
    # with tap abilities are handled by mana_produced above, so exclude them.
    if card.is_instant or card.is_sorcery:
        if "add " in oracle and "{t}" not in oracle:
            ritual = _count_added_mana(oracle)
            if ritual:
                effects.ritual_mana = ritual
                effects.ritual_colors = _added_mana_colors(oracle)
                matched.append("ritual")

    effects.matched = sorted(set(matched))
    return effects


def clear_cache() -> None:
    """Drop the parse cache. Tests that synthesise cards reusing a name
    across cases must call this to avoid cross-test contamination."""
    _CACHE.clear()


# Effect families the simulator deliberately does not model, kept explicit so
# the limits stay auditable rather than becoming folklore. Each of these is a
# real gap; none of them silently pretends to work.
@dataclass(frozen=True)
class EffectFamily:
    """One declared, modelled card behaviour.

    The registry below is the contract: it is what `densa-deck coverage`
    reports, what `docs/SIMULATION_COVERAGE.md` documents, and what a
    contributor reads before adding a new mechanic. Adding a family means
    adding an entry here, so coverage can never quietly drift away from
    what the parser actually does.
    """

    key: str            # the name that appears in CardEffects.matched
    field: str          # the CardEffects field it populates
    phase: str          # immediate | static | recurring | cost | cast
    summary: str        # what the simulator does with it
    example: str = ""   # a card that exercises it


# Order is presentation order: mana first, then cards, board, and costs.
EFFECT_FAMILIES: tuple[EffectFamily, ...] = (
    # --- mana ---
    EffectFamily("mana_production", "mana_produced", "static",
                 "Sources tap for the amount their text states, not a flat 1.",
                 "Sol Ring"),
    EffectFamily("land_ramp", "lands_to_battlefield / lands_to_hand", "immediate",
                 "Pulls lands out of the library, tapped or untapped as printed.",
                 "Cultivate"),
    EffectFamily("ritual", "ritual_mana / ritual_colors", "immediate",
                 "One-shot mana in the colours the card names.", "Dark Ritual"),
    EffectFamily("treasure", "treasure_tokens", "immediate",
                 "Treasures become one-shot any-colour mana.", "Brass's Bounty"),
    EffectFamily("recurring_treasure", "treasure_per_turn", "recurring",
                 "Treasures produced at the start of each of your turns.",
                 "Smothering Tithe"),
    EffectFamily("mana_multiplier", "mana_multiplier", "static",
                 "Doubles mana from permanents; best multiplier wins, no stacking.",
                 "Mana Reflection"),
    EffectFamily("extra_land_drops", "extra_land_drops", "static",
                 "Additional land drops per turn.", "Azusa, Lost but Seeking"),

    # --- cards ---
    EffectFamily("draw", "draw", "immediate",
                 "Cards drawn on resolution or on an enters-the-battlefield trigger.",
                 "Divination"),
    EffectFamily("selection_draw", "draw", "immediate",
                 "Impulse-style selection and exile-and-play; both put cards in hand.",
                 "Impulse"),
    EffectFamily("recurring_draw", "draw_per_turn", "recurring",
                 "Extra cards at the start of each of your turns.",
                 "Phyrexian Arena"),
    EffectFamily("tutor", "tutor_to_hand", "immediate",
                 "Searching to hand, counted as card advantage but not selection quality.",
                 "Demonic Tutor"),
    EffectFamily("scry", "scry", "immediate",
                 "Bottoms an unwanted top card based on whether we need lands.",
                 "Preordain"),
    EffectFamily("mill", "mill", "immediate",
                 "Self-mill moves library to graveyard; feeds delve and combo assembly.",
                 "Stitcher's Supplier"),

    # --- board ---
    EffectFamily("creature_tokens", "creature_tokens / creature_token_power", "immediate",
                 "Tokens tracked in aggregate and added to the damage clock.",
                 "Secure the Wastes"),
    EffectFamily("anthem", "anthem_power", "static",
                 "Team-wide power bonus applied to every attacker.", "Glorious Anthem"),
    EffectFamily("counters", "counters_added", "immediate",
                 "+1/+1 counters placed on the biggest creature.", "Bond Beetle"),
    EffectFamily("counters_each", "counters_each", "immediate",
                 "+1/+1 counters spread across every creature you control.",
                 "Ajani's Pridemate effects"),
    EffectFamily("enters_with_counters", "enters_with_counters", "immediate",
                 "Creatures that arrive already carrying counters.", "Kalonian Hydra"),
    EffectFamily("counters_per_turn", "counters_per_turn", "recurring",
                 "Counters added each turn from a trigger we control.", "Ajani, Mentor"),
    EffectFamily("proliferate", "proliferate", "immediate",
                 "Adds one counter to every permanent that already has one.",
                 "Contagion Clasp"),
    EffectFamily("proliferate_per_turn", "proliferate_per_turn", "recurring",
                 "Proliferate from a self-controlled trigger, once per turn.",
                 "Inexorable Tide"),
    EffectFamily("counter_multiplier", "counter_multiplier", "static",
                 "Doubles counters as they are placed; best doubler wins.",
                 "Branching Evolution"),
    EffectFamily("haste", "grants_haste", "static",
                 "Creatures can attack the turn they arrive.", "Fervor"),
    EffectFamily("pump", "pump_power", "immediate",
                 "Combat pump added to this turn's damage.", "Giant Growth"),
    EffectFamily("direct_damage", "direct_damage", "immediate",
                 "Damage to the opponent without combat.", "Lightning Bolt"),
    EffectFamily("reanimate", "reanimate", "immediate",
                 "Returns the biggest creature from graveyard to battlefield.",
                 "Reanimate"),
    EffectFamily("extra_turn", "extra_turns", "immediate",
                 "Grants another turn; depth-capped to keep batches terminating.",
                 "Time Warp"),

    # --- costs ---
    EffectFamily("cost_reduction", "cost_reduction", "static",
                 "Generic cost reduction from permanents; never reduces pips.",
                 "Goblin Electromancer"),
    EffectFamily("delve", "delve", "cost",
                 "Exiles graveyard cards to pay generic mana; the graveyard is spent.",
                 "Treasure Cruise"),
    EffectFamily("convoke", "convoke", "cost",
                 "Taps creatures to pay generic mana; those creatures can't attack.",
                 "Chord of Calling"),
    EffectFamily("improvise", "improvise", "cost",
                 "Taps artifacts to pay generic mana.", "Whir of Invention"),
    EffectFamily("affinity", "cost_less_per / cost_less_amount", "cost",
                 "Cost scales down with a counted permanent type.", "Frogmite"),
    EffectFamily("cost_less_per", "cost_less_per / cost_less_amount", "cost",
                 "'Costs {N} less for each X' resolved against the board.",
                 "Thoughtcast"),

    # --- casting more spells ---
    EffectFamily("cascade", "cascade", "cast",
                 "Casts a cheaper nonland card free from the top of the library.",
                 "Bloodbraid Elf"),
)

EFFECT_FAMILIES_BY_KEY = {f.key: f for f in EFFECT_FAMILIES}

UNMODELLED = (
    "opponent-dependent 'whenever' triggers (Rhystic Study, death triggers) — "
    "self-controlled triggers ARE modelled, at once per turn",
    "draw and token payoffs on self-controlled triggers: the per-turn rate "
    "assumption is safe for counters but compounds badly for card flow",
    "targeted interaction (removal, counterspells) — no opponent board in goldfish",
    "combat keywords (evasion, trample, deathtouch) — a goldfish has no blockers, "
    "so they change nothing here",
    "mass symmetric reanimation (Living Death) — sacrifices your board first, "
    "so modelling it as pure upside would flatter the deck",
    "extra combat phases",
    "wheel effects (discard hand, redraw)",
    "free casts outside cascade ('you may cast this without paying its mana cost')",
    "kicker and other optional additional costs",
    "storm and spell-copying",
    "fetchland shuffle and deck-thinning — the land and its colours are right, "
    "the library manipulation is not",
    "land ETB triggers generally: play_land does not resolve effects",
)
