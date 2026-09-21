/**
 * The real SQLite driver, for when this runs on a phone.
 *
 * Kept in its own file and behind the same `Database` interface the tests use,
 * so importing expo-sqlite never leaks into the sync engine. That separation
 * is what allows the logic that can lose someone's cards to be tested in plain
 * Node on every run.
 */

import type { Database } from './store.ts';

/** The slice of expo-sqlite this needs, named so the import stays lazy. */
interface ExpoDatabase {
  runAsync(sql: string, params?: unknown[]): Promise<unknown>;
  getAllAsync<T>(sql: string, params?: unknown[]): Promise<T[]>;
  getFirstAsync<T>(sql: string, params?: unknown[]): Promise<T | null>;
}

export class ExpoSqliteDatabase implements Database {
  private db: ExpoDatabase;

  constructor(db: ExpoDatabase) {
    this.db = db;
  }

  async run(sql: string, params: unknown[] = []): Promise<void> {
    await this.db.runAsync(sql, params);
  }

  async all<T = Record<string, unknown>>(
    sql: string,
    params: unknown[] = [],
  ): Promise<T[]> {
    return this.db.getAllAsync<T>(sql, params);
  }

  async get<T = Record<string, unknown>>(
    sql: string,
    params: unknown[] = [],
  ): Promise<T | undefined> {
    const row = await this.db.getFirstAsync<T>(sql, params);
    return row ?? undefined;
  }
}

/**
 * Open the phone's collection database.
 *
 * The import is inside the function on purpose: this module is imported by
 * code that also runs under Node during tests, where expo-sqlite does not
 * exist and must not be resolved.
 */
/**
 * One connection per file, shared.
 *
 * This used to open a fresh one on every call, and the app calls it
 * five times -- the collection, the decks, and again down each of the
 * setup paths. Saving a deck then wrote through two different native
 * connections in one operation, which is how you get
 *
 *   Call to function 'NativeDatabase.prepareAsync' has been rejected.
 *   The 2nd argument cannot be cast to NativeStatement (received Integer)
 *   Cannot convert provided JavaScriptObject to the SharedObject,
 *   because it doesn't contain valid id
 *
 * on a Save that had worked a hundred times before. expo-sqlite hands
 * out native objects belonging to a specific connection; mixing them
 * is undefined and fails whenever the runtime happens to notice.
 *
 * Caching also makes SQLite serialise the writes for us, instead of
 * two handles contending over one file.
 */
const open = new Map<string, Promise<Database>>();

export async function openDeviceDatabase(
  name = 'densa-deck.db',
): Promise<Database> {
  // The PROMISE is cached, not the result, so two callers racing on
  // startup share one connection rather than opening two and keeping
  // the second.
  const held = open.get(name);
  if (held) return held;

  const opening = (async () => {
    const sqlite = await import('expo-sqlite');
    const db = await sqlite.openDatabaseAsync(name);
    // WAL keeps a sync writing in the background from blocking the list
    // the user is scrolling. Cards arriving mid-scroll is the normal
    // case here.
    await db.execAsync('PRAGMA journal_mode = WAL;');
    return new ExpoSqliteDatabase(db as unknown as ExpoDatabase);
  })();
  open.set(name, opening);
  try {
    return await opening;
  } catch (err) {
    // A failed open must not be cached, or the app is broken until it
    // is killed rather than until the next attempt.
    open.delete(name);
    throw err;
  }
}
