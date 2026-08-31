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
import { DesktopClient, Unreachable } from './client.ts';
import type { EndpointReport } from './client.ts';
import type { Pairing } from './client.ts';
import { identifyLocally } from './identify.ts';
import { downloadedChunks } from './bulk-download.ts';
import { chooseSource } from './index-source.ts';
import type { IndexSource } from './index-source.ts';
import { deviceTextReader } from './ocr.ts';
import { bulkSources, readBulk, toOracleRow, toPrintingRow } from './scryfall.ts';
import type { BulkSource } from './scryfall.ts';
import { RepeatGuard } from './scanner.ts';
import type { TextReader } from './ocr.ts';
import { stackKey } from './protocol.ts';
import type { CatalogueRow, OracleRow } from './store.ts';
import { defaultFinish, identifyPhoto } from './scanner.ts';
import type { ScanResult } from './scanner.ts';
import type {
  BuiltDeck,
  CardDetail,
  CataloguePrinting,
  CatalogueCard,
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
// The oracle walk's own cursor. Separate from the printing one because the
// two are different lengths and either can be interrupted alone.
const ORACLE_CURSOR_KEY = 'oracle.cursor';

/**
 * What free keeps, for a phone with no desktop to ask.
 *
 * Mirrors `tiers.py`. Duplicated rather than fetched because the whole
 * point is that there is nothing to fetch from — and a standalone phone
 * that guessed "unlimited" would quietly hand out what the desktop sells.
 */
const FREE_ALLOWANCES = {
  saved_decks: 3,
  suggestions: 2,
  sets_tracked: 3,
  collections: 3,
};

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

  /** Running with no desktop, ever — not merely out of range right now. */
  private readonly standalone: boolean;

  /** Plain network access, for the one thing that is not the desktop. */
  private readonly plainFetch: typeof fetch;

  constructor(store: LocalStore, engine: SyncEngine, client: DesktopClient,
              decks?: DeckStore, textReader: TextReader = deviceTextReader,
              standalone = false, plainFetch?: typeof fetch) {
    // Scryfall is reached DIRECTLY, not through the desktop client — that
    // one carries a pairing token to a machine that may not exist.
    this.plainFetch = plainFetch ?? fetch;
    this.standalone = standalone;
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
        rows: CatalogueRow[];
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

  /**
   * Mana value per printing, for sorting a collection by curve.
   *
   * From the index this phone pulled off the PC, so it answers with no
   * signal — which is the situation the whole index exists for. A printing
   * the index does not cover has no mana value, and `sortCards` sinks it
   * rather than treating it as zero.
   */
  async manaValues(): Promise<Map<string, number>> {
    return this.store.manaValues();
  }

  /**
   * Pull what each CARD is, so browsing works with no PC.
   *
   * Separate walk from the printing index because they answer different
   * questions and are different sizes; same resumable shape.
   */
  async syncOracle(
    onProgress?: (done: number, total: number) => void,
  ): Promise<{ rows: number; total: number }> {
    let after = (await this.store.getMeta(ORACLE_CURSOR_KEY)) ?? '';
    let done = await this.store.oracleSize();
    let total = done;
    for (;;) {
      const page = await this.client.call<{
        rows: OracleRow[]; next: string; total: number;
      }>('oracle/page', { after, limit: 3000 });
      const rows = page.rows ?? [];
      total = page.total ?? total;
      if (rows.length) {
        await this.store.putOracle(rows);
        done += rows.length;
        onProgress?.(Math.min(done, total), total);
      }
      after = page.next ?? '';
      await this.store.setMeta(ORACLE_CURSOR_KEY, after);
      if (!after) break;
    }
    return { rows: await this.store.oracleSize(), total };
  }

  /**
   * Get the card index, from whichever source is available.
   *
   * The PC when it is there and Scryfall when it is not — a preference,
   * not a fallback. The PC is enormously faster (7 MB over the LAN in
   * under a second, against 74 MB from the internet that has to be
   * inflated and parsed here) but it must never be REQUIRED, because a
   * phone-only owner has no PC to offload to and scanning is the first
   * thing they try.
   *
   * Both indexes, because they answer different questions: which printing
   * is which, and what each card does.
   */
  async fetchIndex(
    onProgress?: (p: { source: IndexSource; done: number; total: number;
                       stage: string }) => void,
    // The bytes of a bulk file, as a seam. The real one reaches a native
    // filesystem that cannot exist under Node, and the cursor handling
    // around it is worth testing.
    chunks: (url: string) => AsyncIterable<Uint8Array> = downloadedChunks,
  ): Promise<{ printings: number; oracle: number; source: IndexSource }> {
    const source = chooseSource({
      desktopAvailable: await this.desktopAvailable(),
    });

    if (source === 'desktop') {
      const printings = await this.syncCatalogue((done, total) =>
        onProgress?.({ source, done, total, stage: 'printings' }));
      const oracle = await this.syncOracle((done, total) =>
        onProgress?.({ source, done, total, stage: 'cards' }));
      return { printings: printings.rows, oracle: oracle.rows, source };
    }

    const sources = await bulkSources(this.plainFetch);
    // Printings first: it is the one scanning turns on, so a download
    // interrupted halfway still leaves the scanner working.
    const printings = await this.pullBulk(
      sources.default_cards, toPrintingRow,
      (rows) => this.store.putCatalogue(rows as CatalogueRow[]),
      CATALOGUE_CURSOR_KEY, chunks,
      (done, total) => onProgress?.({ source, done, total, stage: 'printings' }),
    );
    const oracle = await this.pullBulk(
      sources.oracle_cards, toOracleRow,
      (rows) => this.store.putOracle(rows as OracleRow[]),
      ORACLE_CURSOR_KEY, chunks,
      (done, total) => onProgress?.({ source, done, total, stage: 'cards' }),
    );
    return { printings, oracle, source };
  }

  /** One bulk file, inflated in pieces and written in batches. */
  private async pullBulk<T>(
    source: BulkSource,
    pick: (card: Record<string, unknown>) => T | null,
    write: (rows: T[]) => Promise<void>,
    cursorKey: string,
    chunks: (url: string) => AsyncIterable<Uint8Array>,
    onProgress: (done: number, total: number) => void,
  ): Promise<number> {
    const rows = await readBulk(
      chunks(source.url), pick, write,
      (p) => onProgress(p.bytes, source.bytes || p.bytes),
    );
    // The cursor belongs to the DESKTOP walk, which is resumable page by
    // page. A bulk file is all-or-nothing, so finishing one means the walk
    // has nothing left to resume — and leaving a stale cursor behind would
    // make `catalogueReady` call a complete index half-fetched.
    await this.store.setMeta(cursorKey, '');
    return rows;
  }

  /** Whether a desktop is paired AND answering right now. */
  private async desktopAvailable(): Promise<boolean> {
    if (this.standalone) return false;
    try {
      await this.client.call('tier', {});
      return true;
    } catch {
      return false;
    }
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
  async drainScans(): Promise<{
    filed: number; undecided: number; failed: number; repeats: number;
  }> {
    let filed = 0;
    let undecided = 0;
    let failed = 0;
    let repeats = 0;
    /**
     * The same rule the live scanner uses, applied to the queue.
     *
     * A card that would not file has nothing visible happen, so people
     * photograph it again — and again — and every one of those was a
     * separate row that filed a separate copy. One card became five.
     *
     * Live, `RepeatGuard` answers this with "same name inside four seconds
     * is the same card still in frame". Here the names arrive late, but the
     * queue stored WHEN each photo was taken, so the identical rule can be
     * applied to the identical question. Fed capture times, not drain
     * times: drained back to back they are all milliseconds apart, and
     * everything after the first would vanish — including four real copies
     * of a card somebody actually scanned four times.
     */
    const guard = new RepeatGuard();
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
        const when = Date.parse(scan.captured_at);
        const decision = guard.consider(
          top.name, Number.isFinite(when) ? when : 0);
        if (!decision.file) {
          // The same card, photographed again because nothing looked like
          // it happened. Drop the photo — filing it is the bug.
          await this.store.dropScan(scan.scan_uid);
          repeats += 1;
          continue;
        }
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
    return { filed, undecided, failed, repeats };
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
    // A phone that has never met a desktop is FREE, and there is nothing
    // uncertain about it: nobody bought Pro for it, there is no licence to
    // honour, and no desktop will ever answer. Falling back to Pro here
    // handed a standalone phone unlimited everything.
    if (this.standalone) {
      this._tier = {
        tier: 'free',
        is_pro: false,
        allowances: FREE_ALLOWANCES,
      };
      return this._tier;
    }
    try {
      this._tier = await this.client.call<TierSnapshot>('tier', {});
    } catch {
      // PAIRED but out of range is a different question, and the answer
      // stays generous: a wifi drop must not lock a paying user out of
      // features they own. The last answer the desktop gave wins if there
      // is one; Pro is the assumption only when there is not.
      this._tier ??= { tier: 'pro', is_pro: true, allowances: {} };
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
  /**
   * Organising is a phone job, and it does not need the PC.
   *
   * These went through the desktop while `newCollection` did not, so you
   * could MAKE a list standing over a box and then not rename or delete it
   * until you were back at the machine. The engine has always been able to
   * do both locally and tell the PC afterwards; this was simply calling
   * the wrong door.
   */
  async deleteCollection(collectionUid: string): Promise<void> {
    await this.engine.deleteCollection(collectionUid);
    await this.refreshPending();
    // Best-effort push. Being out of range is a state, not a failure — the
    // event is on disk either way.
    await this.sync().catch(() => undefined);
  }

  async renameCollection(collectionUid: string, name: string): Promise<void> {
    await this.engine.renameCollection(collectionUid, name);
    await this.refreshPending();
    await this.sync().catch(() => undefined);
  }

  /**
   * Make a grouping, if this tier has room for one.
   *
   * Checked HERE rather than only on the desktop, because groups are made
   * locally and offline: a limit only the PC knows is one the phone
   * discovers by having a sync rejected, long after the user made the group
   * and put forty cards in it.
   *
   * The allowance comes from the last tier snapshot the phone was given, so
   * an unpaired or never-synced phone has no number and is not restricted —
   * refusing on a value it has never been told would lock a paying user out
   * of their own phone.
   */
  async newCollection(name: string): Promise<string> {
    const allowed = this._tier?.allowances?.collections;
    if (typeof allowed === 'number' && allowed >= 0) {
      // The main collection is made for you and cannot be opted out of, so
      // it does not spend a slot — counting it would quietly make three
      // into two.
      const mine = (await this.store.listCollections())
        .filter((c) => c.collection_uid !== DEFAULT_COLLECTION_UID);
      const taken = mine.some(
        (c) => c.name.trim().toLowerCase() === name.trim().toLowerCase());
      if (!taken && mine.length >= allowed) {
        throw new Error(
          `Free keeps ${allowed} groups alongside your main collection. `
          + 'Densa Deck Pro keeps as many as you sort into — every group '
          + 'you have still works.',
        );
      }
    }
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
    // Answered from this phone's own record of what it owns. Tagging is
    // pure organisation — nothing is bought, sold or counted — and the
    // memberships live here anyway, so needing the PC for it made a job
    // done standing over a box depend on being at a desk.
    const owned = (await this.store.stacksByPrinting(printingId))
      .filter((s) => !finish || s.finish === finish);

    if (!owned.length) {
      return {
        printing_id: printingId, tagged: 0, owned: 0, candidates: [],
        collection_uid: collectionUid,
      };
    }
    if (owned.length > 1) {
      // A foil and a nonfoil are different objects worth different money,
      // and which one goes in the bundle is a question only the person
      // holding it can answer.
      return {
        printing_id: printingId, tagged: 0,
        owned: owned.reduce((n, s) => n + s.quantity, 0),
        candidates: owned.map((s) => ({
          item_id: 0,
          stack_key: s.stack_key,
          card_name: s.card_name,
          finish: s.finish,
          condition: s.condition,
          quantity: s.quantity,
        })),
        collection_uid: collectionUid,
      };
    }
    return this.tagStack(owned[0]!.stack_key, collectionUid);
  }

  /** The old desktop-side tag, kept for nothing — see tagIntoGroup. */
  private async tagIntoGroupOnPc(
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
  /**
   * Put a stack in a group, and take it back out.
   *
   * Local first, PC second — which is the whole point of a filter. The
   * membership tables and the sync event for them already existed and were
   * used by the collection screen; the scanner reached past them to the
   * desktop, so tagging a bundle worked at a desk and failed over a box.
   *
   * Addressed by `stack_key`, not the desktop's row id: local ids do not
   * travel, and the PC's do not exist here.
   */
  async tagStack(stackKey: string, collectionUid: string): Promise<TagResult> {
    const stack = await this.store.stackByKey(stackKey);
    if (!stack) {
      // Not a failure: "you do not own this card" is real information when
      // you are picking a bundle out of a pile.
      return {
        printing_id: '', tagged: 0, owned: 0, candidates: [],
        collection_uid: collectionUid,
      };
    }
    const already = (await this.listsFor(stackKey)).includes(collectionUid);
    await this.setListMembership(stack, collectionUid, true);
    return {
      printing_id: stack.printing_id,
      stack_key: stack.stack_key,
      card_name: stack.card_name,
      tagged: already ? 0 : 1,
      already_in: already,
      owned: stack.quantity,
      candidates: [],
      collection_uid: collectionUid,
    };
  }

  /** Take a stack back out of a group. The card itself is untouched. */
  async untagStack(stackKey: string, collectionUid: string): Promise<void> {
    const stack = await this.store.stackByKey(stackKey);
    if (stack) await this.setListMembership(stack, collectionUid, false);
  }

  async cardDetail(printingId: string, cardName: string): Promise<CardDetail> {
    // The PC first: it carries rulings, legality, prices and every printing,
    // none of which is on the phone. What the phone has is the rules text,
    // which is the part you are actually reading when you tap a card.
    try {
      return await this.client.call<CardDetail>('cards/detail', {
        printing_id: printingId,
        card_name: cardName,
      });
    } catch {
      const card = await this.store.oracleByName(cardName);
      if (!card) throw new Unreachable('That card is not on this phone yet.');
      return {
        printing_id: printingId,
        card_name: card.name,
        mana_cost: card.mana_cost,
        cmc: card.cmc ?? 0,
        type_line: card.type_line,
        oracle_text: card.oracle_text,
        color_identity: card.color_identity
          ? card.color_identity.split(/[^A-Z]+/).filter(Boolean) : [],
      };
    }
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
    // Local first and always. A hand-added want had nowhere local to
    // live, so the one screen you use standing in a shop could not be
    // added to FROM the shop.
    await this.store.setWish({
      card_name: cardName,
      quantity,
      set_code: printing?.set_code ?? '',
      collector_number: printing?.collector_number ?? '',
    });
    await this.engine.recordWish({
      card_name: cardName,
      quantity,
      set_code: printing?.set_code ?? '',
      collector_number: printing?.collector_number ?? '',
    });
    await this.refreshPending();
  }

  /** The old remote add, replaced above. */
  private async wishlistAddOnPc(
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

  /**
   * Cards that appear in more than one list.
   *
   * Computed here rather than asked for. Every input is already local —
   * the stacks and the memberships both live on this phone — so needing
   * the PC for it made a pure read of local data depend on the network.
   */
  async overlaps(minCollections = 2): Promise<OverlapsReply> {
    const stacks = await this.store.listStacks();
    const names = new Map((await this.store.listCollections())
      .map((c) => [c.collection_uid, c.name]));
    const cards = [];
    for (const stack of stacks) {
      const lists = await this.store.membershipsFor(stack.stack_key);
      if (lists.length < minCollections) continue;
      cards.push({
        item_id: 0,
        printing_id: stack.printing_id,
        card_name: stack.card_name,
        finish: stack.finish,
        quantity: stack.quantity,
        collection_count: lists.length,
        // Named, not uid'd: this is read by a person deciding which list to
        // take a card out of.
        collections: lists.map((uid) => names.get(uid) ?? uid),
        // More lists want it than you own copies of it — the situation the
        // screen exists to surface, as opposed to merely being in two.
        overcommitted: lists.length > stack.quantity,
      });
    }
    return {
      cards,
      overcommitted: cards.filter((c) => c.overcommitted).length,
    };
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
    // Both halves, locally. Filing it without clearing the want leaves
    // you shopping for a card already in your bag, and doing either half
    // only on the PC means the shop is the one place it does not work.
    await this.addCard({
      printing_id: printingId,
      card_name: cardName,
      quantity,
    });
    await this.store.forgetWish(cardName);
    await this.engine.recordWish({
      card_name: cardName, quantity: 0, forget: true,
    });
    await this.refreshPending();
    // Best-effort: the events are on disk whether or not the PC is there.
    await this.sync().catch(() => undefined);
  }

  /** Stop wanting a card, whichever printings were listed. */
  async removeFromWishlist(cardName: string): Promise<void> {
    await this.store.forgetWish(cardName);
    await this.engine.recordWish({
      card_name: cardName, quantity: 0, forget: true,
    });
    await this.refreshPending();
  }

  /** Wants added by hand, which no deck implies. */
  async handWishes(): Promise<WishlistRow[]> {
    const owned = await this.store.listStacks();
    const have = new Map<string, number>();
    for (const s of owned) {
      const key = s.card_name.trim().toLowerCase();
      have.set(key, (have.get(key) ?? 0) + s.quantity);
    }
    return (await this.store.wishes())
      // A want for a card you have since bought is finished, whether or not
      // anybody pressed the button.
      .filter((w) => (have.get(w.card_name.trim().toLowerCase()) ?? 0) < w.quantity)
      .map((w) => ({
        // `name`/`qty` come from DeckEntry, which a wishlist row extends —
        // the same shape a deck slot has, so the two lists render through
        // one component.
        name: w.card_name,
        qty: w.quantity,
        card_name: w.card_name,
        quantity: w.quantity,
        quantityAcrossDecks: w.quantity,
        set_code: w.set_code,
        collector_number: w.collector_number,
        // Nothing asked for it but you. That is the difference from a
        // derived row, and the screen says so.
        wantedBy: [],
      }));
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
    // The PC first, because it searches on far more than a name — rules
    // text, colours, types, format legality, price. This is the case where
    // the PC is genuinely better and the phone is the fallback rather than
    // the fast path.
    try {
      return await this.client.call<CardSearchReply>('cards/search', { query });
    } catch {
      // No PC. Answer from the phone's own index, which covers the search
      // people actually do standing over a box: by name.
      const term = String(query.name ?? '').trim();
      if (!term) return { cards: [], total: 0, offset: 0, limit: 0 };
      const found = await this.store.searchOracle(
        term, Number(query.limit ?? 50));
      const printings = await this.store.printingsForNames(
        found.map((c) => c.name));
      return {
        cards: found.map((c) => {
          const p = printings.get(c.name.trim().toLowerCase());
          return {
            scryfall_id: '',
            oracle_id: c.oracle_id,
            name: c.name,
            type_line: c.type_line,
            mana_cost: c.mana_cost,
            cmc: c.cmc ?? 0,
            colors: [],
            color_identity: c.color_identity
              ? c.color_identity.split(/[^A-Z]+/).filter(Boolean) : [],
            rarity: '',
            set_code: p?.set_code ?? '',
            // A printing id is what art is fetched by, and the art itself
            // is a Scryfall URL the phone loads directly — so a card found
            // offline still has a picture the moment there is any network,
            // without the PC being involved.
            printing_id: p?.printing_id ?? '',
          } as CatalogueCard;
        }),
        total: found.length,
        offset: 0,
        limit: found.length,
      };
    }
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
  // Plain network access for Scryfall, kept separate from the desktop
  // client — that one carries a pairing token to a machine that may not
  // exist.
  plainFetch?: typeof fetch,
): AppState {
  const client = new DesktopClient(pairing, fetchImpl ? { fetchImpl } : {});
  // The engine needs the deck store to APPLY deck events; without one it
  // remembers them and does nothing, which is recoverable but means a deck
  // built on the PC never appears here.
  const engine = new SyncEngine(store, client, device, uuid, decks);
  // An empty address is what "no desktop" is spelled as; see App.tsx.
  return new AppState(store, engine, client, decks, textReader,
                      !pairing.baseUrl, plainFetch);
}
