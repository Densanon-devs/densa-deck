# mtg-deck-engine (Densa Deck)

Commercial MTG deck analysis platform. Free tools on `toolkit.densanon.com`,
Pro desktop app sold via Stripe one-time purchase. **Released v0.2.0** to
GitHub on 2026-04-24; **11 post-release commits queued for v0.3.0.**

## Stack

- Python 3.11+, Pydantic, httpx, Rich CLI, SQLite
- llama-cpp-python (optional, Pro analyst model)
- pywebview for the desktop UI shell
- PyInstaller for desktop binary (~107 MB folder mode after CUDA / torch / scipy
  excludes — see `densa-deck.spec`'s post-Analysis filter)

## Architecture

12 packages under `src/densa_deck/`:
- `data/` — Scryfall ingestion, SQLite card database, structured search
- `deck/` — parser (plain text, Moxfield, Archidekt, CSV), resolver, validator, URL import
- `classification/` — 30+ functional tag classifier (ramp, removal, draw, etc.)
- `analysis/` — static analysis, advanced (pip/synergy/mana base), power level,
  castability, staples, deck_diff, **brackets** (1-precon ... 5-cedh framework)
- `probability/` — hypergeometric, opening hand Monte Carlo, mana development, key card access
- `goldfish/` — game state, casting heuristics, mulligan AI (combo-aware),
  objective tests, batch runner with combo win tracking
- `matchup/` — 11 archetype profiles, deck-vs-archetype sim (combo-aware),
  benchmark gauntlet with combo aggregate
- `versioning/` — SQLite snapshots, diffs, impact analysis, trend tracking
- `formats/` — 7 format profiles with combo-aware archetype detection
- `analyst/` — LLM-backed analyst (compare/explain/Rule 0 phase 6) +
  candidates (cuts/adds w/ combo protection + bias) + coach REPL + phase6 module
- **`combos/`** — Commander Spellbook integration (MIT). ComboStore (SQLite cache),
  detect_combos / detect_near_miss_combos / diff_combos, refresh_combo_snapshot.
- `export/` — JSON/Markdown/HTML report export (combo sections + Spellbook attribution)
- `benchmarks/` — 6 built-in gauntlet suites (casual-commander, cedh, modern-meta, etc.)

Top-level: `cli.py` (~25 commands), `models.py`, `tiers.py` (free/pro gating),
`licensing.py` (hash-based key validation), `legal.py` (attribution).

## CLI Commands

**Free tier:** ingest, analyze (basic), search, info, calc, license, **combos**
(refresh/status/detect/near-miss/density), **rule0**, **bracket**, **export** (mtga/mtgo/moxfield)

**Pro tier:** analyze --deep, analyze --export, probability, goldfish, gauntlet,
save, compare, history, diff, practice, **explain**, **compare-decks**, coach,
analyst (pull/show)

Tier enforcement in `tiers.py`. Pro commands show upgrade message and exit 0 if
free tier. Tier mappings live in `tiers.COMMAND_FEATURES`.

## Combo Integration (post-v0.2.0 work)

**Commander Spellbook MIT-licensed integration.** Source:
`backend.commanderspellbook.com/variants/`. ~30k variants. Polite walker
(250ms inter-page sleep, custom UA).

Combo data lives in `~/.densa-deck/combos.db` (SQLite). Refresh via
**Settings → Refresh combo data** in the desktop UI or `densa-deck combos refresh`.

**Eleven layers of the engine are combo-aware:**
1. `analysis.power_level.estimate_power_level(deck, *, detected_combo_count, near_miss_combo_count)`
   — combo count lifts combo_potential, floors win_condition_quality
2. `analysis.brackets.bracket_fit(..., combo_lines)` — bracket-fit recommendations
   name specific combo lines to drop on over-pitch
3. `formats.profiles.detect_archetype(deck, *, detected_combo_count)` —
   2+ combos overrides to `DeckArchetype.COMBO`
4. `goldfish.runner.run_goldfish_batch(..., combos)` — combo assembly is a
   first-class win condition; report has combo_win_rate / win-turn distribution
5. `goldfish.mulligan.mulligan_phase(..., combo_card_names)` — softer keep
   floor on combo-rich hands; bottoming pins combo pieces (+200 score)
6. `matchup.simulator.simulate_matchup(..., combos)` — combo-as-win-condition
   per game; reason="combo" when combo closes before opponent
7. `matchup.gauntlet.run_gauntlet(..., combos)` — gauntlet aggregates combo
   wins across all 11 archetypes
8. `analyst.candidates.rank_cut_candidates(..., protected_card_names)` —
   combo pieces NEVER surfaced as cut candidates
9. `analyst.add_candidates.find_add_candidates(..., combo_completers)` —
   pins combo-finishing cards to top of role-gap suggestions
10. `analyst.coach.build_deck_sheet(..., combo_lines)` — `[COMBOS]` block
    in deck sheet
11. `analyst.prompts.executive_summary_prompt(..., combo_lines)` — system
    instruction requires the prose to acknowledge the combo plan

**AppApi endpoints:**
- `get_combo_status` / `combo_refresh_start` / `combo_refresh_progress` —
  cache management
- `detect_combos_for_deck` / `detect_near_miss_combos_for_deck` — detection
- `assess_bracket_fit` — bracket fit with combo-aware verdict
- `compare_decks_analyst` — analyst prose + combo_gained / combo_lost
- `explain_card_in_deck` — prepends "COMBO PIECE" flag for combo cards;
  returns `is_combo_piece`
- `build_rule0_worksheet` — pre-game disclosure sheet with combo lines
- `suggest_deckbuild_additions` — biased toward combo completers
- `save_deck_version` — returns `combos_broken` when a save breaks a combo
- `diff_deck_versions` — returns `combo_gained` / `combo_lost`

## Licensing System

Hash-based, matches D-Brief pattern. No server, no cryptography, no private key
management.

Flow:
1. Customer buys via Stripe at `buy.stripe.com/...`
2. Stripe redirects to `toolkit.densanon.com/densa-deck-success.html?session_id=cs_xxx`
3. Browser JS hashes session_id with `LICENSE_SALT` (`Densa-Deck-pro-v1`) to derive
   `DD-XXXX-XXXX-XXXX` key
4. Customer pastes key into Settings → Activate Pro license
5. Desktop app re-hashes segments and verifies checksum offline

**Critical:** The Python hash in `licensing.py::_hash_key()` must bit-for-bit match
the JS hash in `toolkit.densanon.com/densa-deck-success.html`. Regression locked
via `tests/test_licensing.py::TestJavaScriptCompatibility`.

**If you change `LICENSE_SALT` or the hash algorithm, all existing licenses break.**

Master key: `densanon-mtg-engine-2026` (dev bypass).

Tier detection order: `MTG_ENGINE_TIER` env var → saved license file → `config.json`
→ default free.

## Where Things Live

- **Engine repo:** `densanon-devs/densa-deck` (this repo)
- **Free web tools:** `toolkit.densanon.com/categories/mtg-tools/` (calc, analyzer,
  staples — in `densanon-devs/densanon-toolkit`)
- **Product page:** `toolkit.densanon.com/densa-deck.html`
- **Success page:** `toolkit.densanon.com/densa-deck-success.html`
- **Version manifest:** `toolkit.densanon.com/densa-deck-version.json`
- **Binary release:** GitHub Release on `densanon-devs/densa-deck`, asset
  `Densa-Deck-<version>-windows.zip` (folder mode, ~107 MB unzipped, ~55 MB zipped)

## Building the Desktop Binary

```bash
pip install pywebview llama-cpp-python httpx pydantic rich
python scripts/build_desktop.py
# Output: dist/densa-deck/ (~107 MB, folder mode)
```

`densa-deck.spec` excludes torch / scipy / transformers / faiss / django / etc.
to keep the bundle small, AND filters CUDA / cuBLAS DLLs from a.binaries (see
`_is_cuda_dll` in the spec). llama_cpp falls back to CPU on customer machines.

`scripts/build_desktop.py` handles a Windows cp1252 stdout decoding gotcha (Rich
box-drawing chars in `analyst show` output) — see `feedback_windows_stdout_cp1252.md`.

## Legal Requirements

- **No hosted card images** — always hotlink Scryfall
- **Scryfall attribution** on every output (CLI footer)
- **WotC disclaimer** — "Not affiliated with Wizards of the Coast..."
- **Combo data attribution** — "Combo data via Commander Spellbook (MIT, ©
  2023 Commander-Spellbook)" surfaced in Settings + every export
- **Independent branding** — no MTG logos
- **Feature-gated monetization** — never paywall raw card data
- **EDHREC: NO-GO** — ToS forbids commercial integration. Don't scrape, cache,
  or live-fetch. See `project_densa_deck_phase6.md` memory.

## Testing

**474 tests, all passing in ~16s:**
```bash
PYTHONPATH=src python -m pytest tests/
```

Key test files:
- `test_licensing.py` (25 tests) — includes JS compat locks
- `test_tiers.py` (12) — tier enforcement
- `test_cli.py` (15) — subprocess integration tests
- `test_app_api.py` (~80) — desktop API surface
- `test_analyst_phase6.py` (15) — Phase 6 + combos
- `test_brackets_and_exports.py` (14) — brackets + near-miss + multi-format export
- `test_goldfish_combos.py` (5) — combo-aware goldfish
- `test_gauntlet_combos.py` (6) — combo-aware matchup + gauntlet
- `test_combo_aware_features.py` (14) — combo power / archetype / mulligan / coach / diff
- `test_combo_aware_v3.py` (8) — protected cuts / export combos / analyze recs
- `test_combo_aware_v4.py` (7) — biased adds / bracket combo lines / save / explain

## Source Docs / Memory

- `~/.claude/projects/D--LLCWork/memory/project_densa_deck_launch.md` — release state, pricing, post-v0.2.0 commit stack
- `~/.claude/projects/D--LLCWork/memory/project_densa_deck_phase6.md` — combo arc state (research, 4 waves, backlog)
- `~/.claude/projects/D--LLCWork/memory/project_densa_deck_v02_plan.md` — v0.1.7 + v0.2.0 historical reference
- `D:\LLCWork\mtg_deck_testing_engine_plan.docx` — original product plan
- `D:\LLCWork\mtg_legal_monetization_strategy.docx` — IP safety, tier model, WotC compliance
