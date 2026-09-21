/**
 * Installing a new build over an old one.
 *
 * Every table is created with `CREATE TABLE IF NOT EXISTS`. Add a
 * column to one and a fresh install gets it; a phone that already has
 * the app keeps the old table, because the CREATE succeeds by doing
 * nothing. Prices were added to the catalogue and the first phone to
 * update answered
 *
 *   Call to function 'NativeDatabase.prepareAsync' has been rejected.
 *   Caused by: Error code : no such column: price_usd
 *
 * which is the normal way to get a new build, failing.
 *
 * These run against a REAL SQLite rather than the in-memory harness,
 * because the harness has no columns and therefore cannot have a
 * missing one.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  addColumnStatements,
  canAddLive,
  tablePlan,
} from '../src/lib/migrate.ts';
import { LocalStore, SCHEMA } from '../src/lib/store.ts';
import { RealDatabase } from './real-sqlite.mjs';

/** Every CREATE TABLE in the schema, parsed. */
const PLANS = SCHEMA.map(tablePlan).filter(Boolean);

/** The live column names of a table. */
async function columns(db, table) {
  const rows = await db.all(`PRAGMA table_info(${table})`);
  return rows.map((r) => r.name);
}

/**
 * A database built from the schema with one column left out.
 *
 * This is what an older install IS: the same tables, minus whatever
 * has been added since.
 */
async function olderInstall(table, column) {
  const db = new RealDatabase();
  for (const stmt of SCHEMA) {
    const plan = tablePlan(stmt);
    if (!plan || plan.table !== table) {
      // An index over the column being removed could not have existed
      // on that install either. init recreates it once the column is
      // back -- which is the whole reason indexes are applied after
      // the migration rather than with the tables.
      if (!plan && new RegExp(`\\b${column}\\b`).test(stmt)) continue;
      await db.run(stmt);
      continue;
    }
    const kept = plan.columns
      .filter((c) => c.name !== column)
      .map((c) => c.definition);
    await db.run(`CREATE TABLE ${table} (${kept.join(', ')})`);
  }
  return db;
}

describe('the reported failure', () => {
  test('a catalogue with no price columns gets them', async () => {
    const db = await olderInstall('catalogue', 'price_usd');
    assert.ok(!(await columns(db, 'catalogue')).includes('price_usd'),
      'the fixture has to start without the column or it proves nothing');

    await new LocalStore(db).init();

    assert.ok((await columns(db, 'catalogue')).includes('price_usd'));
  });

  test('and "Update prices now" then works', async () => {
    // The exact statement the phone rejected: UPDATE ... SET price_usd.
    const db = await olderInstall('catalogue', 'price_usd');
    await db.run(
      `INSERT INTO catalogue (printing_id, name, set_code, collector_number)
       VALUES ('p1', 'Sol Ring', 'c21', '263')`);
    const store = new LocalStore(db);
    await store.init();

    await store.putPrices([{ printing_id: 'p1', usd: 3.09, usdFoil: null }]);

    assert.deepEqual(await store.pricesFor(['p1']),
      { p1: { usd: 3.09, usdFoil: null } });
  });

  test('the cards already in the index survive it', async () => {
    // A migration that loses a hundred thousand rows to gain a column
    // would be a worse bug than the one it fixes.
    const db = await olderInstall('catalogue', 'price_usd');
    await db.run(
      `INSERT INTO catalogue (printing_id, name, set_code, collector_number)
       VALUES ('p1', 'Sol Ring', 'c21', '263')`);

    await new LocalStore(db).init();

    const row = await db.get('SELECT * FROM catalogue WHERE printing_id = ?',
      ['p1']);
    assert.equal(row.name, 'Sol Ring');
    assert.equal(row.set_code, 'c21');
    // New column, no value yet. Not zero -- nobody has priced it.
    assert.equal(row.price_usd, null);
  });

  test('a card stored after the migration keeps its price', async () => {
    // End to end through the real insert, which binds eight values.
    const db = await olderInstall('catalogue', 'price_usd_foil');
    const store = new LocalStore(db);
    await store.init();

    await store.putCatalogue([
      ['p2', 'Rhystic Study', 'jr', '4', 3, 'rare', 12.5, 40],
    ]);

    assert.deepEqual(await store.pricesFor(['p2']),
      { p2: { usd: 12.5, usdFoil: 40 } });
  });
});

describe('every column, not just the one that broke', () => {
  // The point of doing this by reading the schema rather than by
  // listing migrations is that the next column added needs no thought.
  // This asserts that, for all of them.
  for (const plan of PLANS) {
    for (const column of plan.columns) {
      if (!canAddLive(column.definition)) continue;
      test(`${plan.table}.${column.name} is restored`, async () => {
        const db = await olderInstall(plan.table, column.name);
        await new LocalStore(db).init();
        assert.ok((await columns(db, plan.table)).includes(column.name));
      });
    }
  }
});

describe('a fresh install and a second run', () => {
  test('every planned column exists after one init', async () => {
    const db = new RealDatabase();
    await new LocalStore(db).init();
    for (const plan of PLANS) {
      const live = await columns(db, plan.table);
      for (const column of plan.columns) {
        assert.ok(live.includes(column.name),
          `${plan.table}.${column.name} missing on a fresh install`);
      }
    }
  });

  test('running init twice changes nothing and throws nothing', async () => {
    // Every app launch calls init. A migration that ran a second time
    // would fail with "duplicate column name" on the second launch,
    // which is a worse failure than the one being fixed.
    const db = new RealDatabase();
    const store = new LocalStore(db);
    await store.init();
    const before = await columns(db, 'catalogue');
    await store.init();
    assert.deepEqual(await columns(db, 'catalogue'), before);
  });

  test('the default collection is not duplicated by the second run',
    async () => {
      const db = new RealDatabase();
      const store = new LocalStore(db);
      await store.init();
      await store.init();
      const rows = await db.all('SELECT * FROM collections');
      assert.equal(rows.length, 1);
    });
});

describe('what it refuses to do', () => {
  test('a NOT NULL column with no default is not attempted', () => {
    // SQLite rejects it, and a throw inside init takes the whole
    // database open down -- the app would not start.
    assert.equal(canAddLive('name TEXT NOT NULL'), false);
    assert.equal(canAddLive("name TEXT NOT NULL DEFAULT ''"), true);
  });

  test('a primary key or unique column is not attempted', () => {
    assert.equal(canAddLive('printing_id TEXT PRIMARY KEY'), false);
    assert.equal(canAddLive('code TEXT UNIQUE'), false);
  });

  test('it is reported rather than silently skipped', () => {
    const plan = tablePlan('CREATE TABLE t (a TEXT PRIMARY KEY, b REAL)');
    const { statements, unsupported } = addColumnStatements(plan, ['b']);
    assert.deepEqual(statements, []);
    assert.deepEqual(unsupported, ['t.a']);
  });

  test('nothing is added to a table that is not there', async () => {
    // An empty PRAGMA means no such table, or a driver that cannot
    // answer. Either way, ALTERing something unexamined is worse.
    const db = new RealDatabase();
    const plan = tablePlan('CREATE TABLE ghost (a TEXT, b REAL)');
    const { statements } = addColumnStatements(plan, []);
    assert.equal(statements.length, 2);
    assert.equal((await columns(db, 'ghost')).length, 0);
  });
});

describe('reading the schema back', () => {
  test('a table-level constraint is not mistaken for a column', () => {
    const plan = tablePlan(`CREATE TABLE price_points (
       series_key TEXT NOT NULL,
       captured_on TEXT NOT NULL,
       PRIMARY KEY (series_key, captured_on)
     )`);
    assert.deepEqual(plan.columns.map((c) => c.name),
      ['series_key', 'captured_on']);
  });

  test('a comment containing a comma is not a column', () => {
    const plan = tablePlan(`CREATE TABLE t (
       a TEXT,
       -- one, two, three (and brackets)
       b REAL
     )`);
    assert.deepEqual(plan.columns.map((c) => c.name), ['a', 'b']);
  });

  test('CREATE INDEX is not a table', () => {
    assert.equal(tablePlan('CREATE INDEX IF NOT EXISTS i ON t(a)'), null);
  });

  test('every table in the schema parses', () => {
    // A statement this cannot read is a table that never migrates,
    // and it would fail silently.
    const tables = SCHEMA.filter((s) => /CREATE\s+TABLE/i.test(s));
    assert.equal(PLANS.length, tables.length);
    for (const plan of PLANS) assert.ok(plan.columns.length > 0);
  });
});
