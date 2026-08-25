/**
 * Everything the screens need to know, without any screen knowing how.
 *
 * The UI is deliberately thin: React Native cannot be tested here without a
 * device, so anything that decides something lives in this file instead and is
 * covered by the Node suite. A screen should read as a description of what is
 * on it, not as logic.
 */

import { DesktopClient } from './client.ts';
import type { Pairing } from './client.ts';
import type {
  CardQuery,
  CardSearchReply,
  CollectionPage,
  CollectionsReply,
} from './protocol.ts';
import { LocalStore } from './store.ts';
import { SyncEngine } from './sync.ts';

export type Connection = 'connected' | 'offline' | 'unpaired' | 'unknown';

export interface AppSnapshot {
  connection: Connection;
  pendingEdits: number;
  lastSyncAt?: string;
  lastError?: string;
}

const LAST_SYNC_KEY = 'sync.last_at';

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

    if (outcome.unpaired) {
      this.emit({ connection: 'unpaired', pendingEdits: pending,
                  lastError: outcome.error });
    } else if (outcome.offline) {
      this.emit({ connection: 'offline', pendingEdits: pending,
                  lastError: undefined });
    } else if (outcome.ok) {
      const now = new Date().toISOString();
      await this.store.setMeta(LAST_SYNC_KEY, now);
      this.emit({ connection: 'connected', pendingEdits: pending,
                  lastSyncAt: now, lastError: undefined });
    } else {
      this.emit({ connection: 'connected', pendingEdits: pending,
                  lastError: outcome.error });
    }

    // The desktop had more than one round could carry; keep going rather than
    // leaving the phone quietly out of date.
    if (outcome.more) return this.sync();
    return this.snapshot;
  }

  async refreshPending(): Promise<void> {
    this.emit({ pendingEdits: await this.engine.pending() });
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
  get scanClient(): DesktopClient {
    return this.client;
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
