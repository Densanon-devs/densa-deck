/**
 * A real SQLite, for the things a fake one cannot tell you.
 *
 * `tests/harness.mjs` reimplements the queries this app makes, which
 * is what lets the sync engine be tested in plain Node. It is the
 * right tool for logic and the wrong one for schema: it has no
 * columns, so it cannot answer "does this column exist" and cannot
 * fail when one does not. A missing column shipped anyway, and the
 * phone said
 *
 *   no such column: price_usd
 *
 * on the upgrade path -- a sentence no test could have produced,
 * because no test was running SQL.
 *
 * Node has had a real SQLite built in since 22. Nothing to install.
 */

import { DatabaseSync } from 'node:sqlite';

/** Values node:sqlite will bind; everything else is coerced. */
function bindable(value) {
  if (value === undefined || value === null) return null;
  if (typeof value === 'boolean') return value ? 1 : 0;
  if (typeof value === 'number' || typeof value === 'string') return value;
  if (typeof value === 'bigint' || value instanceof Uint8Array) return value;
  return String(value);
}

/** The `Database` interface from store.ts, over a real file-less SQLite. */
export class RealDatabase {
  constructor(path = ':memory:') {
    this.db = new DatabaseSync(path);
  }

  async run(sql, params = []) {
    this.db.prepare(sql).run(...params.map(bindable));
  }

  async all(sql, params = []) {
    return this.db.prepare(sql).all(...params.map(bindable));
  }

  async get(sql, params = []) {
    return this.db.prepare(sql).get(...params.map(bindable)) ?? undefined;
  }

  close() {
    this.db.close();
  }
}
