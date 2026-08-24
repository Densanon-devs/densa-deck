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
      where.push('collection_uid = ?');
      params.push(collectionUid);
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

  // ---------------------------------------------------------- collections

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
      await this.db.run('DELETE FROM stacks WHERE collection_uid = ?', [uid]);
    } else {
      // The grouping goes; the cards move to the unfiled pile. Deleting a
      // grouping must never delete cardboard.
      await this.db.run(
        'UPDATE stacks SET collection_uid = ? WHERE collection_uid = ?',
        [DEFAULT_COLLECTION_UID, uid],
      );
    }
    await this.db.run('DELETE FROM collections WHERE collection_uid = ?', [uid]);
  }

  async listCollections(): Promise<CollectionRow[]> {
    return this.db.all<CollectionRow>(
      `SELECT c.*, COALESCE(SUM(s.quantity), 0) AS cards
       FROM collections c
       LEFT JOIN stacks s ON s.collection_uid = c.collection_uid
       GROUP BY c.collection_uid
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
