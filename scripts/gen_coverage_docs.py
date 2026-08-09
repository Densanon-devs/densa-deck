"""Generate docs/SIMULATION_COVERAGE.md from the declared effect registry.

The registry in `densa_deck.goldfish.effects.EFFECT_FAMILIES` is the source
of truth for what the simulator models. This script renders it to Markdown
so the documentation cannot drift from the code — and `tests/test_effects.py`
asserts that every family the parser emits is declared, so the registry
cannot drift from the parser either.

Usage:  python scripts/gen_coverage_docs.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from densa_deck.formats.profiles import FORMAT_PROFILES, starting_life_for  # noqa: E402
from densa_deck.goldfish.effects import EFFECT_FAMILIES, UNMODELLED  # noqa: E402
from densa_deck.models import Format  # noqa: E402

PHASES = [
    ("immediate", "On resolution",
     "Applied the moment the card resolves, including enters-the-battlefield "
     "triggers — for a simulator that resolves a card once, an ETB and a spell "
     "effect are the same event."),
    ("static", "While on the battlefield",
     "Continuous effects read off every permanent in play."),
    ("recurring", "Each of your turns",
     "Applied once at the start of your turn."),
    ("cost", "Cost modifiers",
     "Resolved against the board at cast time. Each spends the resource it "
     "used — convoke taps the creatures, delve exiles the graveyard — so none "
     "of them is free."),
    ("cast", "Casts more spells",
     "Puts additional spells onto the battlefield without paying for them."),
]


def build() -> str:
    out: list[str] = []
    w = out.append

    w("# Simulation coverage")
    w("")
    w("What the goldfish simulator models, what it deliberately does not, and how")
    w("to add a new mechanic on purpose rather than by accident.")
    w("")
    w("**This file is generated.** The source of truth is `EFFECT_FAMILIES` in")
    w("`src/densa_deck/goldfish/effects.py`. Regenerate with")
    w("`python scripts/gen_coverage_docs.py`. A test asserts that every family the")
    w("parser emits is declared there, so this document cannot silently fall behind.")
    w("")
    w("Run `densa-deck coverage` against your own card database for live counts,")
    w("including the share of cards the simulator still treats as blanks.")
    w("")
    w("## What the simulator is")
    w("")
    w("A model of mana, cards, board presence and the damage clock — not a rules")
    w("engine. There is no stack, no priority and no opponent board. The goal is to")
    w("measure how a deck *functions*, so effects are modelled when they move those")
    w("four numbers and skipped when they don't.")
    w("")
    w(f"## Modelled effects ({len(EFFECT_FAMILIES)} families)")
    w("")

    for phase, title, blurb in PHASES:
        members = [f for f in EFFECT_FAMILIES if f.phase == phase]
        if not members:
            continue
        w(f"### {title}")
        w("")
        w(blurb)
        w("")
        w("| Family | What the simulator does | Example |")
        w("|--------|-------------------------|---------|")
        for f in members:
            w(f"| `{f.key}` | {f.summary} | {f.example or '—'} |")
        w("")

    w("## Deliberately not modelled")
    w("")
    w("Each of these is a real gap, kept explicit so the limits stay auditable")
    w("rather than becoming folklore.")
    w("")
    for line in UNMODELLED:
        w(f"- {line}")
    w("")

    w("## Format coverage")
    w("")
    w("Card legality, banned and restricted status come from Scryfall's per-format")
    w("`legalities` data and refresh with every `densa-deck ingest`. The validator")
    w("flags banned, not-legal and restricted cards for the deck's format.")
    w("")
    w("| Format | Analysis profile | Deck size | Singleton | Starting life |")
    w("|--------|------------------|-----------|-----------|---------------|")
    for fmt in Format:
        profile = FORMAT_PROFILES.get(fmt)
        if profile:
            t = profile.targets
            singleton = "yes" if t.singleton else f"no (max {t.max_copies})"
            w(f"| {profile.display_name} | full | {t.min_deck_size} | "
              f"{singleton} | {t.starting_life} |")
        else:
            w(f"| {fmt.value.title()} | legality only | — | — | "
              f"{starting_life_for(fmt)} |")
    w("")
    w('"Full" profiles add tuned targets (lands, ramp, draw, removal, curve) and')
    w('archetype detection. "Legality only" formats still validate against the')
    w("banned list and simulate at the correct life total, but have no tuned")
    w("deckbuilding targets.")
    w("")
    w("**Rulings are not ingested.** Scryfall publishes per-card rulings as a")
    w("separate bulk file; the engine does not download or use it. Nothing in the")
    w("simulator or validator depends on rulings text.")
    w("")

    w("## Adding a new mechanic")
    w("")
    w("1. Add a field to `CardEffects` for what the mechanic produces.")
    w("2. Parse it in `_parse_effects_uncached`, appending your family key to `matched`.")
    w("3. Declare it in `EFFECT_FAMILIES` — key, field, phase, summary, example.")
    w("4. Apply it in `goldfish/state.py` at the phase you declared.")
    w("5. Add tests: one for the parse, one for the in-game effect.")
    w("6. Regenerate this file.")
    w("")
    w("If a mechanic depends on opponent behaviour, or its per-turn rate is a guess")
    w("that compounds, prefer adding it to `UNMODELLED` with a reason over shipping")
    w("a number that looks precise and isn't.")

    return "\n".join(out) + "\n"


def main() -> None:
    target = ROOT / "docs" / "SIMULATION_COVERAGE.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text(build(), encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
