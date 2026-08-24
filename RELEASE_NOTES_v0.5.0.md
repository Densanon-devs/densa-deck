# Densa Deck v0.5.0

Three product-uplift features added — all free-tier, all local, all rule-engine (no LLM cost, no network dependencies beyond the existing Scryfall/Commander Spellbook fetches).

## What's new

### Budget-aware suggestions + TCGPlayer hooks
- **`analyze --budget USD`** — set a per-card price ceiling. The analyze output now includes a deck-value summary (total, priciest 3, unpriced count) and flags every card above the cap with its current Scryfall price.
- **Build tab** — Suggest modal now has a budget input + a live deck-value badge + Refresh button. Every suggestion row shows price and a direct TCGPlayer search link.
- **Affiliate-ready** — set the `DENSA_TCGPLAYER_PARTNER` env var to inject your partner ID into every TCG URL; without it, links still work.

### Playgroup-aware analyst tuning
- **New CLI:** `densa-deck playgroup add/list/remove/delete/set-default` — store the commanders you regularly play against, their archetypes, and rough power levels. Lives at `~/.densa-deck/playgroup.db`, never leaves the machine.
- **Pod context derived automatically** — average power, archetype mix, and threat themes (graveyard hate, counterspells, board wipes, etc.) are computed from your pod members and fed to the analyst.
- **`analyze --playgroup <name>`** — the executive summary now narrates against your *actual* table rather than a generic one. Pod's derived avg_power outranks the single-number `--playgroup-power` flag when both are set; falls back gracefully when not.
- **Free tier** — pod data is yours, never sent anywhere.

### Iteration loop — close the build-feedback gap
- **`densa-deck iterate propose <deck>`** — surface concrete cut + add proposals from the existing analyst rankers, with combo-completion bias.
- **`densa-deck iterate preview <deck> <kind> <card>`** — apply a single change in-memory and show before/after deltas (power, avg CMC, total cards, role counts, value). No goldfish, no LLM — fast enough to chain.
- **`densa-deck iterate history <deck_id>`** — see every accept/reject decision with timestamps + net power delta across your iteration history.
- **Build tab gets an Iterate button** — modal lists proposals with inline Preview / Accept / Reject. Accept mutates the draft in place + writes to the iteration log; Reject just logs the decision so the same proposal doesn't keep resurfacing.

## Under the hood

- 3 new packages: `densa_deck.analysis.pricing`, `densa_deck.playgroup`, `densa_deck.iteration`
- 8 new playgroup API endpoints + 4 new iteration endpoints + 2 new pricing endpoints
- Tests: **474 → 582 (+108)**, all green in 23s
- Tier strategy unchanged: every new feature is FREE. Existing Pro gates on `--with-llm` paths untouched.

## Compatibility

- v0.4.x license keys continue to work — no licensing changes.
- v0.4.x saved decks, combo cache, and analyst model continue to work — no schema changes outside the new playgroup + iteration tables (auto-created on first use).
- No new required dependencies.
