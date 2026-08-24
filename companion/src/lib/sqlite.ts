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
export async function openDeviceDatabase(
  name = 'densa-deck.db',
): Promise<Database> {
  const sqlite = await import('expo-sqlite');
  const db = await sqlite.openDatabaseAsync(name);
  // WAL keeps a sync writing in the background from blocking the list the
  // user is scrolling. Cards arriving mid-scroll is the normal case here.
  await db.execAsync('PRAGMA journal_mode = WAL;');
  return new ExpoSqliteDatabase(db as unknown as ExpoDatabase);
}
