/**
 * The phone's own copy of the collection.
 *
 * Not a cache. The phone is edited while the desktop is unreachable — that is
 * the whole point of the companion — so this is a real database with its own
 * event log, and it is authoritative for the edits made here until they are
 * exchanged.
 *
 * The SQL is deliberately plain and the driver is injected. On a device that
 * driver is expo-sqlite; in tests it is an in-memory implementation. Keeping
 * the logic free of React Native imports is what allows the part that can lose
 * someone's cards to be tested in Node, on every run, without a device.
 */

import { stackKey } from './protocol.ts';
import type { StackDelta, SyncEvent } from './protocol.ts';

/** The minimum a SQLite driver has to provide. */
export interface Database {
  run(sql: string, params?: unknown[]): Promise<void>;
  all<T = Record<string, unknown>>(sql: string, params?: unknown[]): Promise<T[]>;
  get<T = Record<string, unknown>>(
    sql: string,
    params?: unknown[],
  ): Promise<T | undefined>;
}

/**
 * Mirrors the desktop's shape closely enough that the same reasoning applies
 * to both, and no closer. The phone does not need price history, acquisitions
 * or cost basis — those are desktop concerns and syncing them would mean
 * shipping the whole catalogue.
 */
export const SCHEMA: string[] = [
  `CREATE TABLE IF NOT EXISTS collections (
     collection_uid TEXT PRIMARY KEY,
     name TEXT NOT NULL,
     kind TEXT NOT NULL DEFAULT 'collection',
     notes TEXT NOT NULL DEFAULT '',
     is_default INTEGER NOT NULL DEFAULT 0,
     updated_at TEXT NOT NULL
   )`,
  `CREATE TABLE IF NOT EXISTS stacks (
     stack_key TEXT PRIMARY KEY,
     printing_id TEXT NOT NULL,
     card_name TEXT NOT NULL,
     oracle_id TEXT NOT NULL DEFAULT '',
     finish TEXT NOT NULL DEFAULT 'nonfoil',
     condition TEXT NOT NULL DEFAULT 'NM',
     language TEXT NOT NULL DEFAULT 'en',
     location TEXT NOT NULL DEFAULT '',
     collection_uid TEXT NOT NULL,
     quantity INTEGER NOT NULL DEFAULT 0,
     price_usd REAL,
     updated_at TEXT NOT NULL
   )`,
  `CREATE INDEX IF NOT EXISTS idx_stacks_collection
     ON stacks(collection_uid)`,
  `CREATE INDEX IF NOT EXISTS idx_stacks_name ON stacks(card_name)`,
  // The phone's own log. Events made here wait in it until a desktop is
  // reachable, which may be days.
  // Collections are FILTERS, not boxes. `stacks.collection_uid` says where a
  // card is filed — one place, because a physical card is in one physical box
  // — which is a different question from which lists it belongs to. The same
  // card can be in a set you are completing, a deck, and last weekend's
  // seventy-five, all at once and without moving.
  `CREATE TABLE IF NOT EXISTS stack_collections (
     stack_key TEXT NOT NULL,
     collection_uid TEXT NOT NULL,
     PRIMARY KEY (stack_key, collection_uid)
   )`,
  `CREATE INDEX IF NOT EXISTS idx_sc_collection
     ON stack_collections(collection_uid)`,
  `CREATE TABLE IF NOT EXISTS sync_events (
     event_uid TEXT PRIMARY KEY,
     device TEXT NOT NULL,
     seq INTEGER NOT NULL,
     kind TEXT NOT NULL,
     payload_json TEXT NOT NULL,
     created_at TEXT NOT NULL,
     /* 0 until a desktop has acknowledged it. Nothing is deleted on send:
        an event the desktop never confirmed must be re-sendable. */
     pushed INTEGER NOT NULL DEFAULT 0
   )`,
  `CREATE INDEX IF NOT EXISTS idx_events_pushed ON sync_events(pushed, seq)`,
  `CREATE TABLE IF NOT EXISTS meta (
     key TEXT PRIMARY KEY,
     value TEXT NOT NULL
   )`,
  `CREATE TABLE IF NOT EXISTS decks (
     deck_id TEXT PRIMARY KEY,
     name TEXT NOT NULL,
     format TEXT NOT NULL DEFAULT '',
     decklist_json TEXT NOT NULL DEFAULT '{}',
     notes TEXT NOT NULL DEFAULT '',
     updated_at TEXT NOT NULL
   )`,
  // Games played, and which version of the deck was on the table.
  //
  // `game_uid` is the primary key rather than a rowid, because this is the
  // identity that has to mean the same thing on the desktop: two devices
  // logging a game offline would each mint row 7, and a sync keyed on that
  // would treat two different games as one. Same reason a stack travels by
  // natural key and a collection by uid.
  //
  // Being the primary key is also what makes applying idempotent — a game
  // arriving twice, which it will, writes one row.
  `CREATE TABLE IF NOT EXISTS deck_games (
     game_uid TEXT PRIMARY KEY,
     deck_id TEXT NOT NULL,
     version_number INTEGER NOT NULL DEFAULT 0,
     result TEXT NOT NULL,
     opponent TEXT NOT NULL DEFAULT '',
     notes TEXT NOT NULL DEFAULT '',
     played_at TEXT NOT NULL
   )`,
  `CREATE INDEX IF NOT EXISTS idx_games_deck ON deck_games(deck_id)`,
  // What the desktop told us about a deck slot, kept.
  //
  // The mirror already answers for cards you OWN, so a deck of your own
  // cards survives going offline. Everything else — the price of a card you
  // have never owned, the art standing in for a name-only slot, the colour
  // identity a legality check needs — came from the desktop and vanished
  // with it. Opening a deck out of range then showed grey rectangles and no
  // total, which is the opposite of what a companion is for.
  //
  // Keyed by the slot key rather than the printing: a name-only slot and an
  // exact one are different questions with different answers.
  // What a card has been worth, one row per day.
  //
  // The phone cannot record this itself — it has no catalogue to price
  // against and no daily trigger — so the desktop keeps the series and this
  // holds whatever it has been handed. Kept rather than refetched because
  // the answer is the same tomorrow: a captured day never changes, and a
  // phone in a shop with no signal is exactly when somebody wants to know
  // whether a card has been climbing.
  //
  // Keyed by day as well as card, so pulling twice writes one row and a
  // later pull ADDS to what is there rather than replacing it. The desktop
  // returns a window; the phone should not forget a day that fell out of it.
  `CREATE TABLE IF NOT EXISTS price_points (
     series_key TEXT NOT NULL,
     captured_on TEXT NOT NULL,
     price_usd REAL,
     scope TEXT NOT NULL DEFAULT 'printing',
     PRIMARY KEY (series_key, captured_on)
   )`,
  `CREATE TABLE IF NOT EXISTS slot_facts (
     slot_key TEXT PRIMARY KEY,
     printing_id TEXT NOT NULL DEFAULT '',
     set_code TEXT NOT NULL DEFAULT '',
     collector_number TEXT NOT NULL DEFAULT '',
     price_usd REAL,
     color_identity TEXT NOT NULL DEFAULT '',
     type_line TEXT NOT NULL DEFAULT '',
     cached_at TEXT NOT NULL
   )`,
];

/** A stack as it comes back from the mirror. */
export interface StackRow {
  stack_key: string;
  printing_id: string;
  card_name: string;
  oracle_id: string;
  finish: string;
  condition: string;
  language: string;
  location: string;
  collection_uid: string;
  quantity: number;
  price_usd?: number | null;
  updated_at: string;
}

export interface CollectionRow {
  collection_uid: string;
  name: string;
  kind: string;
  notes: string;
  is_default: number;
  cards: number;
  updated_at: string;
}

/** The desktop's well-known uid for "cards I haven't filed anywhere". */
export const DEFAULT_COLLECTION_UID = '00000000-0000-4000-8000-00000000d0cc';

export class LocalStore {
  // Written out rather than declared as a constructor parameter property:
  // Node's type stripping runs the source as-is and cannot desugar those, and
  // being able to test this file in plain Node is worth more than the syntax.
  private db: Database;

  constructor(db: Database) {
    this.db = db;
  }

  async init(): Promise<void> {
    for (const stmt of SCHEMA) await this.db.run(stmt);
    // Both devices have to agree on what "unfiled" means without ever having
    // spoken, so the default collection has a fixed uid rather than a minted
    // one. A random uid per device gave each its own unfiled pile.
    await this.db.run(
      `INSERT OR IGNORE INTO collections
         (collection_uid, name, kind, is_default, updated_at)
       VALUES (?, 'Main Collection', 'collection', 1, ?)`,
      [DEFAULT_COLLECTION_UID, new Date().toISOString()],
    );
  }

  // ------------------------------------------------------------- metadata

  async setMeta(key: string, value: string): Promise<void> {
    await this.db.run(
      `INSERT INTO meta (key, value) VALUES (?, ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value`,
      [key, value],
    );
  }

  async getMeta(key: string): Promise<string | undefined> {
    const row = await this.db.get<{ value: string }>(
      'SELECT value FROM meta WHERE key = ?',
      [key],
    );
    return row?.value;
  }

  // --------------------------------------------------------------- stacks

  /**
   * Apply a quantity change to the local mirror.
   *
   * Additive by construction. Nothing here ever writes a total, because a
   * total asserts what the whole world holds and this device only knows what
   * it has seen.
   */
  /**
   * Set a stack to an absolute quantity.
   *
   * Used only by the first-sync baseline. Everything else here is a delta,
   * deliberately, so two devices editing offline both keep their change — an
   * absolute set cannot commute and would silently discard whichever edit
   * arrived first. It is safe here and nowhere else, because a device taking
   * a baseline has no state of its own to lose.
   */
  async setStackQuantity(row: StackDelta & { quantity: number }): Promise<void> {
    const key = stackKey(row);
    const existing = await this.db.get<{ quantity: number }>(
      'SELECT quantity FROM stacks WHERE stack_key = ?',
      [key],
    );
    const current = existing?.quantity ?? 0;
    const wanted = Math.max(0, Math.trunc(row.quantity));
    if (wanted === current) return;
    await this.applyDelta({ ...row, delta: wanted - current });
  }

  async applyDelta(delta: StackDelta): Promise<void> {
    const key = stackKey(delta);
    const now = new Date().toISOString();
    const existing = await this.db.get<{ quantity: number }>(
      'SELECT quantity FROM stacks WHERE stack_key = ?',
      [key],
    );

    if (existing) {
      const next = existing.quantity + delta.delta;
      if (next <= 0) {
        // A stack at zero is not a thing you own. Keeping it would make
        // "unique cards" wrong and clutter every list.
        await this.db.run('DELETE FROM stacks WHERE stack_key = ?', [key]);
        // And the lists that mentioned it. A membership for a card you no
        // longer own is a row that outlives its card — inert today because
        // every count reads from `stacks`, and a trap the moment one does
        // not. The desktop cleans up on the same event.
        await this.db.run(
          'DELETE FROM stack_collections WHERE stack_key = ?',
          [key],
        );
      } else {
        await this.db.run(
          'UPDATE stacks SET quantity = ?, updated_at = ? WHERE stack_key = ?',
          [next, now, key],
        );
      }
      return;
    }

    if (delta.delta <= 0) return; // removing from nothing is a no-op, not an error

    await this.db.run(
      `INSERT INTO stacks (stack_key, printing_id, card_name, oracle_id,
                           finish, condition, language, location,
                           collection_uid, quantity, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        key,
        delta.printing_id,
        delta.card_name,
        delta.oracle_id || '',
        delta.finish || 'nonfoil',
        delta.condition || 'NM',
        delta.language || 'en',
        delta.location || '',
        delta.collection_uid || DEFAULT_COLLECTION_UID,
        delta.delta,
        now,
      ],
    );
  }

  async totalCards(): Promise<number> {
    const row = await this.db.get<{ n: number }>(
      'SELECT COALESCE(SUM(quantity), 0) AS n FROM stacks',
    );
    return Number(row?.n ?? 0);
  }

  async cardsIn(collectionUid: string): Promise<number> {
    const row = await this.db.get<{ n: number }>(
      'SELECT COALESCE(SUM(quantity), 0) AS n FROM stacks WHERE collection_uid = ?',
      [collectionUid],
    );
    return Number(row?.n ?? 0);
  }

  async listStacks(
    collectionUid?: string,
    search?: string,
  ): Promise<StackRow[]> {
    const where: string[] = ['quantity > 0'];
    const params: unknown[] = [];
    if (collectionUid) {
      // Membership OR filing. A card belongs to a list either because it was
      // put there or because that is where it lives, and a filter that only
      // knew about one of those would hide cards from the collection they
      // were scanned into.
      where.push(
        `(collection_uid = ? OR stack_key IN (
            SELECT stack_key FROM stack_collections WHERE collection_uid = ?))`,
      );
      params.push(collectionUid, collectionUid);
    }
    if (search) {
      where.push('card_name LIKE ?');
      params.push(`%${search}%`);
    }
    return this.db.all<StackRow>(
      `SELECT * FROM stacks WHERE ${where.join(' AND ')}
       ORDER BY card_name LIMIT 500`,
      params,
    );
  }

  // -------------------------------------------------- membership (filters)

  /** Put a stack in a list without taking it out of any other. */
  async addMembership(stackKey: string, collectionUid: string): Promise<void> {
    if (!stackKey || !collectionUid) return;
    await this.db.run(
      'INSERT OR IGNORE INTO stack_collections (stack_key, collection_uid) VALUES (?, ?)',
      [stackKey, collectionUid],
    );
  }

  /**
   * Take a stack out of one list.
   *
   * The card itself is untouched — a filter cannot destroy what it filters.
   */
  async removeMembership(stackKey: string, collectionUid: string): Promise<void> {
    await this.db.run(
      'DELETE FROM stack_collections WHERE stack_key = ? AND collection_uid = ?',
      [stackKey, collectionUid],
    );
  }

  /** Every list a stack appears in, including where it is filed. */
  async membershipsFor(stackKey: string): Promise<string[]> {
    const rows = await this.db.all<{ collection_uid: string }>(
      'SELECT collection_uid FROM stack_collections WHERE stack_key = ?',
      [stackKey],
    );
    const stack = await this.db.get<{ collection_uid: string }>(
      'SELECT collection_uid FROM stacks WHERE stack_key = ?',
      [stackKey],
    );
    const uids = new Set(rows.map((r) => r.collection_uid));
    if (stack?.collection_uid) uids.add(stack.collection_uid);
    return [...uids];
  }

  // ---------------------------------------------------------- collections

  /**
   * Remember what the desktop said about these slots.
   *
   * Written on every successful resolve, so the cache warms simply by using
   * the app while it is in range.
   */
  async cacheSlotFacts(
    rows: Array<{ slot_key: string } & Record<string, unknown>>,
  ): Promise<void> {
    const now = new Date().toISOString();
    for (const row of rows) {
      const key = String(row.slot_key || '').trim();
      if (!key) continue;
      await this.db.run(
        `INSERT INTO slot_facts
           (slot_key, printing_id, set_code, collector_number, price_usd,
            color_identity, type_line, cached_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(slot_key) DO UPDATE SET
           printing_id = excluded.printing_id,
           set_code = excluded.set_code,
           collector_number = excluded.collector_number,
           price_usd = excluded.price_usd,
           color_identity = excluded.color_identity,
           type_line = excluded.type_line,
           cached_at = excluded.cached_at`,
        [
          key,
          String(row.printing_id ?? ''),
          String(row.set_code ?? ''),
          String(row.collector_number ?? ''),
          row.price_usd == null ? null : Number(row.price_usd),
          Array.isArray(row.color_identity) ? row.color_identity.join('') : '',
          String(row.type_line ?? ''),
          now,
        ],
      );
    }
  }

  /**
   * Everything remembered, keyed by slot.
   *
   * Read whole rather than queried per slot: a deck asks about a hundred
   * slots at once, and a hundred round trips through the driver costs more
   * than reading a table that holds a few thousand small rows.
   */
  async cachedSlotFacts(): Promise<Map<string, {
    printing_id: string;
    set_code: string;
    collector_number: string;
    price_usd: number | null;
    color_identity: string[];
    type_line: string;
  }>> {
    const rows = await this.db.all<{
      slot_key: string;
      printing_id: string;
      set_code: string;
      collector_number: string;
      price_usd: number | null;
      color_identity: string;
      type_line: string;
    }>('SELECT * FROM slot_facts');
    const out = new Map();
    for (const row of rows) {
      out.set(row.slot_key, {
        printing_id: row.printing_id ?? '',
        set_code: row.set_code ?? '',
        collector_number: row.collector_number ?? '',
        price_usd: row.price_usd ?? null,
        color_identity: String(row.color_identity ?? '').split('').filter(Boolean),
        type_line: row.type_line ?? '',
      });
    }
    return out;
  }

  /** Keep the points the desktop just handed over. */
  async cachePricePoints(
    seriesKey: string,
    scope: string,
    points: Array<{ captured_on: string; price_usd: number | null }>,
  ): Promise<void> {
    const key = (seriesKey || '').trim().toLowerCase();
    if (!key) return;
    for (const point of points) {
      const day = String(point.captured_on || '').trim();
      if (!day) continue;
      await this.db.run(
        `INSERT INTO price_points (series_key, captured_on, price_usd, scope)
         VALUES (?, ?, ?, ?)
         ON CONFLICT(series_key, captured_on) DO UPDATE SET
           price_usd = excluded.price_usd,
           scope = excluded.scope`,
        [key, day, point.price_usd == null ? null : Number(point.price_usd),
         scope || 'printing'],
      );
    }
  }

  /** Everything remembered for one card or printing, oldest first. */
  async cachedPricePoints(
    seriesKey: string,
  ): Promise<Array<{ captured_on: string; price_usd: number | null }>> {
    const key = (seriesKey || '').trim().toLowerCase();
    if (!key) return [];
    const rows = await this.db.all<{
      series_key: string;
      captured_on: string;
      price_usd: number | null;
    }>('SELECT * FROM price_points');
    return rows
      .filter((r) => r.series_key === key)
      .sort((a, b) => a.captured_on.localeCompare(b.captured_on))
      .map((r) => ({ captured_on: r.captured_on, price_usd: r.price_usd }));
  }

  async upsertCollection(row: {
    collection_uid: string;
    name: string;
    kind?: string;
    notes?: string;
    is_default?: boolean;
  }): Promise<void> {
    await this.db.run(
      `INSERT INTO collections (collection_uid, name, kind, notes, is_default,
                                updated_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(collection_uid) DO UPDATE SET
         name = excluded.name,
         notes = excluded.notes,
         updated_at = excluded.updated_at`,
      [
        row.collection_uid,
        row.name,
        row.kind ?? 'collection',
        row.notes ?? '',
        row.is_default ? 1 : 0,
        new Date().toISOString(),
      ],
    );
  }

  async deleteCollection(uid: string, discardCards: boolean): Promise<void> {
    if (discardCards) {
      // The memberships of the cards being destroyed go first, while the
      // stacks are still there to identify them. Afterwards there is nothing
      // to join against and the rows would outlive their cards — which is how
      // a collection you have cleared keeps being counted.
      //
      // Read then delete, rather than one statement with a subquery: this has
      // to run on the phone's SQLite and on the Node fake the data layer is
      // tested against, and a list of keys is a thing both understand.
      const doomed = await this.db.all<{ stack_key: string }>(
        'SELECT * FROM stacks WHERE collection_uid = ?',
        [uid],
      );
      for (const row of doomed) {
        await this.db.run('DELETE FROM stack_collections WHERE stack_key = ?',
                          [row.stack_key]);
      }
      await this.db.run('DELETE FROM stacks WHERE collection_uid = ?', [uid]);
    } else {
      // The grouping goes; the cards move to the unfiled pile. Deleting a
      // grouping must never delete cardboard.
      await this.db.run(
        'UPDATE stacks SET collection_uid = ? WHERE collection_uid = ?',
        [DEFAULT_COLLECTION_UID, uid],
      );
    }
    // Either way, nothing is a member of a collection that no longer exists.
    await this.db.run('DELETE FROM stack_collections WHERE collection_uid = ?',
                      [uid]);
    // The default collection is EMPTIED, never deleted — cards need somewhere
    // to land, and a device without it mints a second one on the next scan.
    // The desktop makes exactly this distinction; the phone applied the same
    // event and deleted the row, so clearing everything left the two with
    // different ideas of where unfiled cards live.
    if (uid !== DEFAULT_COLLECTION_UID) {
      await this.db.run('DELETE FROM collections WHERE collection_uid = ?', [uid]);
    }
  }

  /**
   * Forget everything the desktop ever told us, and where we had got to.
   *
   * The repair for a mirror that has drifted and cannot fix itself. A pulled
   * event is remembered by uid so it is never applied twice — which is right,
   * until one of them was recorded and NOT applied. After that the phone
   * skips it forever and no amount of syncing brings those cards back.
   *
   * Keeps this phone's OWN events, including any not yet pushed: they are
   * edits the user made that the desktop has not seen, and throwing them away
   * to fix a display problem would turn a confusing screen into lost work.
   * They push on the next sync, before the fresh baseline is pulled, so the
   * baseline already reflects them.
   */
  async forgetDesktopState(device: string): Promise<void> {
    await this.db.run('DELETE FROM stacks');
    await this.db.run('DELETE FROM stack_collections');
    await this.db.run('DELETE FROM sync_events WHERE device != ?', [device]);
    // Back to zero, which is what asks the desktop for a whole baseline
    // rather than a replay from a point in its log.
    await this.db.run('DELETE FROM meta WHERE key = ?', ['sync.cursor']);
  }

  async listCollections(): Promise<CollectionRow[]> {
    // Counts membership as well as filing, because that is what the list
    // itself shows. Counting only `stacks.collection_uid` meant ticking a
    // card into a collection changed the list and not the number beside it,
    // which reads as the tick not having worked.
    return this.db.all<CollectionRow>(
      `SELECT c.*, (
         SELECT COALESCE(SUM(s.quantity), 0) FROM stacks s
          WHERE s.quantity > 0
            AND (s.collection_uid = c.collection_uid
                 OR s.stack_key IN (SELECT stack_key FROM stack_collections
                                     WHERE collection_uid = c.collection_uid))
       ) AS cards
       FROM collections c
       ORDER BY c.is_default DESC, c.name`,
    );
  }

  // --------------------------------------------------------------- events

  async nextSeq(device: string): Promise<number> {
    const row = await this.db.get<{ n: number }>(
      'SELECT COALESCE(MAX(seq), 0) AS n FROM sync_events WHERE device = ?',
      [device],
    );
    return Number(row?.n ?? 0) + 1;
  }

  async recordEvent(event: SyncEvent): Promise<void> {
    await this.db.run(
      `INSERT OR IGNORE INTO sync_events
         (event_uid, device, seq, kind, payload_json, created_at, pushed)
       VALUES (?, ?, ?, ?, ?, ?, 0)`,
      [
        event.event_uid,
        event.device,
        event.seq,
        event.kind,
        JSON.stringify(event.payload),
        event.created_at,
      ],
    );
  }

  async knowsEvent(eventUid: string): Promise<boolean> {
    const row = await this.db.get(
      'SELECT 1 AS x FROM sync_events WHERE event_uid = ?',
      [eventUid],
    );
    return row !== undefined;
  }

  /** Events made here that no desktop has confirmed receiving yet. */
  async unpushed(limit = 200): Promise<SyncEvent[]> {
    const rows = await this.db.all<{
      event_uid: string;
      device: string;
      seq: number;
      kind: string;
      payload_json: string;
      created_at: string;
    }>(
      `SELECT event_uid, device, seq, kind, payload_json, created_at
       FROM sync_events WHERE pushed = 0 ORDER BY seq LIMIT ?`,
      [limit],
    );
    return rows.map((r) => ({
      event_uid: r.event_uid,
      device: r.device,
      seq: r.seq,
      kind: r.kind,
      payload: JSON.parse(r.payload_json),
      created_at: r.created_at,
    }));
  }

  /**
   * Mark events as delivered.
   *
   * Deliberately not a delete. An event the desktop acknowledged is still the
   * only record of what this device did, and keeping it is what lets a
   * re-paired or restored desktop be brought back up to date.
   */
  async markPushed(eventUids: string[]): Promise<void> {
    for (const uid of eventUids) {
      await this.db.run('UPDATE sync_events SET pushed = 1 WHERE event_uid = ?', [
        uid,
      ]);
    }
  }

  async pendingCount(): Promise<number> {
    const row = await this.db.get<{ n: number }>(
      'SELECT COUNT(*) AS n FROM sync_events WHERE pushed = 0',
    );
    return Number(row?.n ?? 0);
  }
}
