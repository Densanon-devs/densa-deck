/**
 * Everything the screens need to know, without any screen knowing how.
 *
 * The UI is deliberately thin: React Native cannot be tested here without a
 * device, so anything that decides something lives in this file instead and is
 * covered by the Node suite. A screen should read as a description of what is
 * on it, not as logic.
 */

import {
  loadCameraSettings,
  saveCameraSettings,
} from './camera-settings.ts';
import type { CameraSettings } from './camera-settings.ts';
import { DesktopClient } from './client.ts';
import type { EndpointReport } from './client.ts';
import type { Pairing } from './client.ts';
import { identifyLocally } from './identify.ts';
import { deviceTextReader } from './ocr.ts';
import type { TextReader } from './ocr.ts';
import { stackKey } from './protocol.ts';
import { defaultFinish, identifyPhoto } from './scanner.ts';
import type { ScanResult } from './scanner.ts';
import type {
  BuiltDeck,
  CardDetail,
  CataloguePrinting,
  CatalogueSet,
  DeckResolveReply,
  DesktopDeck,
  DesktopDeckDetail,
  GroupManifest,
  GroupReview,
  SavedToPc,
  TierSnapshot,
  OverlapsReply,
  TagResult,
  ResolvedSlot,
  CardQuery,
  CardSearchReply,
  CollectionPage,
  CollectionsReply,
} from './protocol.ts';
import { DeckStore, entryKey, resolveSlots, wishlistFromDecks } from './decks.ts';
import type { Deck, DeckEntry, SlotFacts, WishlistRow } from './decks.ts';
import type { Via } from './reach.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from './store.ts';
import { SyncEngine } from './sync.ts';

export type Connection = 'connected' | 'offline' | 'unpaired' | 'unknown';

export interface AppSnapshot {
  connection: Connection;
  pendingEdits: number;
  lastSyncAt?: string;
  lastError?: string;
  /**
   * Which path the last exchange took.
   *
   * Worth surfacing rather than hiding: "connected" over Tailscale from the
   * sofa and "connected" over Wi-Fi in the same room are different enough
   * that someone debugging their own setup wants to know which happened.
   */
  via?: Via;
}

const LAST_SYNC_KEY = 'sync.last_at';
const SCAN_TARGET_KEY = 'scan.collection_uid';
// Where the index walk got to. Empty means finished — and, before the first
// pull, means "never started", which is why readiness checks the row count
// as well.
const CATALOGUE_CURSOR_KEY = 'catalogue.cursor';

export class AppState {
  private store: LocalStore;
  private engine: SyncEngine;
  private client: DesktopClient;
  private listeners = new Set<(s: AppSnapshot) => void>();
  private snapshot: AppSnapshot = {
    connection: 'unknown',
    pendingEdits: 0,
  };

  /** This phone's own decks, when one has been wired in. */
  private decks?: DeckStore;

  private readonly textReader: TextReader;

  constructor(store: LocalStore, engine: SyncEngine, client: DesktopClient,
              decks?: DeckStore, textReader: TextReader = deviceTextReader) {
    this.store = store;
    this.engine = engine;
    this.client = client;
    this.decks = decks;
    // Injected so the whole offline scan path is testable under Node, where
    // the native recogniser cannot exist.
    this.textReader = textReader;
  }

  /**
   * Identify a card without the PC.
   *
   * Reads the text on the device, then matches it against the index this
   * phone pulled down. Exact keys only — see `identify.ts` for why the
   * fuzzy half deliberately stayed on the desktop.
   *
   * Returns null when it cannot place the card, which is the signal to keep
   * the photo for the PC rather than to guess.
   */
  async identifyOffline(imageUri: string): Promise<{
    printing: { printing_id: string; name: string; set_code: string;
                collector_number: string };
    foilHint: boolean;
  } | null> {
    const { ready } = await this.catalogueReady();
    if (!ready) return null;
    const text = await this.textReader.read(imageUri);
    if (!text) return null;
    const out = await identifyLocally(text, this.store);
    const hit = out.candidates[0];
    // Only what it is CERTAIN of. Anything less is a photo for the PC, which
    // has the fuzzy matcher and a person in front of it.
    return out.autoAddable && hit
      ? { printing: hit, foilHint: out.identity.foilHint }
      : null;
  }

  /**
   * Pull the card index off the PC.
   *
   * Never bundled into the build. The index changes every time a set comes
   * out, and an app that shipped one would be wrong within weeks and could
   * only be fixed by shipping another app. The PC already has the real
   * catalogue and already keeps it current, so this is a copy of the four
   * fields a scan needs, taken on demand.
   *
   * Resumable by design: the walk is keyed on the last printing id, so a
   * pull interrupted by walking out of range picks up where it stopped
   * rather than starting the seven megabytes again.
   */
  async syncCatalogue(
    onProgress?: (done: number, total: number) => void,
  ): Promise<{ rows: number; total: number }> {
    let after = (await this.store.getMeta(CATALOGUE_CURSOR_KEY)) ?? '';
    let done = await this.store.catalogueSize();
    let total = done;
    for (;;) {
      const page = await this.client.call<{
        rows: Array<[string, string, string, string]>;
        next: string;
        total: number;
      }>('catalogue/page', { after, limit: 5000 });
      const rows = page.rows ?? [];
      total = page.total ?? total;
      if (rows.length) {
        await this.store.putCatalogue(rows);
        done += rows.length;
        onProgress?.(Math.min(done, total), total);
      }
      after = page.next ?? '';
      // The cursor is saved AFTER the rows it covers are written, so an
      // interrupted pull resumes from the last page that actually landed
      // rather than skipping one.
      await this.store.setMeta(CATALOGUE_CURSOR_KEY, after);
      if (!after) break;
    }
    return { rows: await this.store.catalogueSize(), total };
  }

  /** How much of the index this phone is holding. */
  async catalogueReady(): Promise<{ rows: number; ready: boolean }> {
    const rows = await this.store.catalogueSize();
    // A partial pull is not usable: the missing rows are exactly the cards
    // it would silently fail to identify, and "scanned it, nothing found"
    // reads as a bad photo rather than a half-downloaded index.
    const cursor = (await this.store.getMeta(CATALOGUE_CURSOR_KEY)) ?? '';
    return { rows, ready: rows > 0 && cursor === '' };
  }

  /**
   * Keep a photographed card until the PC can look at it.
   *
   * The phone cannot identify a card by itself — no OCR, no catalogue — so
   * out of range this is the only honest thing to do with a picture. The
   * lists it was headed for travel with it, because by the time it drains
   * you have moved on to another box.
   */
  async queueScan(
    image: string,
    collectionUid: string,
    alsoUids: string[] = [],
  ): Promise<void> {
    await this.store.queueScan({
      scan_uid: this.newUuid(),
      image,
      captured_at: new Date().toISOString(),
      collection_uid: collectionUid,
      also_uids: alsoUids,
    });
  }

  async queuedScans(): Promise<number> {
    return this.store.countPendingScans();
  }

  /**
   * Work through the queue now that the PC is there.
   *
   * Files only what the PC is CERTAIN of. Anything less waits for a human,
   * exactly as it would have live: a wrong card filed silently is worse
   * than no card, because you will not know to look for it — and that is
   * more true here, not less, since nobody was watching when it went in.
   *
   * One at a time and in the order they were scanned, so a queue that dies
   * halfway has filed a prefix rather than a scatter.
   */
  async drainScans(): Promise<{ filed: number; undecided: number; failed: number }> {
    let filed = 0;
    let undecided = 0;
    let failed = 0;
    for (const scan of await this.store.pendingScans()) {
      let reply: ScanResult;
      try {
        reply = await identifyPhoto(this.scanClient, scan.image);
      } catch {
        // The PC went away again. Stop rather than burn the rest of the
        // queue against a wall — they are still safe on disk.
        break;
      }
      const top = reply.candidates?.[0];
      if (reply.auto_addable && top) {
        await this.addCard({
          printing_id: top.printing_id,
          card_name: top.name,
          finish: defaultFinish(top, reply),
          collection_uid: scan.collection_uid,
          also_collection_uids: scan.also_uids,
        });
        await this.store.dropScan(scan.scan_uid);
        filed += 1;
      } else if (reply.candidates?.length) {
        await this.store.markScanTried(scan.scan_uid, 'Needs a decision');
        undecided += 1;
      } else {
        await this.store.markScanTried(
          scan.scan_uid,
          'Could not read this one',
        );
        failed += 1;
      }
    }
    return { filed, undecided, failed };
  }

  /** The oldest queued photo the PC could not decide, ready to be shown. */
  async reviewNextScan(): Promise<
    { scanUid: string; reply: ScanResult } | null
  > {
    const [scan] = await this.store.pendingScans();
    if (!scan) return null;
    return { scanUid: scan.scan_uid, reply: await identifyPhoto(this.scanClient, scan.image) };
  }

  /** A human decided. File it and let the photo go. */
  async fileQueuedScan(
    scanUid: string,
    candidate: { printing_id: string; name: string },
    finish: string,
  ): Promise<void> {
    const [scan] = (await this.store.pendingScans())
      .filter((s) => s.scan_uid === scanUid);
    await this.addCard({
      printing_id: candidate.printing_id,
      card_name: candidate.name,
      finish,
      collection_uid: scan?.collection_uid ?? '',
      also_collection_uids: scan?.also_uids ?? [],
    });
    await this.store.dropScan(scanUid);
  }

  /** Give up on one. The picture goes; nothing is filed. */
  async discardQueuedScan(scanUid: string): Promise<void> {
    await this.store.dropScan(scanUid);
  }

  /**
   * What a card has been worth.
   *
   * Asked of the desktop, which is the only side that records it — the phone
   * has no catalogue to price against. Whatever comes back is kept, and
   * whatever is kept is what answers when the desktop is away, which is when
   * somebody standing in a shop most wants to know whether a card has been
   * climbing.
   *
   * A card NAME is passed as well as a printing so a card you do not own
   * still has a series: a wishlist entry naming no printing is tracked at
   * whichever copy was cheapest each day, which only reads as a series when
   * asked about the card.
   */
  async priceHistory(printingId: string, cardName = ''): Promise<{
    points: Array<{ captured_on: string; price_usd: number | null }>;
    scope: string;
    cached: boolean;
  }> {
    const key = (printingId || cardName || '').trim().toLowerCase();
    try {
      const reply = await this.client.call<{
        points: Array<{ captured_on: string; price_usd: number | null }>;
        scope: string;
      }>('prices/history', {
        printing_id: printingId,
        card_name: cardName,
        finish: 'nonfoil',
        limit: 365,
      });
      const points = reply.points ?? [];
      // Cached under the key that was ASKED for, not the one that answered,
      // so the same question finds it again offline.
      await this.store.cachePricePoints(key, reply.scope ?? 'printing', points);
      // Read back rather than returned directly: the cache may hold days the
      // desktop's window no longer covers.
      return {
        points: await this.store.cachedPricePoints(key),
        scope: reply.scope ?? 'printing',
        cached: false,
      };
    } catch {
      return {
        points: await this.store.cachedPricePoints(key),
        scope: 'printing',
        cached: true,
      };
    }
  }

  /**
   * What this phone may do, as the DESKTOP sees it.
   *
   * The licence lives there, so the answer comes from there. The phone had
   * no tier concept at all, which made installing the companion a way around
   * the whole paywall.
   *
   * Cached after the first answer so every screen is not asking, and
   * refreshable — activating Pro on the desktop should reach the phone
   * without reinstalling it.
   *
   * Unknown reads as PRO. A phone out of range must not start hiding
   * features somebody has paid for, and the desktop refuses the routes
   * itself, so failing open here costs nothing but a button that explains
   * itself when pressed.
   */
  async tier(refresh = false): Promise<TierSnapshot> {
    if (!refresh && this._tier) return this._tier;
    try {
      this._tier = await this.client.call<TierSnapshot>('tier', {});
    } catch {
      this._tier = { tier: 'pro', is_pro: true, allowances: {} };
    }
    return this._tier;
  }

  private _tier?: TierSnapshot;

  /**
   * Save a deck edited HERE, and tell the desktop.
   *
   * The single door for a user edit, and the reason it exists: `deckChanged`
   * and `deckDeleted` were written, and nothing ever called them. Every
   * screen went straight to `DeckStore.save`, so a deck edited on the phone
   * was stored and never broadcast — and the sync tests drove the engine
   * directly, so they passed while this path sat disconnected.
   *
   * Deliberately NOT folded into `DeckStore.save`. The applier writes decks
   * too; if saving broadcast, applying a deck from the PC would send it
   * straight back and the two devices would hand it to each other forever.
   * The applier uses `upsertFromSync`, this uses `saveDeck`, and the
   * difference between them is which one is an edit.
   */
  async saveDeck(deck: Deck): Promise<void> {
    if (!this.decks) return;
    await this.decks.save(deck);
    await this.engine.recordDeckUpsert(deck);
  }

  /** Delete a deck here, and tell the desktop. */
  async removeDeck(deckId: string): Promise<void> {
    if (!this.decks) return;
    await this.decks.remove(deckId);
    await this.engine.recordDeckDelete(deckId);
  }

  /**
   * Log a game here, at the table, and tell the PC.
   *
   * Written locally FIRST so the record is right even with no desktop in
   * range — which is the whole point of logging on a phone. The event waits
   * in the outbox until something is reachable.
   */
  async logGame(deckId: string, result: string, options: {
    versionNumber?: number;
    opponent?: string;
    notes?: string;
  } = {}): Promise<string> {
    const gameUid = this.newUuid();
    const playedAt = new Date().toISOString();
    if (this.decks) {
      await this.decks.recordGame({
        game_uid: gameUid,
        deck_id: deckId,
        version_number: options.versionNumber ?? 0,
        result,
        opponent: options.opponent ?? '',
        notes: options.notes ?? '',
        played_at: playedAt,
      });
    }
    await this.engine.recordDeckGame({
      deck_id: deckId,
      game_uid: gameUid,
      result,
      version_number: options.versionNumber ?? 0,
      opponent: options.opponent ?? '',
      notes: options.notes ?? '',
      played_at: playedAt,
    });
    return gameUid;
  }

  async forgetGame(deckId: string, gameUid: string): Promise<void> {
    if (this.decks) await this.decks.forgetGame(gameUid);
    await this.engine.recordDeckGame({
      deck_id: deckId, game_uid: gameUid, removed: true,
    });
  }

  /** The uuid source the engine was built with, so ids look the same. */
  private newUuid(): string {
    return this.engine.mintUuid();
  }

  subscribe(listener: (s: AppSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => this.listeners.delete(listener);
  }

  private emit(patch: Partial<AppSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener(this.snapshot);
  }

  /**
   * Exchange with the desktop and describe what happened.
   *
   * Offline is reported as a state, not an error. A companion that shouts
   * every time it cannot reach home would spend most of its life shouting,
   * and the edits are safe either way.
   */
  async sync(rounds = 0): Promise<AppSnapshot> {
    const outcome = await this.engine.sync();
    const pending = await this.engine.pending();

    const via = this.client.via;

    if (outcome.unpaired) {
      this.emit({ connection: 'unpaired', pendingEdits: pending,
                  lastError: outcome.error, via: null });
    } else if (outcome.offline) {
      this.emit({ connection: 'offline', pendingEdits: pending,
                  lastError: undefined, via: null });
    } else if (outcome.ok) {
      const now = new Date().toISOString();
      await this.store.setMeta(LAST_SYNC_KEY, now);
      this.emit({ connection: 'connected', pendingEdits: pending,
                  lastSyncAt: now, lastError: undefined, via });
    } else {
      this.emit({ connection: 'connected', pendingEdits: pending,
                  lastError: outcome.error, via });
    }

    // The desktop had more than one round could carry; keep going rather than
    // leaving the phone quietly out of date.
    //
    // BOUNDED, and that bound is not a formality. This was an unguarded
    // recursion on a flag the desktop computes, so a desktop that said "more"
    // without the cursor advancing meant a pull-to-refresh that never
    // returned and a spinner that never stopped. The cause of that is fixed
    // on the desktop; the reason it was FOREVER rather than a slow sync was
    // here, and a phone must not be able to hang on what a peer tells it.
    //
    // Twenty rounds is 10,000 events at the desktop's page size — far past
    // any real backlog — so hitting this is a bug elsewhere, not a big
    // collection. Stopping leaves the phone partly caught up, which is the
    // normal state between syncs and self-corrects on the next one.
    if (outcome.more && rounds < 20) return this.sync(rounds + 1);
    return this.snapshot;
  }

  /**
   * Throw away the mirror and ask the PC for all of it again.
   *
   * For a phone whose copy has drifted and cannot right itself. A pulled
   * event is remembered by uid so it is never applied twice; if one was ever
   * recorded without being applied — a force-quit mid-sync is enough — the
   * phone skips it forever and the cards it described never arrive, however
   * many times you pull to refresh.
   *
   * Safe to press: it discards only what came FROM the desktop. Edits made
   * here that have not been sent are kept and go first, so the fresh copy
   * includes them rather than undoing them.
   */
  async rebuildFromDesktop(): Promise<AppSnapshot> {
    await this.store.forgetDesktopState(this.engine.deviceId);
    return this.sync();
  }

  /**
   * The camera levers, kept between visits.
   *
   * Screens do not reach into the store; finding a zoom that focuses on your
   * cards and losing it every time the tab changes is worse than not having
   * the control at all.
   */
  async cameraSettings(): Promise<CameraSettings> {
    return loadCameraSettings(this.store);
  }

  async rememberCameraSettings(settings: CameraSettings): Promise<void> {
    await saveCameraSettings(this.store, settings);
  }

  /**
   * Which collection the scanner files into.
   *
   * Kept, because a scanning session is one shelf at a time — a target that
   * reset to the default whenever the tab changed would quietly scatter half
   * a box into the wrong place.
   */
  async scanTarget(): Promise<string> {
    const stored = await this.store.getMeta(SCAN_TARGET_KEY);
    if (!stored) return DEFAULT_COLLECTION_UID;
    // A collection deleted on the desktop must not strand the scanner
    // pointing at something that no longer exists.
    const known = await this.store.listCollections();
    return known.some((c) => c.collection_uid === stored)
      ? stored
      : DEFAULT_COLLECTION_UID;
  }

  async rememberScanTarget(uid: string): Promise<void> {
    await this.store.setMeta(SCAN_TARGET_KEY, uid);
  }

  async refreshPending(): Promise<void> {
    this.emit({ pendingEdits: await this.engine.pending() });
  }

  /**
   * How many edits are waiting for the desktop.
   *
   * Exposed so a screen that ASKS the desktop something can push first. The
   * subscription carries the same number, but a screen loading for the first
   * time has not been told anything yet — and that is exactly when it would
   * show the desktop's stale answer.
   */
  async pendingCount(): Promise<number> {
    return this.engine.pending();
  }

  // ------------------------------------------------------------- reading
  //
  // Reads come from the LOCAL mirror, always. Going to the desktop for a list
  // the phone already has would make browsing fail the moment the tailnet
  // dropped, which is the situation the companion exists for.

  async collections() {
    return this.store.listCollections();
  }

  async cards(collectionUid?: string, search?: string) {
    return this.store.listStacks(collectionUid, search);
  }

  async totals() {
    return { cards: await this.store.totalCards() };
  }

  // ------------------------------------------------------------- writing
  //
  // Writes go to the local mirror AND the local log together, so an edit made
  // with no signal is both visible immediately and remembered for the desktop.

  /** File a card into a collection. Works offline. */
  async addCard(card: {
    printing_id: string;
    card_name: string;
    finish?: string;
    condition?: string;
    collection_uid?: string;
    oracle_id?: string;
    location?: string;
    quantity?: number;
    /**
     * Further lists to TAG it into, beyond the one it is filed in.
     *
     * Filed once, tagged many. Filing it into each would mint a separate
     * stack per list and you would own four of a card you scanned once —
     * a stack is keyed by the collection it lives in.
     */
    also_collection_uids?: string[];
  }): Promise<void> {
    const stack = {
      printing_id: card.printing_id,
      card_name: card.card_name,
      finish: card.finish ?? 'nonfoil',
      condition: card.condition ?? 'NM',
      language: 'en',
      location: card.location ?? '',
    };
    await this.engine.editQuantity({
      ...stack,
      oracle_id: card.oracle_id ?? '',
      collection_uid: card.collection_uid ?? '',
      reason: 'phone-scan',
      delta: card.quantity ?? 1,
    });

    // Tags go in as their own events, addressed by the card's natural key,
    // so a box scanned with no signal still lands in the right lists once
    // the phone is back in range.
    //
    // Only on the way IN. Taking a copy back out (delta < 0) must not
    // untag the stack: the other copies are still in those lists, and a
    // slip of the finger should not quietly empty them.
    const delta = card.quantity ?? 1;
    if (delta > 0) {
      // The local key includes WHERE it is filed — that is what makes two
      // copies of one card in two collections two stacks — so it cannot be
      // built from the natural key the sync event carries.
      const key = stackKey({ ...stack, collection_uid: card.collection_uid ?? '' });
      for (const uid of card.also_collection_uids ?? []) {
        if (!uid || uid === card.collection_uid) continue;
        // Written locally AND queued. Queuing alone would leave the card
        // looking untagged until a sync round-trip, which is exactly the
        // thing that has not happened yet when you are scanning a box in
        // somebody's garage.
        await this.store.addMembership(key, uid);
        await this.engine.recordMembership(stack, uid, true);
      }
    }
    await this.refreshPending();
  }

  /** Take a card back out. Also works offline. */
  async removeCard(card: {
    printing_id: string;
    card_name: string;
    finish?: string;
    condition?: string;
    collection_uid?: string;
    quantity?: number;
  }): Promise<void> {
    await this.addCard({ ...card, quantity: -(card.quantity ?? 1) });
  }

  /**
   * Delete a collection. The cards stay.
   *
   * Only the grouping goes — the desktop can also discard the copies, and
   * that is deliberately not reachable from here. It is irreversible, and a
   * mis-tap on a handset is not the place for it.
   *
   * Needs the desktop, unlike almost everything else: deleting locally and
   * telling the PC later would leave a window where a card is filed under a
   * collection that no longer exists on one device.
   */
  async deleteCollection(collectionUid: string): Promise<void> {
    await this.client.call('collection/delete', {
      collection_uid: collectionUid,
    });
    await this.sync();
  }

  async renameCollection(collectionUid: string, name: string): Promise<void> {
    await this.client.call('collection/rename', {
      collection_uid: collectionUid,
      name,
    });
    await this.sync();
  }

  async newCollection(name: string): Promise<string> {
    const uid = await this.engine.createCollection(name);
    await this.refreshPending();
    return uid;
  }

  /**
   * Scanning needs the desktop's OCR, so the raw client is exposed for it.
   *
   * Deliberately narrow: a screen reaching into the client for anything the
   * app already has a method for would be reaching around the offline-first
   * rule, and browse/edit must never depend on the network.
   */
  /**
   * What one card is and does.
   *
   * Needs the desktop: the catalogue is 34,000 cards and the phone holds a
   * mirror of what you OWN, not of every card in Magic. The art does not come
   * through here — that is a Scryfall URL the phone loads directly — so this
   * failing costs the rules text and nothing else.
   */
  /** Every set in the catalogue, newest first. Needs the desktop. */
  async sets(): Promise<{ sets: CatalogueSet[] }> {
    return this.client.call<{ sets: CatalogueSet[] }>('cards/sets', {});
  }

  /**
   * Every printing of one card.
   *
   * Needs the desktop: the phone mirrors what you own, and the point here is
   * the printings you do not.
   */
  async printingsFor(cardName: string): Promise<{ printings: CataloguePrinting[] }> {
    return this.client.call<{ printings: CataloguePrinting[] }>(
      'cards/printings',
      { card_name: cardName },
    );
  }

  /**
   * Which picture each slot in a deck shows, and what a copy costs.
   *
   * Answers from the phone's own mirror FIRST and always, so opening a deck
   * with no signal still shows the cards you own rather than a grid of grey
   * rectangles. The desktop is then asked to fill in everything the mirror
   * could not — every card you have never owned, and every slot that came
   * back from the text box carrying a set and number but no id.
   *
   * A desktop that is away costs detail, never the screen. That is the whole
   * shape of this app.
   */
  async deckSlots(entries: DeckEntry[]): Promise<Record<string, SlotFacts>> {
    const owned = await this.store.listStacks();
    if (!entries.length) return {};

    let answered: ResolvedSlot[] = [];
    try {
      const reply = await this.client.call<DeckResolveReply>('decks/resolve', {
        slots: entries.map((entry) => ({
          name: entry.name,
          printing_id: entry.printing_id ?? '',
          set_code: entry.set_code ?? '',
          collector_number: entry.collector_number ?? '',
        })),
      });
      answered = reply.slots ?? [];
      // Kept, so the next time the desktop is out of range this deck still
      // has its pictures, its prices and its colours. The cache warms simply
      // by using the app while it is in range.
      await this.store.cacheSlotFacts(
        entries.map((entry, index) => ({
          slot_key: entryKey(entry),
          ...(answered[index] ?? {}),
        })).filter((row) => row.printing_id || row.color_identity),
      );
    } catch {
      // Offline, or the desktop is asleep. The mirror answers for everything
      // you OWN — most of a deck you are holding — and the cache answers for
      // the rest, so a deck opened out of range looks the same as one opened
      // at a desk rather than a grid of grey rectangles with no total.
      const remembered = await this.store.cachedSlotFacts();
      answered = entries.map((entry) => {
        const hit = remembered.get(entryKey(entry));
        if (!hit) return undefined as unknown as ResolvedSlot;
        return {
          name: entry.name,
          printing_id: hit.printing_id,
          set_code: hit.set_code,
          collector_number: hit.collector_number,
          price_usd: hit.price_usd,
          color_identity: hit.color_identity,
          type_line: hit.type_line,
          found: Boolean(hit.printing_id),
        } as ResolvedSlot;
      });
    }
    return resolveSlots(entries, owned, answered);
  }

  /**
   * Put a card you ALREADY OWN into a group.
   *
   * The scanner's second mode, and the difference matters: `addCard` files a
   * new copy, which is right when entering cards you have just acquired and
   * wrong when walking a pile you own picking out a bundle. There, a second
   * copy is not a tag — it is a counting error you will not notice for
   * months.
   *
   * Needs the desktop, unlike almost everything else here, because the answer
   * depends on which stacks exist and the phone mirrors quantities rather
   * than owning that decision. It fails honestly instead of guessing.
   */
  async tagIntoGroup(
    printingId: string,
    collectionUid: string,
    finish = '',
  ): Promise<TagResult> {
    return this.client.call<TagResult>('group/tag-scanned', {
      printing_id: printingId,
      collection_uid: collectionUid,
      finish,
    });
  }

  /** Answer the "you own this two ways" question by naming the stack. */
  async tagStack(itemId: number, collectionUid: string): Promise<TagResult> {
    return this.client.call<TagResult>('group/tag-item', {
      item_id: itemId,
      collection_uid: collectionUid,
    });
  }

  /** Take a stack back out of a group. The card itself is untouched. */
  async untagStack(itemId: number, collectionUid: string): Promise<void> {
    await this.client.call('group/untag-item', {
      item_id: itemId,
      collection_uid: collectionUid,
    });
  }

  async cardDetail(printingId: string, cardName: string): Promise<CardDetail> {
    return this.client.call<CardDetail>('cards/detail', {
      printing_id: printingId,
      card_name: cardName,
    });
  }

  /**
   * Cards in more than one list.
   *
   * Needs the desktop: the counting is over every relationship between
   * collections, and the phone mirrors what you OWN rather than how the
   * lists overlap.
   */
  /**
   * Put a card on the wishlist by hand.
   *
   * Deliberately possible for a card you have never seen and do not own: the
   * catalogue is every card in Magic, and "things I want" is a list about
   * cards, not about the collection.
   */
  async wishlistAdd(
    cardName: string,
    quantity = 1,
    printing?: { set_code?: string; collector_number?: string },
  ): Promise<void> {
    await this.client.call('wishlist/add', {
      card_name: cardName,
      quantity,
      // Naming a printing is a different want from wanting the card, and it
      // decides what gets tracked: a name-only wish is priced at whichever
      // copy is cheapest each day, which is the wrong answer for somebody
      // watching one particular version.
      set_code: printing?.set_code ?? '',
      collector_number: printing?.collector_number ?? '',
    });
  }

  /** Which lists a stack is in, from the phone's own mirror. */
  async listsFor(stackKey: string): Promise<string[]> {
    return this.store.membershipsFor(stackKey);
  }

  /**
   * Put a card in a list, or take it out of one.
   *
   * Applied locally first so it is visible with no signal, and logged for the
   * desktop. Adding never removes from another list; removing never removes
   * the card.
   */
  async setListMembership(
    stack: {
      stack_key: string;
      printing_id: string;
      card_name: string;
      finish: string;
      condition: string;
      language: string;
      location: string;
    },
    collectionUid: string,
    member: boolean,
  ): Promise<void> {
    if (member) await this.store.addMembership(stack.stack_key, collectionUid);
    else await this.store.removeMembership(stack.stack_key, collectionUid);
    await this.engine.recordMembership(stack, collectionUid, member);
    await this.refreshPending();
  }

  async overlaps(): Promise<OverlapsReply> {
    return this.client.call<OverlapsReply>('overlaps', {});
  }

  get scanClient(): DesktopClient {
    return this.client;
  }

  /**
   * Why it says Offline.
   *
   * One word was standing in for a dozen different problems, each with a
   * different fix. This reports what every address actually did.
   */
  async diagnose(): Promise<EndpointReport[]> {
    return this.client.diagnose();
  }

  // ------------------------------------------- things only the desktop knows

  /**
   * Ask the desktop to think about a deck.
   *
   * This is the one class of operation with no offline answer: the analysis
   * needs the card catalogue and the combo database, neither of which belongs
   * on a phone. Failing honestly is better than a stale cached verdict.
   */
  async analyze(decklistText: string, name = 'Deck') {
    return this.client.call('analyst/analyze', {
      decklist_text: decklistText,
      name,
    });
  }

  /**
   * Make a deck out of one collection, using only cards in it.
   *
   * Needs the desktop: the pool has to be judged against the whole catalogue
   * for colour identity and legality, which is not something a phone carries.
   * The DECK is arithmetic though, not a model — so this answers the same way
   * twice and works whether or not an analyst is loaded.
   */
  async buildFromCollection(
    collectionUid: string,
    format = 'commander',
    commander = '',
  ): Promise<BuiltDeck> {
    return this.client.call<BuiltDeck>('group/build-deck', {
      collection_uid: collectionUid,
      format,
      commander,
    });
  }

  /**
   * The decks saved on the PC.
   *
   * Separate from `DeckStore`, which is this phone's own decks. The two are
   * genuinely different sets — the desktop's are versioned and analysed, the
   * phone's are built in a shop — and the bridge has been able to list these
   * all along with nothing on the phone asking.
   */
  async desktopDecks(): Promise<DesktopDeck[]> {
    const reply = await this.client.call<{ decks: DesktopDeck[] }>(
      'decks/list', {},
    );
    return reply.decks ?? [];
  }

  /**
   * Push a deck from this phone up to the PC.
   *
   * The other half of copying one down, and the half that was missing: a
   * deck built standing in a shop lived on the phone and nowhere else, which
   * is the one place it is least useful afterwards. Saved on the PC it gets a
   * version, static analysis, and everything else the desktop keeps.
   *
   * The deck keeps its id, so saving the same deck twice is a new VERSION of
   * it rather than a second deck with the same name.
   */
  async saveDeckToDesktop(
    deckId: string,
    name: string,
    decklistText: string,
    format = '',
  ): Promise<SavedToPc> {
    return this.client.call<SavedToPc>('decks/save', {
      deck_id: deckId,
      name,
      decklist_text: decklistText,
      format: format || null,
    });
  }

  /** What is in a group, what it is worth, and what your decks still want. */
  async reviewGroup(collectionUid: string): Promise<GroupReview> {
    return this.client.call<GroupReview>('group/review', {
      collection_uid: collectionUid,
    });
  }

  /** A group as a manifest — the thing you actually hand a buyer. */
  async exportGroup(
    collectionUid: string,
    format: 'csv' | 'decklist' | 'json' = 'decklist',
  ): Promise<GroupManifest> {
    return this.client.call<GroupManifest>('group/export', {
      collection_uid: collectionUid,
      format,
    });
  }

  /**
   * You bought it: file the card and take it off every list that wanted it.
   *
   * The two halves belong together, which is why this is one call rather than
   * an add followed by a wishlist edit — filing it without clearing the list
   * leaves you shopping for a card that is already in your bag.
   *
   * Needs a printing, because a copy of a card is a copy of some PRINTING and
   * the collection is keyed that way. A wishlist row that named one supplies
   * it; one that did not gets a representative resolved first.
   */
  async acquireFromWishlist(
    printingId: string,
    cardName: string,
    quantity = 1,
  ): Promise<void> {
    await this.client.call('wishlist/acquire', {
      printing_id: printingId,
      card_name: cardName,
      quantity,
    });
    // The card is now owned on the PC; pull that down so the phone agrees
    // rather than showing it as still wanted until the next refresh.
    await this.sync();
  }

  /** Stop wanting a card, whichever printings were listed. */
  async removeFromWishlist(cardName: string): Promise<void> {
    await this.client.call('wishlist/remove', { card_name: cardName });
  }

  async desktopDeck(deckId: string): Promise<DesktopDeckDetail> {
    return this.client.call<DesktopDeckDetail>('decks/get', {
      deck_id: deckId,
    });
  }

  async combos(decklistText: string) {
    return this.client.call('analyst/combos', { decklist_text: decklistText });
  }

  async rule0(decklistText: string) {
    return this.client.call('analyst/rule0', { decklist_text: decklistText });
  }

  /**
   * What your decks want that you do not own.
   *
   * Derived here from local decks and the local mirror rather than fetched,
   * so it answers with no signal — which is the situation it exists for.
   */
  async wishlist(decks: Deck[]): Promise<WishlistRow[]> {
    return wishlistFromDecks(decks, await this.store.listStacks());
  }

  /**
   * Search the whole card catalogue, not just what you own.
   *
   * This is how a card you do not have gets into a deck. It needs the
   * desktop: 34k oracle cards and 107k printings are not going on a phone,
   * and a search that quietly fell back to the local mirror would answer a
   * different question from the one asked — "what do I own" instead of "what
   * exists" — which is worse than saying it cannot.
   */
  async searchCards(query: CardQuery = {}): Promise<CardSearchReply> {
    return this.client.call<CardSearchReply>('cards/search', { query });
  }

  /** Live figures from the desktop, for when it is reachable. */
  async desktopCollections(): Promise<CollectionsReply> {
    return this.client.call<CollectionsReply>('collections', {});
  }

  async desktopCards(query: Record<string, unknown> = {}): Promise<CollectionPage> {
    return this.client.call<CollectionPage>('collection/list', { query });
  }
}

/** Wire up a store, an engine and a client into something the UI can hold. */
export function buildAppState(
  store: LocalStore,
  pairing: Pairing,
  device: string,
  uuid: () => string,
  fetchImpl?: typeof fetch,
  decks?: DeckStore,
  textReader?: TextReader,
): AppState {
  const client = new DesktopClient(pairing, fetchImpl ? { fetchImpl } : {});
  // The engine needs the deck store to APPLY deck events; without one it
  // remembers them and does nothing, which is recoverable but means a deck
  // built on the PC never appears here.
  const engine = new SyncEngine(store, client, device, uuid, decks);
  return new AppState(store, engine, client, decks, textReader);
}
