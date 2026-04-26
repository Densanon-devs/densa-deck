# Densa Deck

Deck analysis, MTGA-style deckbuilder, goldfish testing, matchup simulation,
combo detection, and AI coaching for Magic: The Gathering. Local-first
desktop app — your decks and analyses never leave your machine.

## What It Does

### Core analysis (free)
- **Card database** — Pulls the full MTG card database from Scryfall
- **Deck import** — Parses decklists from plain text, Moxfield (via Export →
  Text), Archidekt URLs, or CSV
- **Card classification** — Auto-tags every card by functional role (ramp,
  removal, draw, threats, etc.)
- **Static analysis** — Mana curve, color sources, role distribution,
  structural scoring, actionable recommendations
- **Format validation** — Legality checks, copy limits, color identity, commander rules
- **Combo detection** — Local Commander Spellbook integration (~30k variants).
  Detects complete combo lines + 1-card-away near-misses
- **Bracket fit** — 1-precon ... 5-cedh framework with verdict + over/under
  signals + punch-list of recommendations
- **Rule 0 worksheet** — pre-game discussion sheet (paste into Discord)
- **MTGA-style Build tab** — three-column search/deck/stats with autosave
- **Multi-format export** — MTGA paste / MTGO `.dek` / Moxfield text

### Pro tier
- **Goldfish simulation** — Solo testing with combo-aware win conditions
- **Matchup gauntlet** — vs 11 archetype profiles, combo wins counted
- **Deck-vs-deck duel + analyst compare** — head-to-head with prose
- **Version history + diff** — combo gained / combo lost between versions
- **AI coach** — local LLM (Qwen 2.5 3B GGUF) with combo-aware deck sheet
- **Explain a card** — narrate why a card is flagged (or pinned as combo piece)
- **AI deckbuild suggestions** — role-gap + combo-completion blended ranking
- **Report export** — Markdown / HTML with combo sections

## Installation

**Option 1: Desktop binary (recommended)** — Download from the
[releases page](https://github.com/densanon-devs/densa-deck/releases).
Windows ZIP, ~55 MB. Extract and run `densa-deck.exe`.

**Option 2: From source**
```bash
pip install -e .
pip install pywebview llama-cpp-python  # optional, for desktop UI + analyst
```

Activate Pro:
```bash
densa-deck license activate DD-XXXX-XXXX-XXXX
```

## Quick Start

```bash
# One-time card data download (~250 MB Scryfall bulk)
densa-deck ingest

# One-time combo data download (~30k Commander Spellbook variants)
densa-deck combos refresh

# Static analysis
densa-deck analyze my_deck.txt --format commander

# Combo detection on a deck
densa-deck combos detect my_deck.txt

# 1-card-away combos (high-leverage adds to consider)
densa-deck combos near-miss my_deck.txt

# One-shot density summary (detected + near-miss + bracket implications)
densa-deck combos density my_deck.txt

# Bracket-fit assessment (verdict + recommendations)
densa-deck bracket my_deck.txt --target 3-optimized

# Pre-game Rule 0 worksheet
densa-deck rule0 my_deck.txt

# Multi-format export
densa-deck export my_deck.txt --target mtga
densa-deck export my_deck.txt --target mtgo --out my_deck.dek
densa-deck export my_deck.txt --target moxfield

# Pro: goldfish simulation (combo-aware)
densa-deck goldfish my_deck.txt --format commander --sims 1000

# Pro: matchup gauntlet (combo-aware)
densa-deck gauntlet my_deck.txt --format commander --sims 200

# Pro: explain a single card
densa-deck explain my_deck.txt "Cryptic Command"

# Pro: compare two saved decks via the analyst
densa-deck compare-decks deck_a deck_b

# Save a version snapshot
densa-deck save my_deck.txt my-deck-id --notes "Added more ramp"

# Diff two versions
densa-deck compare my-deck-id

# Launch the desktop app
densa-deck app
```

## Decklist Format

Plain text, one card per line:

```
Commander
1 Atraxa, Praetors' Voice

Mainboard
1 Sol Ring
1 Arcane Signet
1 Command Tower
35 Plains
```

Also supports `4x Lightning Bolt`, Moxfield Export → Text, Archidekt
URL imports, and CSV.

## Free vs Pro

All card data, deck import, static analysis, combo detection, bracket fit,
multi-format export, and the Rule 0 worksheet are **free forever**.
Monetization is feature-gated, not data-gated.

| Feature | Free | Pro |
|---------|:----:|:---:|
| Card search & deck import | Y | Y |
| Static analysis & mana curve | Y | Y |
| Combo detection (Commander Spellbook) | Y | Y |
| 1-card-away combo finder | Y | Y |
| Bracket fit + Rule 0 worksheet | Y | Y |
| MTGA-style deckbuilder (Build tab) | Y | Y |
| Multi-format export (MTGA/MTGO/Moxfield) | Y | Y |
| Save deck versions + history | - | Y |
| Goldfish simulation (combo-aware) | - | Y |
| Matchup gauntlet (combo-aware) | - | Y |
| Deck-vs-deck duel + analyst compare | - | Y |
| Explain a card (analyst LLM) | - | Y |
| AI deckbuild suggestions | - | Y |
| Report export (Markdown / HTML) | - | Y |
| Local AI coach REPL | - | Y |

## Roadmap

- [x] Phase 1: Card data, deck import, classification, static analysis
- [x] Phase 2: Opening hand / mana probability calculator
- [x] Phase 3: Goldfish simulation engine (Pro)
- [x] Phase 4: Matchup framework and benchmark gauntlet (Pro)
- [x] Phase 5: Version comparison and change tracking (Pro)
- [x] Phase 6: Advanced heuristics, format modules, analyst LLM (Pro)
- [x] **Phase 7: Combo integration** — Commander Spellbook MIT
  integration; combo-aware goldfish / gauntlet / mulligan / power /
  archetype / brackets / cuts / adds / coach / exports / version diff
- [ ] Phase 8 (next): natural-language deck build, semantic card search,
  EDHTop16 popularity layer (subject to maintainer email confirmation)

## Legal

This tool is not affiliated with or endorsed by Wizards of the Coast.
Magic: The Gathering and its logos are trademarks of Wizards of the Coast LLC.
Card data provided by [Scryfall](https://scryfall.com). Card images are
hotlinked from Scryfall and are never hosted by this project.

Combo data via [Commander Spellbook](https://commanderspellbook.com)
(MIT, © 2023 Commander-Spellbook). Densa Deck does NOT use EDHREC's
data — their ToS forbids commercial integration.
