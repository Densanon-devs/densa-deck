# Simulation coverage

What the goldfish simulator models, what it deliberately does not, and how
to add a new mechanic on purpose rather than by accident.

**This file is generated.** The source of truth is `EFFECT_FAMILIES` in
`src/densa_deck/goldfish/effects.py`. Regenerate with
`python scripts/gen_coverage_docs.py`. A test asserts that every family the
parser emits is declared there, so this document cannot silently fall behind.

Run `densa-deck coverage` against your own card database for live counts,
including the share of cards the simulator still treats as blanks.

## What the simulator is

A model of mana, cards, board presence and the damage clock — not a rules
engine. There is no stack, no priority and no opponent board. The goal is to
measure how a deck *functions*, so effects are modelled when they move those
four numbers and skipped when they don't.

## Modelled effects (34 families)

### On resolution

Applied the moment the card resolves, including enters-the-battlefield triggers — for a simulator that resolves a card once, an ETB and a spell effect are the same event.

| Family | What the simulator does | Example |
|--------|-------------------------|---------|
| `land_ramp` | Pulls lands out of the library, tapped or untapped as printed. | Cultivate |
| `ritual` | One-shot mana in the colours the card names. | Dark Ritual |
| `treasure` | Treasures become one-shot any-colour mana. | Brass's Bounty |
| `draw` | Cards drawn on resolution or on an enters-the-battlefield trigger. | Divination |
| `selection_draw` | Impulse-style selection and exile-and-play; both put cards in hand. | Impulse |
| `tutor` | Searching to hand, counted as card advantage but not selection quality. | Demonic Tutor |
| `scry` | Bottoms an unwanted top card based on whether we need lands. | Preordain |
| `mill` | Self-mill moves library to graveyard; feeds delve and combo assembly. | Stitcher's Supplier |
| `creature_tokens` | Tokens tracked in aggregate and added to the damage clock. | Secure the Wastes |
| `counters` | +1/+1 counters placed on the biggest creature. | Bond Beetle |
| `counters_each` | +1/+1 counters spread across every creature you control. | Ajani's Pridemate effects |
| `enters_with_counters` | Creatures that arrive already carrying counters. | Kalonian Hydra |
| `proliferate` | Adds one counter to every permanent that already has one. | Contagion Clasp |
| `pump` | Combat pump added to this turn's damage. | Giant Growth |
| `direct_damage` | Damage to the opponent without combat. | Lightning Bolt |
| `reanimate` | Returns the biggest creature from graveyard to battlefield. | Reanimate |
| `extra_turn` | Grants another turn; depth-capped to keep batches terminating. | Time Warp |

### While on the battlefield

Continuous effects read off every permanent in play.

| Family | What the simulator does | Example |
|--------|-------------------------|---------|
| `mana_production` | Sources tap for the amount their text states, not a flat 1. | Sol Ring |
| `mana_multiplier` | Doubles mana from permanents; best multiplier wins, no stacking. | Mana Reflection |
| `extra_land_drops` | Additional land drops per turn. | Azusa, Lost but Seeking |
| `anthem` | Team-wide power bonus applied to every attacker. | Glorious Anthem |
| `counter_multiplier` | Doubles counters as they are placed; best doubler wins. | Branching Evolution |
| `haste` | Creatures can attack the turn they arrive. | Fervor |
| `cost_reduction` | Generic cost reduction from permanents; never reduces pips. | Goblin Electromancer |

### Each of your turns

Applied once at the start of your turn.

| Family | What the simulator does | Example |
|--------|-------------------------|---------|
| `recurring_treasure` | Treasures produced at the start of each of your turns. | Smothering Tithe |
| `recurring_draw` | Extra cards at the start of each of your turns. | Phyrexian Arena |
| `counters_per_turn` | Counters added each turn from a trigger we control. | Ajani, Mentor |
| `proliferate_per_turn` | Proliferate from a self-controlled trigger, once per turn. | Inexorable Tide |

### Cost modifiers

Resolved against the board at cast time. Each spends the resource it used — convoke taps the creatures, delve exiles the graveyard — so none of them is free.

| Family | What the simulator does | Example |
|--------|-------------------------|---------|
| `delve` | Exiles graveyard cards to pay generic mana; the graveyard is spent. | Treasure Cruise |
| `convoke` | Taps creatures to pay generic mana; those creatures can't attack. | Chord of Calling |
| `improvise` | Taps artifacts to pay generic mana. | Whir of Invention |
| `affinity` | Cost scales down with a counted permanent type. | Frogmite |
| `cost_less_per` | 'Costs {N} less for each X' resolved against the board. | Thoughtcast |

### Casts more spells

Puts additional spells onto the battlefield without paying for them.

| Family | What the simulator does | Example |
|--------|-------------------------|---------|
| `cascade` | Casts a cheaper nonland card free from the top of the library. | Bloodbraid Elf |

## Deliberately not modelled

Each of these is a real gap, kept explicit so the limits stay auditable
rather than becoming folklore.

- opponent-dependent 'whenever' triggers (Rhystic Study, death triggers) — self-controlled triggers ARE modelled, at once per turn
- draw and token payoffs on self-controlled triggers: the per-turn rate assumption is safe for counters but compounds badly for card flow
- targeted interaction (removal, counterspells) — no opponent board in goldfish
- combat keywords (evasion, trample, deathtouch) — a goldfish has no blockers, so they change nothing here
- mass symmetric reanimation (Living Death) — sacrifices your board first, so modelling it as pure upside would flatter the deck
- extra combat phases
- wheel effects (discard hand, redraw)
- free casts outside cascade ('you may cast this without paying its mana cost')
- kicker and other optional additional costs
- storm and spell-copying
- fetchland shuffle and deck-thinning — the land and its colours are right, the library manipulation is not
- land ETB triggers generally: play_land does not resolve effects

## Format coverage

Card legality, banned and restricted status come from Scryfall's per-format
`legalities` data and refresh with every `densa-deck ingest`. The validator
flags banned, not-legal and restricted cards for the deck's format.

| Format | Analysis profile | Deck size | Singleton | Starting life |
|--------|------------------|-----------|-----------|---------------|
| Standard | full | 60 | no (max 4) | 20 |
| Pioneer | full | 60 | no (max 4) | 20 |
| Modern | full | 60 | no (max 4) | 20 |
| Legacy | full | 60 | no (max 4) | 20 |
| Vintage | legality only | — | — | 20 |
| Pauper | full | 60 | no (max 4) | 20 |
| Commander / EDH | full | 100 | yes | 40 |
| Brawl | full | 60 | yes | 25 |
| Historic | legality only | — | — | 20 |
| Explorer | legality only | — | — | 20 |
| Alchemy | legality only | — | — | 20 |
| Penny | legality only | — | — | 20 |
| Oathbreaker | legality only | — | — | 20 |
| Duel | legality only | — | — | 20 |
| Premodern | legality only | — | — | 20 |

"Full" profiles add tuned targets (lands, ramp, draw, removal, curve) and
archetype detection. "Legality only" formats still validate against the
banned list and simulate at the correct life total, but have no tuned
deckbuilding targets.

**Rulings are not ingested.** Scryfall publishes per-card rulings as a
separate bulk file; the engine does not download or use it. Nothing in the
simulator or validator depends on rulings text.

## Adding a new mechanic

1. Add a field to `CardEffects` for what the mechanic produces.
2. Parse it in `_parse_effects_uncached`, appending your family key to `matched`.
3. Declare it in `EFFECT_FAMILIES` — key, field, phase, summary, example.
4. Apply it in `goldfish/state.py` at the phase you declared.
5. Add tests: one for the parse, one for the in-game effect.
6. Regenerate this file.

If a mechanic depends on opponent behaviour, or its per-turn rate is a guess
that compounds, prefer adding it to `UNMODELLED` with a reason over shipping
a number that looks precise and isn't.
