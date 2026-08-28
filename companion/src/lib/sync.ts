/**
 * The phone's half of the exchange.
 *
 * An edit made here is written to the local mirror AND to the local event log
 * in the same breath. The log is what survives being offline: it may sit for
 * days before a desktop is reachable, and it is the only record that the edit
 * happened at all.
 *
 * Order matters on the wire. **Push before pull.** If a phone pulled first it
 * could receive a delete for a collection it has just filled with cards, apply
 * it, and only then send the additions — which would arrive addressed to a
 * collection that no longer exists on either side. Pushing first means the
 * desktop knows about the cards before it is asked to act on anything else.
 */

import { DesktopClient, Unpaired, Unreachable } from './client.ts';
import { stackKey } from './protocol.ts';
import type {
  HelloReply,
  PullReply,
  PushReply,
  StackDelta,
  SyncEvent,
} from './protocol.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from './store.ts';
import { DeckStore } from './decks.ts';
import type { DeckEntry } from './decks.ts';

/**
 * Deck entries off the wire, in whichever shape the sender speaks.
 *
 * A newer peer sends `entries` — a list, with the printing each slot named.
 * An older one sends only `decklist`, a `{name: count}` map that cannot say
 * which printing or hold the same card twice. Both are accepted, because a
 * deck arriving in the older shape is still a deck and refusing it would
 * make an upgrade on one device silently break sync with the other.
 */
export function entriesFromSync(raw: unknown): DeckEntry[] {
  if (Array.isArray(raw)) {
    const out: DeckEntry[] = [];
    for (const item of raw) {
      if (!item || typeof item !== 'object') continue;
      const entry = item as Partial<DeckEntry>;
      const name = String(entry.name ?? '').trim();
      const qty = Number(entry.qty);
      if (!name || !Number.isFinite(qty) || qty <= 0) continue;
      out.push({
        name,
        qty,
        printing_id: entry.printing_id,
        set_code: entry.set_code,
        collector_number: entry.collector_number,
      });
    }
    return out;
  }
  if (raw && typeof raw === 'object') {
    const out: DeckEntry[] = [];
    for (const [name, qty] of Object.entries(raw as Record<string, unknown>)) {
      const count = Number(qty);
      if (!name || !Number.isFinite(count) || count <= 0) continue;
      out.push({ name, qty: count });
    }
    return out;
  }
  return [];
}

const CURSOR_KEY = 'sync.cursor';
const DESKTOP_KEY = 'sync.desktop_device';

export interface SyncOutcome {
  ok: boolean;
  pushed: number;
  pulled: number;
  duplicates: number;
  /** Set when the desktop could not be reached; not an error to shout about. */
  offline?: boolean;
  unpaired?: boolean;
  error?: string;
  /** True when the desktop had more waiting than one round could carry. */
  more?: boolean;
}

export interface UuidSource {
  (): string;
}

export class SyncEngine {
  private store: LocalStore;
  private client: DesktopClient;
  private device: string;
  /** Which device this phone is, for anything that has to keep its own events. */
  get deviceId(): string {
    return this.device;
  }
  private uuid: UuidSource;
  /**
   * Where decks and results land.
   *
   * Optional so every existing caller keeps working and a build without one
   * degrades to collection-only sync rather than failing — deck events are
   * then remembered but not applied, which is recoverable, instead of being
   * requested forever.
   */
  private decks?: DeckStore;

  constructor(
    store: LocalStore,
    client: DesktopClient,
    device: string,
    uuid: UuidSource,
    decks?: DeckStore,
  ) {
    this.store = store;
    this.client = client;
    this.device = device;
    this.uuid = uuid;
    this.decks = decks;
  }

  /** Attach a deck store after construction, for callers that build later. */
  useDeckStore(decks: DeckStore): void {
    this.decks = decks;
  }

  /**
   * A fresh id from the same source the engine uses for events.
   *
   * Shared rather than each caller reaching for its own: the tests replace
   * this to make ids predictable, and a second generator would escape that
   * and make one id in the pair unrepeatable.
   */
  mintUuid(): string {
    return this.uuid();
  }

  // ------------------------------------------------------- local editing

  /**
   * Change a quantity locally and remember to tell the desktop.
   *
   * The mirror and the log are written together on purpose: an edit that
   * changed one without the other would either be invisible to the desktop
   * forever, or claimed to the desktop without having happened here.
   */
  async editQuantity(delta: Omit<StackDelta, 'delta'> & { delta: number }): Promise<SyncEvent> {
    const payload: StackDelta = {
      ...delta,
      collection_uid: delta.collection_uid || DEFAULT_COLLECTION_UID,
      finish: delta.finish || 'nonfoil',
      condition: delta.condition || 'NM',
      language: delta.language || 'en',
      location: delta.location || '',
      oracle_id: delta.oracle_id || '',
      reason: delta.reason || 'phone',
    };
    await this.store.applyDelta(payload);
    return this.log('stack-delta', payload);
  }

  async createCollection(name: string, notes = ''): Promise<string> {
    const uid = this.uuid();
    await this.store.upsertCollection({ collection_uid: uid, name, notes });
    await this.log('collection-upsert', {
      collection_uid: uid,
      name,
      kind: 'collection',
      notes,
    });
    return uid;
  }

  async renameCollection(uid: string, name: string): Promise<void> {
    await this.store.upsertCollection({ collection_uid: uid, name });
    await this.log('collection-upsert', { collection_uid: uid, name });
  }

  /**
   * Remove a collection.
   *
   * `discardCards` is the difference between "I don't organise things that way
   * any more" and "I sold the whole box". It is never inferred — the caller
   * has to say which one it means, and the UI has to ask.
   */
  async deleteCollection(uid: string, discardCards = false): Promise<void> {
    await this.store.deleteCollection(uid, discardCards);
    await this.log('collection-delete', {
      collection_uid: uid,
      discard_cards: discardCards,
    });
  }

  private async log(kind: string, payload: Record<string, unknown>): Promise<SyncEvent> {
    const event: SyncEvent = {
      event_uid: this.uuid(),
      device: this.device,
      seq: await this.store.nextSeq(this.device),
      kind,
      payload,
      created_at: new Date().toISOString(),
    };
    await this.store.recordEvent(event);
    return event;
  }

  // ------------------------------------------------------------ exchange

  async sync(): Promise<SyncOutcome> {
    try {
      const hello = await this.client.call<HelloReply>('sync/hello', {
        peer: this.device,
      });
      await this.noticeDesktopChange(hello.device);

      const pushed = await this.pushPending();
      const pulled = await this.pullChanges();

      return {
        ok: true,
        pushed: pushed.applied + pushed.duplicates,
        pulled: pulled.applied,
        duplicates: pushed.duplicates,
        more: pulled.more,
      };
    } catch (err) {
      if (err instanceof Unreachable) {
        // Expected, and not a failure state a user needs telling about in
        // red. The edits are safe in the log and will go next time.
        return { ok: false, pushed: 0, pulled: 0, duplicates: 0, offline: true };
      }
      if (err instanceof Unpaired) {
        return {
          ok: false, pushed: 0, pulled: 0, duplicates: 0, unpaired: true,
          error: 'This phone was unpaired. Scan the QR code again.',
        };
      }
      return {
        ok: false, pushed: 0, pulled: 0, duplicates: 0,
        error: (err as Error).message,
      };
    }
  }

  /**
   * If the desktop is not the one we synced with before, our cursor is
   * meaningless — it points into somebody else's history. Start over rather
   * than resuming from a number that refers to nothing.
   */
  private async noticeDesktopChange(desktopDevice: string): Promise<void> {
    const known = await this.store.getMeta(DESKTOP_KEY);
    if (known && known !== desktopDevice) {
      await this.store.setMeta(CURSOR_KEY, '0');
    }
    await this.store.setMeta(DESKTOP_KEY, desktopDevice);
  }

  private async pushPending(): Promise<{ applied: number; duplicates: number }> {
    let applied = 0;
    let duplicates = 0;

    // Loop: a phone offline for a week can have more waiting than one request
    // should carry.
    for (;;) {
      const batch = await this.store.unpushed(200);
      if (!batch.length) break;

      const reply = await this.client.call<PushReply>('sync/push', {
        events: batch,
        peer: this.device,
      });
      applied += reply.applied;
      duplicates += reply.duplicates;

      // Marked only after the desktop confirms. A push whose response was
      // lost stays pending and is sent again — safe, because every event is
      // idempotent by uid.
      await this.store.markPushed(batch.map((e) => e.event_uid));
      if (batch.length < 200) break;
    }
    return { applied, duplicates };
  }

  private async pullChanges(): Promise<{ applied: number; more: boolean }> {
    const cursor = Number((await this.store.getMeta(CURSOR_KEY)) ?? 0);
    const reply = await this.client.call<PullReply>('sync/pull', {
      since: cursor,
      peer: this.device,
      limit: 500,
    });

    let applied = 0;
    for (const event of reply.events) {
      if (await this.applyRemote(event)) applied += 1;
    }
    await this.store.setMeta(CURSOR_KEY, String(reply.cursor));
    return { applied, more: Boolean(reply.more) };
  }

  /**
   * Apply one event from the desktop. False if it was already known.
   *
   * ORDER MATTERS, and it is not the same order for every kind.
   *
   * Everything used to be recorded first and applied second. Anything that
   * interrupted the app in between — a force-quit, the OS reclaiming it, a
   * crash — left the event marked KNOWN and never applied, and `knowsEvent`
   * then skipped it on every future sync. The cards it described could never
   * arrive again. That is not theoretical: it is what a phone looks like
   * after someone force-quits a sync, which is what someone does to a sync
   * that appears stuck.
   *
   * So the idempotent kinds are APPLIED first and recorded second. Applying
   * one twice is a no-op — `stack-set` is an absolute quantity, membership is
   * an add or a remove — so a crash in the gap costs a repeat, not a loss.
   *
   * `stack-delta` is the exception and keeps the old order, because a delta
   * applied twice DOUBLE-COUNTS. There the safe failure is losing one, not
   * inventing cards, and a full re-pull is the repair.
   */
  private async applyRemote(event: SyncEvent): Promise<boolean> {
    if (await this.store.knowsEvent(event.event_uid)) return false;

    // Recorded as already pushed: it came FROM the desktop, so sending it
    // back would be pointless traffic.
    const remember = async () => {
      await this.store.recordEvent(event);
      await this.store.markPushed([event.event_uid]);
    };
    if (event.kind === 'stack-delta') await remember();

    switch (event.kind) {
      case 'stack-delta':
        await this.store.applyDelta(event.payload as unknown as StackDelta);
        return true;
      case 'stack-set':
        // The first-sync baseline. The desktop's log only holds what has
        // happened since logging existed, so a phone replaying it from zero
        // could never learn about cards that predate it — half this
        // collection, as it turned out. The baseline sends the state instead.
        await this.store.setStackQuantity(
          event.payload as unknown as StackDelta & { quantity: number },
        );
        await remember();
        return true;
      case 'collection-upsert':
        await this.store.upsertCollection({
          collection_uid: String(event.payload.collection_uid ?? ''),
          name: String(event.payload.name ?? 'Collection'),
          kind: String(event.payload.kind ?? 'collection'),
          notes: String(event.payload.notes ?? ''),
        });
        await remember();
        return true;
      case 'membership': {
        // Which lists a card is in. Addressed by natural key on both sides:
        // local row ids cannot travel, because two devices scanning the same
        // card offline each mint their own.
        const payload = event.payload as unknown as StackDelta & {
          member?: boolean;
        };
        const key = stackKey(payload);
        const uid = String(event.payload.collection_uid ?? '');
        if (!uid) return false;
        if (event.payload.member) await this.store.addMembership(key, uid);
        else await this.store.removeMembership(key, uid);
        await remember();
        return true;
      }
      case 'collection-delete':
        await this.store.deleteCollection(
          String(event.payload.collection_uid ?? ''),
          Boolean(event.payload.discard_cards),
        );
        await remember();
        return true;
      case 'deck-upsert': {
        // Applied before it is remembered, like every other idempotent kind:
        // a crash in the gap costs a repeat rather than a deck that is
        // marked known and was never written.
        const decks = this.decks;
        if (!decks) {
          // No deck store wired in. Remembered anyway so it is not requested
          // forever, and so a build that gains one later is not stuck.
          await remember();
          return false;
        }
        const payload = event.payload as Record<string, unknown>;
        const deckId = String(payload.deck_id ?? '').trim();
        if (!deckId) {
          await remember();
          return false;
        }
        await decks.upsertFromSync({
          deck_id: deckId,
          name: String(payload.name ?? 'Untitled'),
          format: String(payload.format ?? ''),
          decklist: entriesFromSync(payload.entries ?? payload.decklist),
          sideboard: entriesFromSync(payload.sideboard),
          notes: String(payload.notes ?? ''),
          // When the DECK was edited, not when the event was written. They
          // are different, and the difference decides who wins.
          updated_at: String(payload.updated_at ?? event.created_at),
        });
        await remember();
        return true;
      }
      case 'deck-delete': {
        const decks = this.decks;
        if (decks) {
          await decks.remove(String(event.payload.deck_id ?? ''));
        }
        await remember();
        return Boolean(decks);
      }
      case 'deck-game': {
        const decks = this.decks;
        if (!decks) {
          await remember();
          return false;
        }
        const payload = event.payload as Record<string, unknown>;
        const gameUid = String(payload.game_uid ?? '').trim();
        const deckId = String(payload.deck_id ?? '').trim();
        if (!gameUid) {
          await remember();
          return false;
        }
        if (payload.removed) {
          await decks.forgetGame(gameUid);
        } else {
          await decks.recordGame({
            game_uid: gameUid,
            deck_id: deckId,
            version_number: Number(payload.version_number ?? 0) || 0,
            result: String(payload.result ?? ''),
            opponent: String(payload.opponent ?? ''),
            notes: String(payload.notes ?? ''),
            played_at: String(payload.played_at ?? event.created_at),
          });
        }
        await remember();
        return true;
      }
      default:
        // Stored but not acted on. A kind from a newer desktop must not break
        // this one, and dropping it would lose it permanently.
        await remember();
        return false;
    }
  }

  /**
   * Note a list change for the desktop.
   *
   * Carries the card's natural key, never a local row id: this phone's
   * numbering means nothing on the other machine.
   */
  async recordMembership(
    stack: {
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
    await this.log('membership', {
      printing_id: stack.printing_id,
      card_name: stack.card_name,
      finish: stack.finish,
      condition: stack.condition,
      language: stack.language,
      location: stack.location,
      collection_uid: collectionUid,
      member,
    });
  }

  /**
   * Note a deck edit for the desktop.
   *
   * Carries the entries, so the printings a slot named survive the crossing.
   * `decklist` goes too, as the name-keyed map an older desktop reads — the
   * two are the same deck said twice, and dropping the map would make this
   * event unreadable to a build that predates entries.
   */
  async recordDeckUpsert(deck: {
    deck_id: string;
    name: string;
    format: string;
    decklist: DeckEntry[];
    sideboard?: DeckEntry[];
    notes: string;
    updated_at: string;
  }): Promise<void> {
    const asMap: Record<string, number> = {};
    for (const entry of deck.decklist ?? []) {
      asMap[entry.name] = (asMap[entry.name] ?? 0) + entry.qty;
    }
    await this.log('deck-upsert', {
      deck_id: deck.deck_id,
      name: deck.name,
      format: deck.format,
      notes: deck.notes,
      decklist: asMap,
      entries: deck.decklist ?? [],
      sideboard: deck.sideboard ?? [],
      updated_at: deck.updated_at,
    });
  }

  async recordDeckDelete(deckId: string): Promise<void> {
    await this.log('deck-delete', { deck_id: deckId });
  }

  /** A game played here, or one taken back here. */
  async recordDeckGame(game: {
    deck_id: string;
    game_uid: string;
    result?: string;
    version_number?: number;
    opponent?: string;
    notes?: string;
    played_at?: string;
    removed?: boolean;
  }): Promise<void> {
    await this.log('deck-game', {
      deck_id: game.deck_id,
      game_uid: game.game_uid,
      result: game.result ?? '',
      version_number: game.version_number ?? 0,
      opponent: game.opponent ?? '',
      notes: game.notes ?? '',
      played_at: game.played_at ?? '',
      removed: Boolean(game.removed),
    });
  }

  async pending(): Promise<number> {
    return this.store.pendingCount();
  }
}
