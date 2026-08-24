# Densa Deck Companion — build plan

A native Android companion that browses collections, builds decks, swaps
cards, and runs the analyst tools **on the PC** with results shown on the
phone. Full offline editing, reconciled by two-way sync over the tailnet.

Decided with the user 2026-08-23:

| Question | Answer |
| --- | --- |
| App shape | Native Android (Expo / React Native) |
| Offline | Full offline editing with sync |
| Transport | Tailnet (Tailscale), and plain Wi-Fi on the same LAN |
| Brain | The PC. The phone never needs a model or the card catalogue to reason |

---

## The one rule everything else serves

**A card that exists must never stop existing because two devices were edited
apart.** This is an inventory of physical property; losing a rename is
survivable, losing cardboard is not. Every design choice below is downstream
of that.

---

## Why deltas, not snapshots

The obvious sync — "send the collection, newest wins" — silently destroys
work. Add a box on the phone at a shop while the PC is off, rebuild a deck on
the PC meanwhile, and one side's edits vanish on reunion with nothing to say
so.

Quantities are therefore never sent as totals. They are sent as **deltas**,
and deltas commute: `+2` on the phone and `+3` on the desktop merge to `+5`
whichever order they arrive in. There is no conflict to resolve because there
is no disagreement — both things happened. The collection already had the
right shape for this: `collection_events` has been an append-only log since
the cost-basis work.

Things that are genuinely documents rather than counters — a collection's
name, a deck's contents — use last-write-wins on a UTC timestamp, because
"which name did they mean" has no merge and the loss is cosmetic.

| Data | Merge strategy | Why |
| --- | --- | --- |
| Stack quantities | Additive deltas | Commutative; cannot lose cards |
| Collection membership | Delta pair (−N there, +N here) | A move is two quantity facts |
| Collection name / notes | LWW by `updated_at` | No sane merge; loss is cosmetic |
| Collection existence | Create wins over delete | Deleting is rarer than adding; an accidental resurrection is recoverable, a deletion is not |
| Deck contents | LWW by `updated_at` | A deck is a document, and half-merged decklists are worse than a lost edit |
| Deck existence | Tombstone with timestamp | Decks are explicitly managed; a delete is deliberate |

## Identity across devices

Nothing crossing the wire is a local autoincrement id — two offline devices
both mint `2` and then disagree about what it means.

* **Stacks** are addressed by their natural key (printing, finish, condition,
  language, location, collection uid). Both devices derive it independently
  and agree without coordination.
* **Collections and decks** carry a UUID minted at creation.
* **Every event** carries a UUID, so applying it twice is a no-op. A retried
  or duplicated sync cannot double-count.

## The exchange

```
phone                                    desktop
  |  POST /sync/pull  {since: cursor}  ->  |
  |  <- events the desktop has, phone lacks
  |  POST /sync/push  {events: [...]}  ->  |
  |  <- accepted count, new cursor
```

Each side keeps a watermark per peer. No central clock, no leader, and no
requirement that the two were ever online together before.

---

## Stages

Each stage ends green: full Python suite, the browser/DOM suite, and lint.

### Stage 1 — Sync foundation (desktop) ✅
- Device identity (stable UUID per install)
- `sync_events` log: event uid, device, seq cursor, kind, payload, timestamp
- Collection UUIDs + migration from autoincrement ids
- Apply logic, idempotent by event uid
- Pull/push with watermarks
- Tests: commutativity, idempotency, offline-both-sides, delete-vs-add races

### Stage 2 — Companion API surface
- Browse: collections, cards (paged, searchable, priced)
- Decks: list, read, save, delete, allocations
- Analyst: analyze, explain, rule 0, bracket, combos — PC computes
- Scoped allow-list on the bridge; nothing destructive reachable by accident
- Tests: every route, including what must NOT be exposed

### Stage 3 — Expo app scaffold
- Project, navigation, pairing by QR (reuses the existing token)
- Connectivity: tailnet address, LAN fallback, clear offline state
- Local SQLite mirror mirroring the desktop schema
- Tests: schema parity, pairing, reachability logic

### Stage 4 — Offline editing + sync engine (phone)
- Local writes emit events into the phone's own log
- Background push/pull, watermarks, retry
- Conflict surfaces shown honestly rather than hidden
- Tests: edit-while-offline, reconnect, duplicate delivery, clock skew

### Stage 5 — Analyst on the phone
- Request/response over the bridge, rendered natively
- Long-running jobs: progress, cancellation, no silent timeouts

### Stage 6 — Scanner in the app
- Camera → frames → desktop OCR (the pipeline already exists and is tuned)
- Reuses the lens picker findings: minimum focus distance is the real enemy

---

## Known constraints

* **Local APK builds are possible.** Checked rather than assumed: the Android
  SDK is installed (platforms 34-36, build-tools 34-36) and Android Studio
  ships a working JDK 17 at `C:\Program Files\Android\Android Studio\jbr`.
  The system `java` on PATH is a stub that does nothing, so `JAVA_HOME` has
  to point at the JBR. No cloud build service is needed.
* **iOS is out of scope** — the Mac/iOS builds belong to a separate team.
* The desktop must be awake to sync or to run the analyst. That is inherent
  to "the PC is the brain" and is the tradeoff for not shipping a model to
  the phone.
