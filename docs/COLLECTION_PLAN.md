# Densa Deck — Physical Collection, Pricing & Reseller Arc

Plan of record for the six-phase expansion that turns Densa Deck from a deck
*testing lab* into a tool that also knows what cardboard you physically own,
what it's worth, and — eventually — whether buying more of it is a good idea.

Written against **v0.6.0** (789 tests, 41 test files). Every claim about the
current codebase below was verified against source, not assumed.

---

## STATUS — all six phases built (2026-08-15)

**1,146 tests green + 17 OpenCV-gated skips** (789 → 1,146, +357), ruff clean, no new third-party
dependencies. Not yet released: version strings are untouched at 0.6.0 and
nothing is committed.

| Phase | State | Where it lives |
|---|---|---|
| 1 Collection | done | `collection/{storage,models,ownership}.py`, `data/printings.py` |
| 2 Pricing | done | `collection/{prices,query}.py` |
| 3 Deck intelligence | done | `collection/{deck_value,allocation}.py`, `search_structured(ownership=)` |
| 4 Scanner | done | `collection/{scanner,scan_backends,capture}.py`, Scan tab |
| 5 Reseller | done | `collection/reseller.py` |
| 6 Acquisition analyzer | done | `collection/reseller.py::analyze_acquisition` |

Measured against real data, not fixtures:

- Printing ingest: **107,353 paper printings, 15.4s including the 74 MB
  download, 38.3 MB on disk.**
- Scanner identification over 400 real printings × 5 OCR-damage models:
  **0 wrong auto-adds in 2,000 attempts**; 399/400 exact on a clean read.
- UI driven headlessly against the real Python API across all six phases:
  **zero page errors, zero console errors.**

### Follow-up pass (same day) — gaps closed

- **Scan tab shipped** — 7th view: capability reporting, identify by name or
  corner text, confidence-tiered candidate picker, continuous session totals,
  session appraisal.
- **Camera capture built** — `collection/capture.py`: card quad detection,
  perspective warp to a flat 488x680, footer/title crops, OCR enhancement.
  Verified against synthetic frames in an isolated OpenCV venv (29 tests),
  including that the footer crop lands on the footer at 0 deg and 10 deg
  rotation. **Not exercised against a real webcam** — no camera on this
  machine. OpenCV stays optional and unbundled; without it every entry point
  degrades to a clear message (12 tests lock that path).
- **Printing-level allocation shipped** — `collection/allocation.py`. Opt-in,
  keyed on stable `deck_id`, refuses to over-allocate, auto-frees on deck
  delete, and `reconcile()` clears allocations stranded by sold cards.
  Oracle-level remains the default and is provably unaffected.
- **Three pre-existing bugs fixed** — see section 4.

### Phone scanning over Tailscale (2026-08-17)

Your phone becomes the camera; this machine stays the brain. Cards added on
the phone land in the same collection and the *same scanning session* as the
desktop Scan tab — it is an input device, not a second app.

```
phone browser --HTTPS--> tailscale serve --HTTP--> 127.0.0.1:8791 --> AppApi
```

Two problems solve themselves at once, which is why Tailscale rather than a
LAN server:

1. **`getUserMedia` requires a secure context.** A phone hitting
   `http://100.x.y.z:8791` gets no camera — not a denied prompt, an absent
   API. `tailscale serve` terminates TLS with a real LetsEncrypt cert for the
   machine's MagicDNS name, so the phone sees an ordinary trusted origin.
   (Note loopback is *itself* a secure context, so testing on 127.0.0.1
   silently exercises the happy path — the fallback branch has to be forced.)
2. **Exposure.** The server binds **127.0.0.1 only**. It is unreachable from
   the LAN or any café network; the Tailscale proxy is the sole ingress, and
   that only accepts devices already on the tailnet.

Tailscale proves *which device*, not *what you meant*, so on top of it:

- a **pairing token**, minted per session, required on every request, rotated
  on restart — stopping the bridge revokes any open phone;
- an **explicit allow-list** of routes (identify / commit / skip / session /
  appraise / capture). `delete_deck`, `record_sale`, `printings_remove`,
  `activate_license` and friends are unreachable, with a parametrised test
  asserting each one stays that way;
- **off by default**, and stopped in `AppApi.close()` so a live socket and a
  valid token can never outlive the window.

**`tailscale serve` hangs when the tailnet has no HTTPS certificates**, so
the guidance is three-state and checks `CertDomains` from `tailscale status
--json` before ever showing the command. Telling someone to run it in that
state wedges their terminal with no error. This is the convention densabooks
already established in this workspace (`backend/apps/core/tailscale.py`), and
the cost is stated rather than buried: enabling HTTPS publishes this machine's
name to the public Certificate Transparency log, permanently, and buys exactly
one thing — a live viewfinder.

`tailscale serve` is **surfaced, not run for you** — it changes machine-level
network config, can need elevation, and provisions a public certificate for
your machine name. The UI and `densa-deck phone status` print the exact
one-liner.

Without HTTPS the phone still works: it falls back to
`<input capture="environment">`, which hands off to the OS camera app over
plain HTTP, and typing a card's corner text always works. Photo scanning
additionally needs OpenCV + an OCR engine on the *desktop*; when either is
missing the phone says which one and how to get it.

**Verified:** 53 bridge tests (loopback-only binding, token auth, route
scoping, revocation) plus an end-to-end run driving the real phone page in a
390x844 touch viewport against the real 107k-printing catalogue — exact reads
auto-add, ambiguous reads offer 25 printings and file nothing, and the
desktop sees the same session. **Not tested on the physical phone** — the
tailnet shows it online, but nobody has held a card up to it yet.

---

## 0. The three facts that shape everything

### 0.1 The card DB has no concept of a printing

`data/scryfall.py:19-20` pins the ingest to one bulk file:

```python
BULK_TYPE = "oracle_cards"  # One entry per unique card (no reprints)
```

So `cards` holds **one row per oracle card** (34,541 live) with an arbitrary
representative printing's `scryfall_id` and `set_code`. There is no
`set_name`, `collector_number`, `lang`, `finishes`, or `games` column anywhere.

A physical collection is per-printing, per-finish, per-condition. Nothing in
the schema can express that today. **This is the whole of Phase 1.**

Measured cost of fixing it (`default_cards` bulk, probed 2026-08-14):

| | |
|---|---|
| Download | 73.9 MB gz, 0.9s |
| Rows in file / paper printings kept | 116,710 → **107,353** |
| Parse + insert + index | **6.2s** |
| Resulting SQLite | 54.3 MB with image columns; ~35 MB without |
| Distinct oracle ids | 37,556 |

Six seconds. This was the main risk in the whole arc and it evaporated.

### 0.2 Prices are already here, and already lossy

Pricing shipped in v0.5.0 — `analysis/pricing.py`, `cards.price_usd`,
`--budget`, deck-value panels, TCGPlayer affiliate links. Do not rebuild it.

But `scryfall.py:207-224` collapses three prices into one float:

```python
for key in ("usd", "usd_foil", "usd_etched"):
    val = prices.get(key)
    if val:
        return float(val)
```

A card whose only printings are foil silently reports its **foil** price as
its normal price. Printing-level pricing is not just a new feature — it fixes
an existing inaccuracy in the shipped deck-value numbers.

Bulk data carries `usd`, `usd_foil`, `usd_etched`, `eur`, `tix` per printing,
so Phase 2 needs **no new data source**. It reads columns Phase 1 already
downloaded.

### 0.3 Scryfall draws a hard line exactly where phases 5–6 live

From the bulk-data docs, verbatim:

> Card objects in bulk data include price information, but prices should be
> considered **dangerously stale after 24 hours**. Only use bulk price
> information to **track trends or provide a general estimate of card value**.
> Prices are **not updated frequently enough to power a storefront or sales
> system**. You consume price information at your own risk.

And from the rate-limit docs:

> If you need to rapidly look up card names, **prices**, or resolve a large
> number of card images, **you must use the bulk data files**.

Reading:

- Phases 1–3 (own it, value it, build with it) are squarely inside
  "general estimate of card value". Green.
- Phases 5–6 (cost basis, realized P&L, "should I buy this collection for
  $1,400?") are decisions about real money made on data whose own publisher
  disclaims that use. Not forbidden, but not what the feed is for.

This does not cancel phases 5–6. It constrains how they're built:

1. **A `PriceProvider` seam from day one.** Scryfall is the default (free,
   estimates). A real market feed can be swapped in for reseller work
   without touching call sites. `default_cards` gives us `tcgplayer_id`,
   which we don't have today — that's the bridge.
2. **Price age is never hidden.** Every surface showing money shows when the
   price was captured. Scryfall's own word is "dangerously".
3. **No bare verdicts.** Phase 6 shows a modelled range with its inputs
   visible, never a naked "✓ BUY" over a number the publisher won't stand behind.

---

## 1. Data architecture

### 1.1 Two databases, split on one question: can this be re-downloaded?

```
~/.densa-deck/
  cards.db        card_printings   <- derived, disposable, re-downloadable
  collection.db   your cardboard   <- irreplaceable, must survive everything
```

`card_printings` goes in `cards.db` because it must JOIN `cards` on
`oracle_id`. Precedent: `card_aliases` was retrofitted the same way.

Everything the user owns goes in a **new `collection.db`**. This is not
stylistic:

**`upsert_cards` uses `INSERT OR REPLACE` with an explicit 21-column list**
(`database.py:150-158`). `INSERT OR REPLACE` is DELETE + INSERT. Any column
added to `cards` that isn't in that list is **silently wiped on every
Scryfall re-ingest**. Ownership data on the `cards` table would quietly
destroy itself the first time the user updated their card database.

Separation also buys: deleting a corrupt `cards.db` (a real support path)
costs a 6-second re-download and loses nothing; the collection can be backed
up as one small file; and it matches the six existing sibling stores
(`versions.db`, `combos.db`, `rulings.db`, `iterations.db`, `playgroup.db`).

### 1.2 `card_printings` (in `cards.db`)

```sql
CREATE TABLE IF NOT EXISTS card_printings (
    printing_id      TEXT PRIMARY KEY,   -- Scryfall card id (per printing)
    oracle_id        TEXT NOT NULL,      -- joins cards.oracle_id
    name             TEXT NOT NULL,
    set_code         TEXT NOT NULL,
    set_name         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    rarity           TEXT DEFAULT '',
    lang             TEXT DEFAULT 'en',
    released_at      TEXT DEFAULT '',
    finishes         TEXT DEFAULT '',    -- csv: nonfoil,foil,etched
    frame            TEXT DEFAULT '',
    border_color     TEXT DEFAULT '',
    promo_types      TEXT DEFAULT '',
    tcgplayer_id     INTEGER,            -- real product URLs, finally
    price_usd        REAL,
    price_usd_foil   REAL,
    price_usd_etched REAL,
    prices_synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pr_oracle ON card_printings(oracle_id);
CREATE INDEX IF NOT EXISTS idx_pr_name   ON card_printings(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_pr_setnum ON card_printings(set_code, collector_number);
```

Deliberate omissions:

- **No image columns.** `legal.scryfall_image_url()` derives the URL from the
  UUID (`f"{BASE}/{size}/{face}/{id[0]}/{id[1]}/{id}.jpg"`). It works
  per-printing. Storing them would waste ~19 MB to duplicate a pure function.
- **No `data_json` blob.** That's what makes `cards` 4 KB/row and 134 MB.
  Slim rows keep the whole printings table around 35 MB.
- **Paper only.** Filter on `"paper" in games` — you cannot physically own an
  Arena card. Drops 9,357 rows.

Added to `_SCHEMA` (not `_MIGRATIONS`): `executescript` runs
`CREATE TABLE IF NOT EXISTS` on every connect, so new *tables* need no
migration entry. New *columns* on existing tables do — that asymmetry is why
`price_usd` appears in both places.

### 1.3 `collection.db`

```sql
CREATE TABLE IF NOT EXISTS collection_items (
    item_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    printing_id  TEXT NOT NULL,
    oracle_id    TEXT NOT NULL DEFAULT '',   -- denormalized
    card_name    TEXT NOT NULL,              -- denormalized
    finish       TEXT NOT NULL DEFAULT 'nonfoil',
    condition    TEXT NOT NULL DEFAULT 'NM',
    language     TEXT NOT NULL DEFAULT 'en',
    quantity     INTEGER NOT NULL DEFAULT 0,
    location     TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    acquired_at  TEXT,
    unit_cost_usd REAL,          -- phase 5
    acquisition_id INTEGER,      -- phase 5
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ci_stack
    ON collection_items(printing_id, finish, condition, language, location);
CREATE INDEX IF NOT EXISTS idx_ci_oracle ON collection_items(oracle_id);
CREATE INDEX IF NOT EXISTS idx_ci_name   ON collection_items(card_name COLLATE NOCASE);
```

**`oracle_id` and `card_name` are denormalized on purpose.** The collection
must stay readable if the user never downloads printings, deletes `cards.db`,
or a printing vanishes from a future bulk. Ownership data outliving the
catalogue that described it is a hard requirement, and cross-database foreign
keys don't exist anyway (SQLite, separate files, `PRAGMA foreign_keys` never
set in this codebase).

The unique index defines a **stack**: same printing in a different box is a
separate row, so `location` belongs in the key.

`condition` ∈ `NM, LP, MP, HP, DMG` · `finish` ∈ `nonfoil, foil, etched`,
validated against that printing's actual `finishes` (you cannot own an etched
copy of a printing never made etched — a check ChatGPT's schema can't express).

Also in `collection.db`: `collection_events` (append-only audit of every
quantity change — makes "where did these 4 copies come from" answerable and
gives Phase 5 its ledger for free).

### 1.4 Price history — owned-only

Snapshotting 107,353 printings daily is 39 M rows/year and pointless. Snapshot
only printings the user **owns or has in a saved deck**: hundreds of rows a
day, and the only rows anyone will ever chart.

```sql
CREATE TABLE IF NOT EXISTS price_history (
    printing_id TEXT NOT NULL,
    captured_on TEXT NOT NULL,   -- YYYY-MM-DD, one row per printing per day
    finish      TEXT NOT NULL,
    price_usd   REAL,
    source      TEXT NOT NULL DEFAULT 'scryfall',
    PRIMARY KEY (printing_id, captured_on, finish)
);
```

History lives in `collection.db`, not `cards.db` — it accumulates over time and
**cannot be re-derived** (Scryfall only serves today's prices). That makes it
precious by the §1.1 test, despite being machine-generated.

---

## 2. Phases

Each phase ends at a **lock gate**. The loop per phase, adapted from the
source plan — "mobile test" is dropped (there is no mobile build) and replaced
with the step that has actually broken this project repeatedly:

```
schema -> store + tests -> API -> UI -> real cards -> stress -> deck integration
      -> FROZEN-BUNDLE TEST -> lock
```

The bundle step is not ceremony: v0.1.0–v0.1.3 shipped four consecutive
releases broken by PyInstaller (SQLite thread crash, missing numpy, analyst
not loading). New JS files ship automatically via the `static/*` glob, but a
new Python dependency needs `hidden_imports` and must dodge `excludes`.

---

### Phase 1 — Collection system *(target v0.7.0)*

**Goal:** Densa Deck knows what cardboard you own, down to the printing.

- `densa_deck/collection/` package — `models.py` + `storage.py`, mirroring
  `playgroup/` exactly (optional `db_path=` for test isolation).
- `card_printings` table + a **separate opt-in printings ingest**, modelled on
  the v0.6.0 rulings download ("not part of card data, never fetched
  automatically, removable in one click"). Triggered on first Collection use,
  not at startup. 74 MB / 6s / ~35 MB disk, all disclosed before the click.
- Reuses `scryfall.py`'s existing `_open_bulk` / `iter_bulk_records` streaming
  helpers. `load_bulk_file` accumulates everything in memory before upsert, so
  the printings path must batch instead (~107k rows).
- New Collection view (6th tab). **Ctrl+1..5 becomes Ctrl+1..6** —
  `app.js:230-243` carries an explicit warning that adding a tab without
  bumping that list silently misroutes every later shortcut.
- Ownership badges wherever cards render: search tiles (mirroring
  `.card-tile-completer-badge`, opposite corner from `.tile-add`), deck rows,
  suggestion rows, castability tables.
- `search_structured` gains an ownership filter via
  `EXISTS (SELECT 1 FROM ...)` — a single appended condition, so the COUNT and
  page queries stay in sync without a JOIN or column-qualification pass.
- CLI `collection` with nested actions (copy `playgroup`'s dispatch shape);
  MCP read tools; `tiers.COMMAND_FEATURES` entry — **a missing mapping
  silently ships as free** (`tiers.py:140-142`).

**Lock gate:** add/remove copies · multiple printings of one card · duplicates
· owned-only search · ownership visible while deck building · copies committed
to decks visible · survives a `cards.db` delete · frozen bundle launches and
scans a collection.

---

### Phase 2 — Pricing engine *(target v0.8.0)*

**Goal:** what is it worth?

- `PriceProvider` protocol; `ScryfallBulkProvider` as the only implementation.
  Every price read goes through it.
- Per-printing/per-finish valuation replacing the single-float fallback. A foil
  copy is valued at `usd_foil`, not at whatever `_parse_price` found first.
- Collection header: total value, unique/total counts, unpriced count.
- Daily `price_history` capture for owned printings; 24h/7d/30d deltas.
- Price filters and sorts (`<$1 … $100+`, by value, by owned value, by movers).
- **Every money surface shows price age.** A price older than 24h is rendered
  as stale, per Scryfall's own guidance.
- `analysis/pricing.py` keeps its API; its internals learn about printings.

**Lock gate:** collection value matches a hand-computed sample · foil priced as
foil · unpriced cards never counted as $0 (NULL means unknown, never free — a
convention held consistently across four modules today) · price age visible
everywhere · history accumulates.

---

### Phase 3 — Deck + collection intelligence *(target v0.9.0)*

**Goal:** owned / missing / allocated / cost-to-complete.

The hard part, and the part the source plan hand-waves. Two obstacles:

1. **Deck entries have no printing identity.** `DeckEntry` is
   `card_name + quantity + zone`. The parser *explicitly strips* set codes:
   `re.sub(r"\s*\([A-Za-z0-9]+\)\s*\d*\s*$", "", line)` (`parser.py:81-85`).
   A round-trip through Densa Deck currently destroys the user's printing choice.
2. **Decks are versioned snapshots.** `DeckSnapshot.decklist` is
   `{name: qty}` — allocation must key on the stable `deck_id`, never a
   `version_id`, and "committed" means the latest saved version of each deck.

Design — two levels, so the useful 90% needs no schema change to decks:

- **Level 1 (default, oracle-level).** `available(oracle) = owned − committed`.
  Delivers "Owned 4 / Allocated 3 / Available 1" with zero change to the deck
  model. This covers nearly every real question.
- **Level 2 (opt-in, printing-level).** `deck_allocations(deck_id, card_name,
  zone, item_id, quantity)` in `collection.db` binds a specific physical copy
  to a deck slot. Only for users who care which foil is sleeved where.

Plus: exact deck value (assigned printings) vs **build value** (cheapest legal
printing) — the genuinely useful split; cost-to-complete; owned/available/
budget filters in the builder.

**Lock gate:** allocation correct when three decks share one playset ·
over-allocation warns rather than silently going negative · deck value and
build value differ correctly on a deliberately-foiled deck · deleting a deck
frees its copies.

---

### Phase 4 — Scanner *(target v0.10.0)*

**Goal:** cardboard into the database fast.

The source plan implies matching art against a reference set. Downloading and
hashing ~107k art crops is the wrong shape. Better:

- **Cards from Magic 2015 onward print their own collector number and set code
  in the bottom-left corner.** OCR that line → exact printing in one indexed
  lookup against `idx_pr_setnum`. No image corpus, no hashing, exact.
- **Fallback for older cards:** OCR the title → oracle match → disambiguate
  among *that card's* printings only (usually <150, often <10) by perceptual
  hash of the art crop, fetched lazily. `*.scryfall.io` has **no rate limit**.
- **OCR engine:** prefer `Windows.Media.Ocr` via winrt — zero bundle cost, on
  every Windows 10+ box, already proven in DensAssistant. Tesseract optional.
- **Bundle risk is the real constraint.** `opencv-python-headless` is ~50 MB
  against a 107 MB bundle. Ship the scanner as an **optional on-demand
  component**, exactly like the analyst GGUF and the rulings file. Non-scanning
  users pay nothing.
- Confidence tiers: high → auto-add; medium → pick from candidates; low →
  manual. Never silently add a guess; bad inventory data is worse than none.
- Continuous mode with session running totals.

**Lock gate:** 50 physical cards scanned, ≥95% correct printing on modern
cards, zero silent wrong adds, bundle growth ≤ optional download.

---

### Phase 5 — Reseller *(target v0.11.0)*

**Goal:** did I make money?

Acquisitions → scan-into-acquisition → proportional cost basis → sales with
fees/shipping → realized vs unrealized P&L → dashboard.

Cost basis: allocate a lot's purchase price across its cards **proportionally
to market value at acquisition time** (hence `price_history` — the snapshot
must be taken when the lot is created, not recomputed later from today's prices).

Constraints from §0.3, non-negotiable:

- Gross spread is labelled **estimated**, never "profit".
- Fee/shipping models are user-editable inputs, not hardcoded truths.
- Every figure carries its price date.
- The provider seam is where a real market feed lands if this becomes serious.

---

### Phase 6 — Acquisition analyzer *(target v1.0.0)*

Scan a stranger's collection in the field → estimated net resale → target
purchase bands (conservative/normal/aggressive) → live slider recomputing
profit and ROI.

Framed throughout as a **model with visible inputs**, not an oracle: show the
price date, the fee assumptions, the confidence, and how many cards were
unpriced (8.6% of printings have no price at all — on a 700-card box that's
~60 cards contributing $0 to a number someone is about to spend real money on).
Suppressing that would be the single most misleading thing this app could do.

---

## 3. Tier strategy

**Recommendation:**

| Free | Pro |
|---|---|
| Collection CRUD, browse, search | Portfolio analytics, value over time, movers |
| Ownership badges everywhere | Scanner |
| Owned/missing/available in decks | Reseller: acquisitions, cost basis, P&L |
| Basic collection value | Acquisition analyzer |

Rationale:

- `CLAUDE.md` forbids paywalling raw card data, and `api.py:1977` already
  classifies pricing as *"commodity data and a draw to ingest"*. Raw prices
  stay free.
- `playgroup` set the precedent that local user-owned CRUD is free
  (`tiers.py:55`). Your own collection is *yours*, not card data.
- Retention: once a collection is entered, the user does not leave. Free
  ownership is the hook; the money layer is the product.
- Passes the Bartle test already logged for this product: every gate is an
  *analysis capability*, never data access or anything resembling card power.

Open question for Jordan: phases 5–6 are a different customer (a dealer, not a
player) and plausibly a separate SKU above the $49.99 one-time. Flagging, not
deciding.

---

## 4. Bugs found while surveying (pre-existing, not introduced here)

**All three are now FIXED**, with regression locks in
`tests/test_regressions_v07.py`. Kept here because the failure modes are
worth remembering — every one of them was silent.

Three live defects in shipped v0.6.0, found while mapping the code:

1. **`compare_decks_analyst` and `duel_decks` are broken at runtime.**
   `DeckSnapshot` (`versioning/storage.py:43-55`) has no `name` or `format`
   field, but `api.py:2366,2370` and `api.py:3373-3374,3387,3426` read
   `snap.format` / `snap.name`. Confirmed: raises
   `AttributeError: 'DeckSnapshot' object has no attribute 'format'`. `@_safe`
   swallows it into `{ok: false}`, so both features fail for every user on
   every call. The data exists in the `decks` table; `coach_start` already
   works around it via `list_decks()` (`api.py:2711-2716`).

2. **MCP `search_cards` silently drops two filters and crashes on a third.**
   `mcp/tools.py:88-95` sends `max_price_usd` and `type_line`, but
   `api.search_cards` reads `max_price` and `types` — both silently ignored.
   It also passes `rarity` as a list where `database.py:294` calls
   `.strip()` → `AttributeError`.

3. **Two stores ignore the test path override.** `_get_iteration_store()`
   (`api.py:1651`) and `_get_playgroup_store()` (`api.py:1854`) construct with
   no `db_path`, so tests and custom installs write into the real
   `~/.densa-deck/`.

None blocked this arc. #1 was user-facing and is the most valuable fix in
the set: two shipped features returned `{ok: false}` for every user on
every call, with no traceback anyone would ever see.

---

## 5. Conventions this arc must follow

Verified against the repo, not assumed:

- **Tests:** `PYTHONPATH=src python -m pytest tests/`. 789 collected. No
  `conftest.py` — fixtures are per-file. CI runs ruff (`E,F,I,W`, import
  sorting enforced) *before* tests, with `-m "not network"`.
- **Envelope:** every `AppApi` method is `@_safe`-decorated →
  `{ok, data}` / `{ok, error, error_type}`. A dict that already has `ok`
  passes through **unwrapped**.
- **Pro-gating:** GUI is the primary gate; server-side is defence in depth
  returning `error_type: "ProRequired"`. MCP uses `assert_pro()` as the first
  statement of each Pro closure.
- **Frontend:** vanilla, no build step, pywebview `js_api` bridge, positional
  args through `callApi()`. New view files follow the `builder.js` IIFE shape
  and load after `app.js`. New `.js` needs only a `<script>` tag — the
  PyInstaller `static/*` glob and the `package-data` glob both cover it.
- **CSS:** append a version-stamped banner block; don't interleave.
- **Version bumps touch 5 files:** `pyproject.toml:7`,
  `src/densa_deck/__init__.py:3`, `packaging/installer.iss:24`,
  `combos/data.py:49`, `app/api.py:2320` (the last two are duplicated
  User-Agent literals and are the ones that get missed).
- **Docs:** `docs/MCP-OPERATOR.md` hardcodes tool counts in four places.
  Release notes are customer-facing prose (`RELEASE_NOTES_vX.md`), not a
  changelog.
- **Legal:** no hosted images (hotlink only), Scryfall attribution, WotC
  disclaimer, never paywall raw card data, EDHREC remains NO-GO.
