# Visual deck view, and printing-level decklists

Planned 2026-08-27, **built the same day**. This document was a plan; it is
now a record of what shipped and what deliberately did not, for whoever picks
this up next — probably me with no memory of it.

Companion **0.7.5** (versionCode 39). Both pieces are done, tested and in the
built APK.

---

## The idea, in one paragraph

A decklist has always been a list of card NAMES, and for legality, combos and
goldfishing that is exactly right: those are facts about cards. It is wrong
for two questions people actually ask — *what is this deck worth* and *which
of my copies is sleeved in it*. The user put it plainly: "if I add the full
art that cost $50 and not the basic that cost $16 these should clearly be
differentiated."

So a deck slot may now carry a printing, **optionally**. A name-only slot
still means "any printing", which is what it always meant and what an import
from Moxfield is. A slot with a printing means that exact card. This is a
widening, not a rewrite: every deck that existed before is still valid, and
nothing needed converting.

---

## Piece 1 — Visual / Written tabs on the deck screen

`companion/src/screens/Decks.tsx`, in **`DeckScreen`**. Visual is the default:
a decklist as text is the form you SEND, and a wall of card faces is the form
you think in.

* Two buttons reusing the `styles.zone` control from the Deck/Sideboard
  toggle directly above, because they do the same kind of thing — switch what
  you are looking at rather than change anything.
* Visual is a wrapped `View` of tiles: art, a quantity badge, the name, and a
  line saying which printing. **No `ScrollView` inside the page** — one
  scroller per screen, and it belongs to the page.
* Tap a tile for one more copy, hold for one fewer.
* Written is the old `TextInput` + `Save deck`, plus a line explaining that
  `1 Sol Ring (CMM) 410` means that exact printing.

The trap that cost the previous attempt — anchoring an edit on
`<TextInput style={styles.list}` and hitting `DeckListScreen`'s "New deck
name" box instead — was avoided by rewriting the file whole.

**Every tile says which card it is.** A slot that named a printing shows
`CMM 410` in green; a slot that did not says `any printing · showing CMM 410`.
The picture is never allowed to imply a choice nobody made.

---

## Piece 2 — Decklists that remember which printing

Option 2 from the plan was taken: **entries carry an optional printing**, so
the migration is a widening rather than a rewrite.

### The phone: a deck is a list of slots

`companion/src/lib/decks.ts`

```ts
interface DeckEntry {
  name: string;
  qty: number;
  printing_id?: string;      // exact; also what fetches the art
  set_code?: string;         // the pair printed on the card, and the only
  collector_number?: string; // form that survives a plain text file
}
```

`entryKey()` decides when two slots are the same slot: printing id if there is
one, else name + set/number, else the bare name. That is what lets one card
sit in a deck twice as two different objects.

Rewritten around it: `parseDecklist` (captures the `(SET) NUM` suffix instead
of stripping it), `formatDecklist` (emits it **only** for slots that have one,
so a name-only deck round-trips byte for byte), `addToDeck`, `removeFromDeck`,
`deckSize`, `mergeCounts`, `shortfall`, `deckWarnings`, `wishlistFromDecks`,
`costToFinish`. New: `countByName`, `copiesOf`, `carryPrintings`,
`resolveSlots`, `pricesFromSlots`, `deckValue`, `printingLabel`.

**Copy limits count by NAME**, across printings. Four Lightning Bolts from
four sets is still four Lightning Bolts, and counting slots would have called
an illegal deck legal the moment someone picked their favourite art.

### The text box loses ids, so `carryPrintings` puts them back

The text box can carry a set and a number; it cannot carry a UUID. Without
this, one hand-edit would silently demote every exact slot in the deck to
set-and-number only, the shortfall would change, and nothing on screen would
say why. `save` runs the parsed list through `carryPrintings(parsed, previous)`
before storing.

### Storage: a third shape, added not migrated

`decklist_json` now holds `{v: 2, main: [entry], side: [entry]}`. The reader
still handles the bare `{name: count}` map and the `{main, side}` pair,
because **there are real decks on the user's phone in both**. A migration that
goes wrong loses a deck someone built; two extra branches in the reader is a
cheap price.

### Shortfall: exact slots settle first

A name-only slot is filled by any printing, as always. A slot naming a
printing is filled only by copies of that printing. Exact slots are settled
**first** and the copies they claim come out of the name pool too — otherwise
one physical card fills an exact slot and a loose slot at once, and the deck
reads as complete with a sleeve empty.

A slot carrying set+number but no id is matched by NAME. The phone's mirror
holds printing ids and not set codes, so there is genuinely nothing to
compare, and admitting that beats inventing a match. Opening the card in the
browser resolves the id, after which it matches exactly.

### `decks/resolve` — one route, three questions

`AppApi.resolve_deck_slots` + the phone allow-list entry. A deck screen asks
all three at the same moment, and a per-slot round trip over a tailnet is the
difference between a grid that appears and one that fills in over seconds.

* a **printing id** → its set, number and price;
* a **set and number** → back to an id (this is what heals a text round trip);
* a **bare name** → a representative printing, so it can be drawn and priced.
  Cheapest priced, falling back to newest unpriced —
  `CardDatabase.representative_printings_for_names`, batched at 400 like
  `cheapest_prices_for_names` beside it.

Unresolvable slots come back with `found: false` rather than being dropped: a
caller handed a shorter list than it sent has to work out which ones went
missing, and getting that wrong shows the wrong card's picture. The phone
matches replies **by index**, because the desktop answers with the
catalogue's spelling of the name.

`AppState.deckSlots` seeds from the local mirror first and always, so opening
a deck with no signal still shows every card you own. The desktop then fills
in what the mirror could not.

### Choosing a printing on the phone

`CardBrowser`'s preview already pages through printings; it now tracks which
page is showing and offers **two** adds:

* **Add any** — records a name. The default, and what an import is.
* **Add this printing (CMM 410)** — records the exact card on screen.

Explicit rather than inferred from the swipe. Letting the visible picture
decide would turn every add into an exact one the moment a card had more than
one printing, and nobody asked for that.

### The desktop

* `models.DeckEntry` gains `set_code` / `collector_number`, both defaulting to
  empty. **Nothing in the analysis engine reads them and nothing should.**
* `deck/parser.py` captures the suffix instead of discarding it, in both the
  `(SET) NUM` and `[ELD]` spellings, and strips `*F*` first so a foil marker
  cannot hide the collector number.
* `save_deck_version` writes a **`printings` sidecar** into `decklist_json`
  beside the existing name-keyed `cards`. Every consumer — diff, trends,
  impact, the eleven combo-aware layers, the analyst — reads `decklist`
  unchanged. Widening the name-keyed map would have rippled through all of
  them to answer a question none of them asks.
* `_refresh_wishlist_for_deck` settles exact slots first against
  `CollectionStore.owned_by_printing` (new), then loose slots by name.
* `wishlist_items` gains `set_code` / `collector_number` and a **wider unique
  index**. `_migrate_wishlist_printings` runs BEFORE the schema statements,
  like its two neighbours, because the schema creates an index over the new
  columns and on an older database that statement fails outright and the app
  will not open — a class of bug a fresh-database test suite cannot see. It
  also drops the old two-column index, which would otherwise still reject the
  second printing one deck wants.
* `builder.js` keys deck entries by SLOT, where a name-only slot's key is
  still the bare name — so every existing name lookup keeps working. Each deck
  row carries a printing chip (`any` / `CMM 410`) that opens an inline picker
  built from `get_card_printings`. `draftToDecklistText` emits the suffix for
  slots that have one.

### Deck value

`deckValue(entries, prices)` in `decks.ts`, surfaced under the deck title with
"still to buy" beside it. Prices are looked up by printing id first and by
lowercased name second. Unpriced cards are reported rather than folded in as
zero — a total that silently omits what it could not price looks
authoritative and is not.

---

## What did NOT change, on purpose

* **The analysis engine is still name-level.** Legality, combos, goldfishing,
  archetype detection, power level and brackets are facts about cards.
  Printings are a decoration ON a decklist, not a replacement for one.
* **Decks still do not sync.** They are phone-local; the desktop has its own.
  No new event kind was needed. `analyzeOnDesktop` now sends the sideboard as
  well as the maindeck, which it previously dropped.
* **`allocation.py` is untouched.** It answers "which physical copy is
  earmarked for this deck" and remains the opt-in refinement it always was.
* **The desktop builder holds one printing per card per zone.** The slot key
  supports two, but the picker binds one — for Commander, its default format,
  the distinction is moot. The phone has no such limit.

---

## Testing

```bash
PYTHONPATH=src python -m pytest tests/     # 1,591 (27 new: test_deck_printings.py)
cd companion && npm test                   # 379 (96 in decks.test.mjs)
cd companion && npx tsc --noEmit
```

The six tests the plan asked for first all exist, and all six pin a **silent**
failure — a merged pair of printings, a slot filled by the wrong card, an old
deck opening empty. None of them throw; all of them are wrong on a screen that
looks right.

`tests/test_apk_contents.py` gained four screen strings and the
`decks/resolve` route. It only passes against a freshly built release APK,
which is the point: a screen nothing navigates to is silently absent from the
build, however well it is tested.

---

## Standing context worth not rediscovering

* **Ship path**: bump `app.json` (version + `android.versionCode`),
  `src/lib/version.ts`, `package.json`, `android/app/build.gradle` — a test
  fails if they disagree. Build with `./gradlew assembleRelease` and
  `JAVA_HOME` set to Android Studio's JBR. Copy to
  `G:\My Drive\Densanon LLC\DensaDeck\`, hash-verify, delete the old one.
* **The desktop binary is stale the moment you change Python.** Rebuild with
  `scripts/build_desktop.py` and restart it, or the phone gets `unknown
  route` — and `decks/resolve` is a new route, so a stale binary means the
  visual grid falls back to the mirror and silently shows art only for cards
  you own.
* **One vertical scroller per screen.** Memory: `rn-one-scroller-per-screen`.
* **Layout still has no test coverage at all.** Every check asserts a string
  is in the bundle; none can tell whether it is visible. This remains the gap
  worth closing — a snapshot or render test — and the visual deck grid is new
  untested layout on top of it.
