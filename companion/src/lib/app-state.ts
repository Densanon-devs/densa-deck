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
import type {
  CardDetail,
  CataloguePrinting,
  CatalogueSet,
  DeckResolveReply,
  OverlapsReply,
  TagResult,
  ResolvedSlot,
  CardQuery,
  CardSearchReply,
  CollectionPage,
  CollectionsReply,
} from './protocol.ts';
import { resolveSlots, wishlistFromDecks } from './decks.ts';
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

export class AppState {
  private store: LocalStore;
  private engine: SyncEngine;
  private client: DesktopClient;
  private listeners = new Set<(s: AppSnapshot) => void>();
  private snapshot: AppSnapshot = {
    connection: 'unknown',
    pendingEdits: 0,
  };

  constructor(store: LocalStore, engine: SyncEngine, client: DesktopClient) {
    this.store = store;
    this.engine = engine;
    this.client = client;
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
  async sync(): Promise<AppSnapshot> {
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
    if (outcome.more) return this.sync();
    return this.snapshot;
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
  }): Promise<void> {
    await this.engine.editQuantity({
      printing_id: card.printing_id,
      card_name: card.card_name,
      oracle_id: card.oracle_id ?? '',
      finish: card.finish ?? 'nonfoil',
      condition: card.condition ?? 'NM',
      language: 'en',
      location: card.location ?? '',
      collection_uid: card.collection_uid ?? '',
      reason: 'phone-scan',
      delta: card.quantity ?? 1,
    });
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
    } catch {
      // Offline, or the desktop is asleep. The mirror still has an answer for
      // everything you own, which is most of a deck you are holding.
      answered = [];
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
  async wishlistAdd(cardName: string, quantity = 1): Promise<void> {
    await this.client.call('wishlist/add', {
      card_name: cardName,
      quantity,
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
): AppState {
  const client = new DesktopClient(pairing, fetchImpl ? { fetchImpl } : {});
  const engine = new SyncEngine(store, client, device, uuid);
  return new AppState(store, engine, client);
}
