/**
 * A phone and a desktop, in memory.
 *
 * The sync engine is the part of this app that can lose someone's cards, so it
 * has to be testable on every run — not only when a device is plugged in. That
 * means no React Native imports anywhere in the code under test, and a driver
 * simple enough to implement here.
 *
 * `FakeDesktop` speaks the same routes as the Python bridge and applies events
 * with the same rules, so a disagreement between the two shows up as a failing
 * test rather than as cards going missing months later.
 */

import { randomUUID } from 'node:crypto';

/**
 * The subset of SQL the store actually uses, interpreted directly.
 *
 * Deliberately not a general SQL engine: it understands exactly the statements
 * in `store.ts` and throws on anything else, so a query the real driver would
 * choke on cannot pass silently here.
 */
export class MemoryDatabase {
  constructor() {
    this.tables = new Map();
  }

  _table(name) {
    if (!this.tables.has(name)) this.tables.set(name, []);
    return this.tables.get(name);
  }

  async run(sql, params = []) {
    const text = sql.trim().replace(/\s+/g, ' ');

    if (/^CREATE (TABLE|INDEX|UNIQUE INDEX)/i.test(text)) {
      const match = text.match(/CREATE TABLE IF NOT EXISTS (\w+)/i);
      if (match) this._table(match[1]);
      return;
    }

    if (/^INSERT (OR IGNORE )?INTO/i.test(text)) return this._insert(text, params);
    if (/^UPDATE/i.test(text)) return this._update(text, params);
    if (/^DELETE FROM/i.test(text)) return this._delete(text, params);
    throw new Error(`MemoryDatabase cannot run: ${text.slice(0, 90)}`);
  }

  _columnsOf(text) {
    const match = text.match(/\(([^)]*)\)\s*VALUES/i);
    if (!match) throw new Error(`no column list in: ${text.slice(0, 80)}`);
    return match[1]
      .split(',')
      .map((c) => c.trim())
      .filter((c) => c && !c.startsWith('/*'));
  }

  _insert(text, params) {
    const table = this._table(text.match(/INTO (\w+)/i)[1]);
    const columns = this._columnsOf(text);
    // VALUES can mix placeholders with literals — `VALUES (?, ?, 0)` is used
    // for a column with a fixed initial value. Binding positionally against
    // params alone put `undefined` in those columns, which silently broke
    // every query that filtered on them.
    const values = this._valuesOf(text);
    const row = {};
    let paramIndex = 0;
    columns.forEach((col, i) => {
      const literal = values[i];
      row[col] = literal === '?' ? params[paramIndex++] : this._literal(literal);
    });

    const keyCol = this._primaryKeyFor(text, columns);
    const existing = keyCol
      ? table.find((r) => r[keyCol] === row[keyCol])
      : undefined;

    if (existing) {
      if (/OR IGNORE/i.test(text)) return;
      if (/ON CONFLICT/i.test(text)) {
        // Only the columns the statement names in its DO UPDATE clause.
        const setters = text.match(/DO UPDATE SET (.+)$/i);
        if (setters) {
          for (const part of setters[1].split(',')) {
            const [target, source] = part.split('=').map((s) => s.trim());
            const col = target.replace(/^\w+\./, '');
            const from = source.replace('excluded.', '');
            existing[col] = row[from];
          }
        }
        return;
      }
    }
    table.push(row);
  }

  _valuesOf(text) {
    const match = text.match(/VALUES\s*\(([^)]*)\)/i);
    if (!match) throw new Error(`no VALUES in: ${text.slice(0, 80)}`);
    return match[1].split(',').map((v) => v.trim());
  }

  _literal(token) {
    if (token === undefined) return undefined;
    if (/^-?\d+$/.test(token)) return Number(token);
    const quoted = token.match(/^'(.*)'$/);
    return quoted ? quoted[1] : token;
  }

  _primaryKeyFor(text, columns) {
    const table = text.match(/INTO (\w+)/i)[1];
    const keys = {
      collections: 'collection_uid',
      stacks: 'stack_key',
      sync_events: 'event_uid',
      meta: 'key',
      decks: 'deck_id',
    };
    const key = keys[table];
    return key && columns.includes(key) ? key : undefined;
  }

  _update(text, params) {
    const table = this._table(text.match(/UPDATE (\w+)/i)[1]);
    const setPart = text.match(/SET (.+?) WHERE/i)[1];
    const wherePart = text.match(/WHERE (.+)$/i)[1];

    // SET and WHERE both mix placeholders with literals — `SET pushed = 1
    // WHERE event_uid = ?` has one of each. Consuming params positionally
    // across every assignment wrote the WHERE value into the SET column and
    // matched nothing, so marking events as pushed silently did nothing.
    let paramIndex = 0;
    const assignments = setPart.split(',').map((part) => {
      const [col, raw] = part.split('=').map((x) => x.trim());
      const value = raw === '?' ? params[paramIndex++] : this._literal(raw);
      return { col, value };
    });
    const conditions = wherePart.split(/ AND /i).map((part) => {
      const [col, raw] = part.split('=').map((x) => x.trim());
      const value = raw === '?' ? params[paramIndex++] : this._literal(raw);
      return { col, value };
    });

    for (const row of table) {
      if (conditions.every((c) => row[c.col] === c.value)) {
        assignments.forEach((a) => (row[a.col] = a.value));
      }
    }
  }

  _delete(text, params) {
    const name = text.match(/DELETE FROM (\w+)/i)[1];
    const table = this._table(name);
    const wherePart = text.match(/WHERE (.+)$/i);
    if (!wherePart) {
      this.tables.set(name, []);
      return;
    }
    // `=` and `!=`, because the real code uses both. Splitting on "=" alone
    // turned `device != ?` into a column called "device !", which matched
    // nothing, so the DELETE quietly kept every row — a fake that answers
    // "did nothing" to a statement it does not understand is worse than one
    // that throws, because the test passes.
    const clauses = wherePart[1].split(/ AND /i).map((clause, i) => {
      const negated = clause.includes('!=');
      const column = clause.split(negated ? '!=' : '=')[0].trim();
      return { column, negated, value: params[i] };
    });
    const matches = (row) =>
      clauses.every(({ column, negated, value }) =>
        negated ? row[column] !== value : row[column] === value);
    this.tables.set(name, table.filter((row) => !matches(row)));
  }

  async all(sql, params = []) {
    const text = sql.trim().replace(/\s+/g, ' ');

    if (/FROM stacks/i.test(text) && /SELECT \*/i.test(text)) {
      return this._selectStacks(text, params);
    }
    // Checked BEFORE the membership branch: the collection query mentions
    // stack_collections in a subquery, and dispatching on substrings means
    // whichever test runs first wins. It cost a green suite once.
    if (/FROM collections c/i.test(text)) return this._selectCollections();
    // Collections are filters: a stack can be in several lists at once, so
    // the fake engine has to answer the membership queries too or every test
    // about lists asserts against a store that cannot store them.
    if (/FROM stack_collections/i.test(text)) {
      const rows = this._table('stack_collections');
      if (/WHERE stack_key = \?/.test(text)) {
        return rows.filter((r) => r.stack_key === params[0]);
      }
      if (/WHERE collection_uid = \?/.test(text)) {
        return rows.filter((r) => r.collection_uid === params[0]);
      }
      return [...rows];
    }
    if (/FROM sync_events/i.test(text)) return this._selectEvents(text, params);
    if (/FROM decks/i.test(text)) {
      return [...this._table('decks')].sort((a, b) =>
        String(b.updated_at).localeCompare(String(a.updated_at)),
      );
    }
    throw new Error(`MemoryDatabase cannot select: ${text.slice(0, 90)}`);
  }

  _selectStacks(text, params) {
    let rows = this._table('stacks').filter((r) => r.quantity > 0);
    let i = 0;
    if (/stack_collections WHERE collection_uid = \?/.test(text)) {
      // The real query is "filed here OR a member here". Both parameters are
      // the same uid.
      const uid = params[i];
      i += 2;
      const members = new Set(
        this._table('stack_collections')
          .filter((r) => r.collection_uid === uid)
          .map((r) => r.stack_key),
      );
      rows = rows.filter(
        (r) => r.collection_uid === uid || members.has(r.stack_key),
      );
    } else if (/collection_uid = \?/.test(text)) {
      const uid = params[i++];
      rows = rows.filter((r) => r.collection_uid === uid);
    }
    if (/card_name LIKE \?/.test(text)) {
      const needle = String(params[i++]).replace(/%/g, '').toLowerCase();
      rows = rows.filter((r) => r.card_name.toLowerCase().includes(needle));
    }
    return [...rows].sort((a, b) => a.card_name.localeCompare(b.card_name));
  }

  _selectCollections() {
    // Counts membership as well as filing, mirroring the real query: a card
    // ticked into a list counts toward it even though it is filed elsewhere.
    const members = this._table('stack_collections');
    const stacks = this._table('stacks').filter((r) => r.quantity > 0);
    return [...this._table('collections')]
      .sort((a, b) =>
        (b.is_default ? 1 : 0) - (a.is_default ? 1 : 0) ||
        String(a.name).localeCompare(String(b.name)),
      )
      .map((c) => {
        const keys = new Set(
          members
            .filter((m) => m.collection_uid === c.collection_uid)
            .map((m) => m.stack_key),
        );
        const cards = stacks
          .filter(
            (s) => s.collection_uid === c.collection_uid || keys.has(s.stack_key),
          )
          .reduce((n, s) => n + s.quantity, 0);
        return { ...c, cards };
      });
  }

  _selectCollectionsLegacy() {
    const stacks = this._table('stacks');
    return this._table('collections')
      .map((c) => ({
        ...c,
        cards: stacks
          .filter((s) => s.collection_uid === c.collection_uid)
          .reduce((sum, s) => sum + s.quantity, 0),
      }))
      .sort(
        (a, b) =>
          Number(b.is_default) - Number(a.is_default) ||
          a.name.localeCompare(b.name),
      );
  }

  _selectEvents(text, params) {
    let rows = this._table('sync_events');
    if (/pushed = 0/.test(text)) rows = rows.filter((r) => r.pushed === 0);
    rows = [...rows].sort((a, b) => a.seq - b.seq);
    const limit = /LIMIT \?/.test(text) ? Number(params[params.length - 1]) : rows.length;
    return rows.slice(0, limit);
  }

  async get(sql, params = []) {
    const text = sql.trim().replace(/\s+/g, ' ');

    if (/COALESCE\(SUM\(quantity\), 0\) AS n FROM stacks/i.test(text)) {
      let rows = this._table('stacks');
      if (/WHERE collection_uid = \?/.test(text)) {
        rows = rows.filter((r) => r.collection_uid === params[0]);
      }
      return { n: rows.reduce((sum, r) => sum + r.quantity, 0) };
    }
    if (/SELECT quantity FROM stacks/i.test(text)) {
      return this._table('stacks').find((r) => r.stack_key === params[0]);
    }
    if (/SELECT collection_uid FROM stacks WHERE stack_key = \?/i.test(text)) {
      // Where a card is FILED, as distinct from which lists it belongs to.
      return this._table('stacks').find((r) => r.stack_key === params[0]);
    }
    if (/SELECT value FROM meta/i.test(text)) {
      return this._table('meta').find((r) => r.key === params[0]);
    }
    if (/MAX\(seq\), 0\) AS n FROM sync_events/i.test(text)) {
      const rows = this._table('sync_events').filter((r) => r.device === params[0]);
      return { n: rows.reduce((max, r) => Math.max(max, r.seq), 0) };
    }
    if (/SELECT 1 AS x FROM sync_events/i.test(text)) {
      return this._table('sync_events').find((r) => r.event_uid === params[0]);
    }
    if (/COUNT\(\*\) AS n FROM sync_events/i.test(text)) {
      return { n: this._table('sync_events').filter((r) => r.pushed === 0).length };
    }
    throw new Error(`MemoryDatabase cannot get: ${text.slice(0, 90)}`);
  }
}

/**
 * A desktop that answers the same routes as the Python bridge.
 *
 * Applies events by the same rules — additive deltas, idempotent by uid — so
 * that a divergence between this and the real implementation surfaces as a
 * failing test.
 */
export class FakeDesktop {
  constructor({ device = 'desktop-1', token = 'test-token' } = {}) {
    this.device = device;
    this.token = token;
    this.events = [];
    this.seen = new Set();
    this.stacks = new Map();
    this.collections = new Map();
    this.reachable = true;
    this.paired = true;
    this.pushCount = 0;
  }

  /** A `fetch` the client can be handed. */
  fetchImpl = async (url, init) => {
    if (!this.reachable) throw new Error('network unreachable');
    const route = String(url).split('/api/')[1];
    const payload = JSON.parse(init.body || '{}');

    if (!this.paired || init.headers['X-Densa-Token'] !== this.token) {
      return { status: 403, json: async () => ({ ok: false, error: 'not paired' }) };
    }
    return { status: 200, json: async () => this.handle(route, payload) };
  };

  handle(route, payload) {
    switch (route) {
      case 'sync/hello':
        return {
          device: this.device,
          head: this.events.length,
          events: this.events.length,
          peer_cursor: 0,
          protocol: 1,
        };
      case 'sync/pull': {
        const since = Number(payload.since || 0);
        const mine = this.events.filter(
          (e, i) => i >= since && e.device !== payload.peer,
        );
        return {
          events: mine,
          cursor: this.events.length,
          head: this.events.length,
          device: this.device,
          more: false,
        };
      }
      case 'sync/push': {
        this.pushCount += 1;
        let applied = 0;
        let duplicates = 0;
        for (const event of payload.events || []) {
          if (this.seen.has(event.event_uid)) {
            duplicates += 1;
            continue;
          }
          this.seen.add(event.event_uid);
          this.events.push(event);
          if (this.applyEvent(event)) applied += 1;
        }
        return {
          applied, duplicates, failed: 0, problems: [],
          head: this.events.length, device: this.device,
        };
      }
      default:
        return { ok: false, error: `unknown route '${route}'` };
    }
  }

  applyEvent(event) {
    if (event.kind === 'stack-delta') {
      const p = event.payload;
      const key = [
        p.printing_id, p.finish || 'nonfoil', p.condition || 'NM',
        p.language || 'en', p.location || '', p.collection_uid || '',
      ].join(' ');
      const next = (this.stacks.get(key) || 0) + Number(p.delta || 0);
      if (next <= 0) this.stacks.delete(key);
      else this.stacks.set(key, next);
      return true;
    }
    if (event.kind === 'collection-upsert') {
      this.collections.set(event.payload.collection_uid, event.payload.name);
      return true;
    }
    if (event.kind === 'collection-delete') {
      this.collections.delete(event.payload.collection_uid);
      return true;
    }
    return false;
  }

  /** An edit made ON the desktop, for testing the pull direction. */
  edit(payload, kind = 'stack-delta') {
    const event = {
      event_uid: randomUUID(),
      device: this.device,
      seq: this.events.length + 1,
      kind,
      payload,
      created_at: new Date().toISOString(),
    };
    this.seen.add(event.event_uid);
    this.events.push(event);
    this.applyEvent(event);
    return event;
  }

  totalCards() {
    return [...this.stacks.values()].reduce((sum, n) => sum + n, 0);
  }
}

let counter = 0;
/** Deterministic ids, so a failing test names the same event every run. */
export function testUuid() {
  counter += 1;
  return `uid-${String(counter).padStart(4, '0')}`;
}

export function resetUuid() {
  counter = 0;
}
