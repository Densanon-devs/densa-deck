# mtg-deck-engine (Densa Deck)

Commercial MTG deck analysis platform. Two halves that ship separately:

* **Desktop** (`src/densa_deck/`) — the engine, the analysis, the licence.
  **0.7.0**, built and installed 2026-09-24, at
  `G:\My Drive\Densanon LLC\DensaDeck\DensaDeck-Desktop-0.7.0-windows.zip`.
  NOT yet cut as a GitHub Release and the version manifest is NOT bumped —
  both of those publish to customers and are a deliberate manual step.
* **Android companion** (`companion/`) — an Expo/React Native app that is a
  real product in its own right, not a remote control. Offline-first: it
  holds its own copy of the card index, scans and identifies cards with no
  PC, and builds decks. **0.57.0 / versionCode 137.** Branch
  `feature/companion-app`, dropped to the same Drive folder.
* **Guide** — `toolkit.densanon.com/densa-deck-help.html`, everything both
  halves do with each entry tagged Free / Needs a PC / Pro. Settings links
  to it. Lives in `densanon-devs/densanon-toolkit`.

## Stack

- Python 3.11+, Pydantic, httpx, Rich CLI, SQLite
- llama-cpp-python (optional, Pro analyst model)
- pywebview for the desktop UI shell
- PyInstaller for desktop binary (~122 MB folder mode / 65 MB zipped, after
  CUDA / torch / scipy / **OpenCV** excludes — see `densa-deck.spec`'s
  post-Analysis filter)

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
- **`collection/`** — physical collection, pricing and reseller layer.
  `storage.py` CollectionStore (`~/.densa-deck/collection.db`, deliberately
  separate from cards.db — `upsert_cards` is INSERT OR REPLACE and would wipe
  anything bolted onto `cards`), per printing/finish/condition stacks.
  `ownership.py` owned/committed/available. `prices.py` PriceProvider seam +
  valuation + price history (owned printings only). `query.py` price-aware
  search. `deck_value.py` deck value vs build value vs cost to complete.
  `scanner.py` OCR-text -> printing identification, `capture.py` camera frame
  -> flattened card -> footer crop (optional OpenCV, NOT bundled).
  `allocation.py` opt-in printing-level deck binding. `reseller.py`
  acquisitions, cost basis, sales, P&L, appraisal. Printing catalogue lives in
  `data/printings.py` (opt-in `default_cards` ingest, ~107k paper printings).
  Full plan + rationale: `docs/COLLECTION_PLAN.md`.
- `benchmarks/` — 6 built-in gauntlet suites (casual-commander, cedh, modern-meta, etc.)

`app/phone.py` — phone-as-scanner bridge. Binds THREE specific addresses:
loopback, this machine's private LAN address, and its tailnet address when
there is one. Never 0.0.0.0 — that distinction is the whole safety argument,
because 0.0.0.0 would also answer on whatever cafe Wi-Fi the laptop joins
next. Token-paired, explicit route allow-list, stopped on app close.

**Wi-Fi is the primary path; Tailscale is optional** (0.7.0). The phone tries
the LAN address first on every call and falls back to the tunnel. Until
0.7.0 `pairing_url()` returned "" with no tailnet, so a desktop and a phone
on the same network could not be introduced at all even though the bridge
was listening on exactly the address the phone would have used.

Page at `app/static/phone/scan.html` — note the .spec needs its own glob
for that subdirectory; `static/*` does not recurse.

Top-level: `cli.py` (~27 commands), `models.py`, `tiers.py` (free/pro gating),
`licensing.py` (hash-based key validation), `legal.py` (attribution).

## CLI Commands

**Free tier:** ingest, analyze (basic), search, info, calc, license, **combos**
(refresh/status/detect/near-miss/density), **rule0**, **bracket**, **export** (mtga/mtgo/moxfield), **collection** (status/sync/add/remove/list/printings/check/value/deck-value/scan/appraise), **phone** (status/serve)

**Pro tier:** analyze --deep, analyze --export, probability, goldfish, gauntlet,
save, compare, history, diff, practice, **explain**, **compare-decks**, coach,
analyst (pull/show)

Tier enforcement in `tiers.py`. Pro commands show upgrade message and exit 0 if
free tier. Tier mappings live in `tiers.COMMAND_FEATURES`.

## Combo Integration (post-v0.2.0 work)

**Commander Spellbook MIT-licensed integration.** ~111.5k variants (2026-09).
Refresh downloads the daily bulk file `json.commanderspellbook.com/variants.json.gz`
(28 MB, not rate-limited) and streams it into the store — ~1 min, ~50 MB peak.
The paged walk over `backend.commanderspellbook.com/variants/` is only the
fallback: the API rate-limits it ~11k combos in, so it resumes across runs
via `last_refresh_partial` / `last_refresh_next_url`, and a partial store is
what makes "Update everything" re-offer Combo data. Download with
`iter_raw` (the host sends `Content-Encoding: gzip`). `tests/conftest.py`
disables the real bulk download in every test.

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
  `Densa-Deck-<version>-windows.zip` (folder mode, ~122 MB unzipped, ~65 MB
  zipped). **0.7.0 is built and on Drive but NOT released** — cutting the
  Release and bumping the version manifest publish to customers.
- **Desktop install on this box:** `%LOCALAPPDATA%\Programs\Densa Deck`,
  with Desktop + Start Menu shortcuts that run `densa-deck app` (the bare
  exe is the CLI and opens a console). Installed FROM the shipped zip, so
  what runs here is byte-for-byte what a customer gets. `pip install -e .`
  under Python 313 also exists and points at `src/` — that one is always
  current by definition.

## The Android Companion (`companion/`)

Expo 54 / React Native. A product, not a remote control: everything below
works with no PC and no signal.

**Build and ship** — `android/` is gitignored and regenerated:
```bash
cd companion
npx expo prebuild --platform android --clean
cd android && export JAVA_HOME="C:/Program Files/Java/jdk-17" &&   ./gradlew assembleRelease --no-daemon
cd ../.. && python scripts/ship_companion.py     # verifies + copies to Drive
```
`JAVA_HOME` on this box points at a JDK 8 that does not exist; every gradle
call needs the export. `--no-daemon` because daemons accumulate at 1-3 GB
each. `ship_companion.py` refuses to ship unless the four version sources
agree AND the version string is actually inside the built JS bundle.

**Tests:** `npm test` (node --test, ~1,230 of them) and `npx tsc --noEmit`.
Some run against a REAL SQLite via `node:sqlite` (`tests/real-sqlite.mjs`) —
`tests/harness.mjs` reimplements queries rather than executing them, so it
has no columns and cannot catch a missing one. That gap shipped a
`no such column: price_usd` crash; schema and ordering are tested for real
now.

**Schema migrations are automatic.** `CREATE TABLE IF NOT EXISTS` does
nothing to an existing table, so a new column reached fresh installs only.
`src/lib/migrate.ts` reads the schema's own CREATE statements back, diffs
them against `PRAGMA table_info`, and adds what is missing. Adding a column
needs no migration written — but tables are created, THEN migrated, THEN
indexes, because an index over a column being added cannot exist first.

**What needs the PC:** deleting a collection, tagging a card into a group,
the set list, wishlist adds, build-from-collection, and all analysis
(combos, brackets, Rule 0, suggestions). Everything else is local.

**Three things the index must be refreshed to gain:** artist names (basic
land scanning), the written-off set list, and which printings come in foil.
All three arrive together in one download.

## Building the Desktop Binary

```bash
pip install pywebview llama-cpp-python httpx pydantic rich
python scripts/build_desktop.py
# Output: dist/densa-deck/ (~122 MB, folder mode)
```

**Close the app first.** The build starts with `rmtree(dist/)` and dies on
`WinError 5` if `densa-deck.exe` is running, naming a file rather than the
running process.

`densa-deck.spec` excludes torch / scipy / transformers / faiss / django / etc.
to keep the bundle small, AND filters CUDA / cuBLAS DLLs from a.binaries (see
`_is_cuda_dll` in the spec). llama_cpp falls back to CPU on customer machines.

Two excludes added 2026-09-24 after an unfiltered 0.7.0 came out at 262 MB:

* **`cv2` / OpenCV, 117 MB.** `api.py` has a `scan_install` flow that
  pip-installs OpenCV ON DEMAND, because desktop photo scanning is opt-in.
  PyInstaller's static analysis finds the lazy `import cv2` inside those
  functions and bundles the wheel anyway — 86 MB of `cv2.pyd` plus a 31 MB
  ffmpeg DLL, shipped to everyone for a feature most never enable, and
  shadowing the copy the install flow would put there.
* **`ggml-cuda.dll`, 31 MB.** `_is_cuda_dll` matched CUDA LIBRARY names, so
  llama.cpp's own CUDA backend walked past it.

262 MB -> 122 MB. The build's `analyst show` smoke test is what proves
dropping the GPU backend did not break the analyst — do not skip it.

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

**2,165 passing, 18 failing, 2 skipped, ~9m30s** (2026-09-24):
```bash
PYTHONPATH=src python -m pytest tests/
```

It is slow because `test_phone_bridge.py` binds real sockets — that file
alone is ~2.5 minutes. Budget for it; it is not hung.

**The 18 failures are all `test_app_icon.py` and none of them are a real
defect.** They assert on PNGs that `scripts/make_icons.py` writes into
`companion/android/app/src/main/res/`, which is GITIGNORED and regenerated
by `expo prebuild --clean` — so they fail for anyone who has built the
companion. The shipped icon is fine: `make_icons.py` also writes
`companion/assets/adaptive-icon.png`, pre-fitted to Android's safe circle,
and Expo regenerates the mipmaps from that. Verified 2026-09-24 by pulling
the launcher foreground out of the shipped APK — 8,989 red pixels among
26,053 opaque, i.e. the real artwork rather than the white ghost that test
was written to catch. **The fix is to repoint the test at
`companion/assets/`; asserting on a regenerated directory cannot work.**

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
