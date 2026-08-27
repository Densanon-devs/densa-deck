# Next session: visual deck view, and printing-level decklists

Written 2026-08-27, at the end of a long session, for whoever picks this up
next — probably me with no memory of it. Current state: companion **0.7.4**
on Drive, desktop rebuilt and running, tree clean at `174b7b3`, 1,564 Python
tests + 340 companion tests green.

Two pieces. The first is small and self-contained. The second is a data model
change and should not be started without room to finish it.

---

## Piece 1 — Visual / Written tabs on the deck screen (~20 min)

**What the user asked for:** "there should be two tabs and the visual should
be the default: Visual grid of current deck list, and the written form."

### Where
`companion/src/screens/Decks.tsx`, in **`DeckScreen`** (the component starting
around line 127) — *not* `DeckListScreen`.

### The trap that cost the last attempt
I anchored an edit on `<TextInput style={styles.list}` and it matched
`DeckListScreen`'s "New deck name" box first, which is `[styles.list,
styles.searchBox]`. The edit went into the wrong component and produced
unbalanced JSX. **Anchor on something unique to `DeckScreen`** — the
`placeholder={'4 Lightning Bolt\n1 Sol Ring'}` line, or the `Save deck`
button — and check which component you are inside before writing.

### Shape
* `const [view, setView] = useState<'visual' | 'text'>('visual')`
* Two buttons reusing the existing `styles.zone` / `styles.zoneOn` /
  `styles.zoneText` / `styles.zoneTextOn` from the Deck/Sideboard toggle, so
  it looks like the control directly above it.
* `visual`: a wrapped `View` of tiles — art, a quantity badge, the name —
  reading from `zone === 'side' ? deck.sideboard : deck.decklist`. Copy the
  tile styles from `CardBrowser`'s grid; they already work.
* `text`: the existing `TextInput` + `Save deck`, unchanged.

### Rules that apply here
* **Lay it out, do not scroll it.** No `ScrollView` inside the deck page —
  see `docs`-worthy note at the top of `CardBrowser.tsx` and the memory
  `rn-one-scroller-per-screen`. Five bugs this session came from ignoring it.
* Art comes from `artSource(printingId, 'small')` in `src/lib/images.ts`,
  which carries the User-Agent Scryfall's CDN requires — a bare URL gets 400.

### The honest limitation until Piece 2 lands
A decklist stores **names**, so the visual grid can only show *a* printing of
each card. Say so on the screen rather than letting it imply otherwise: the
right picture of the right card, the wrong picture of the right *printing*.

---

## Piece 2 — Decklists that remember which printing (a real change)

**What the user asked for:** "if I add the full art that cost $50 and not the
basic that cost $16 these should clearly be differentiated."

They are right, and this reverses a decision made earlier in the project.
`companion/src/lib/decks.ts` currently says, above `decklist`:

> `/** Card name -> copies. Deliberately not printings; see above. */`

The original reasoning was that a deck slot says "Sol Ring" and any printing
satisfies it. That holds for legality and for goldfishing. It does **not**
hold for what the deck is worth, or for "which of my copies is in this box",
which is what the user is actually asking.

### Design decision to make first
Two options, and they are not equally good:

1. **`Record<string, number>` keyed by printing id.** Simple, but loses the
   "any printing will do" case — a decklist imported from Moxfield has names
   only, and every one would need resolving before it could be stored.
2. **Entries carry an optional printing.** `{ name, printing_id?, qty }`.
   A name-only entry keeps meaning "any printing", which is what an import
   is; a printing-level entry means "this exact card". **Prefer this.** It
   makes the migration a widening rather than a rewrite, and every existing
   deck stays valid with no conversion.

### Everything it touches
Work through these deliberately; the order matters because the later ones
read the earlier ones.

- [ ] `companion/src/lib/decks.ts` — the `Deck` type, `parseDecklist`,
      `formatDecklist`, `addToDeck`, `removeFromDeck`, `deckSize`,
      `mergeCounts`, `shortfall`, `deckWarnings`.
- [ ] **Text format.** A printing-level line needs set and number, in the
      form the exporters already use: `1 Sol Ring (CMM) 410`. `parseDecklist`
      already STRIPS that suffix — see the `.replace(/\s*\([A-Za-z0-9]{2,6}\)…`
      in it. Capture it instead of discarding it, and emit it from
      `formatDecklist` only for entries that have one, so a name-only deck
      round-trips unchanged.
- [ ] `DeckStore.save` / `unpack` — the column already holds either a bare map
      or `{main, side}`. Add a third shape rather than migrating: readers must
      keep handling all of them. There are rows on the user's phone.
- [ ] **Sync.** Decks are documents (last-write-wins), so no new event kind —
      but check `analyzeOnDesktop` and anything that sends `decklist_text`.
- [ ] `shortfall` matches by **lowercased name** today. A printing-level entry
      should still be satisfied by that printing specifically; a name-only
      entry by any. Do not silently make one behave like the other.
- [ ] Wishlist maths, same reasoning — `_refresh_wishlist_for_deck` on the
      desktop (`src/densa_deck/app/api.py`).
- [ ] Phone UI: the browser preview already pages through printings
      (`cards/printings`), so **Add** from a specific page should record that
      printing. That is the natural place for the user to choose.
- [ ] Desktop `builder.js` — it already holds `{name, qty}` entries per zone
      and has Mainboard/Sideboard/Commander tabs.
- [ ] Deck value: with printings known, the deck's worth becomes real rather
      than an estimate off a representative printing. Worth surfacing.

### Tests to write first
The failure mode here is silent and expensive, so pin it before building:

- a name-only deck round-trips through save/load/format unchanged;
- a printing-level entry keeps its set and number through the same trip;
- the same card at two printings is two entries, not one merged count;
- `shortfall` for a printing-level entry is not satisfied by owning a
  different printing;
- a deck saved before this change still opens with its cards (there are real
  ones on the phone — this is the test that catches the migration going
  wrong, and the equivalent test caught it last time);
- deck value differs between two printings of the same card.

---

## Standing context worth not rediscovering

* **Ship path**: bump `app.json` (version + `android.versionCode`),
  `src/lib/version.ts`, `package.json`, `android/app/build.gradle` — a test
  fails if they disagree. Build with `./gradlew assembleRelease` and
  `JAVA_HOME` set to Android Studio's JBR. Copy to
  `G:\My Drive\Densanon LLC\DensaDeck\`, hash-verify, delete the old one.
* **The desktop binary is stale the moment you change Python.** Rebuild with
  `scripts/build_desktop.py` and restart it, or the phone gets `unknown
  route`. This has bitten twice.
* **`tests/test_apk_contents.py`** asserts user-visible strings are in the
  shipped bundle. Add one per new screen; a screen nothing navigates to is
  silently absent from the build.
* **One vertical scroller per screen.** Memory:
  `rn-one-scroller-per-screen`.
* **Layout has no test coverage at all.** Every check asserts a string is in
  the bundle; none can tell whether it is visible. Three layout bugs reached
  the user this session. If there is appetite, a snapshot or a render test is
  the gap worth closing.
