/**
 * Which printing a deck row shows a picture of.
 *
 * Reported as "the deck shows wrong cards now", right after the phone
 * learned to answer deck slots out of its own index instead of
 * needing a PC. Both halves of that were true: it stopped saying "82
 * cards couldn't be priced", and it started showing printings nobody
 * had ever owned.
 *
 * A deck row that names no printing -- which is most of them, because
 * a decklist is a list of names -- was resolved with
 *
 *   SELECT * FROM catalogue WHERE name = ?
 *
 * whose answer is whichever row SQLite reaches first. That is
 * insertion order from a 78 MB bulk file: arbitrary, and stable
 * enough to look deliberate. Worse, it then overwrote the lookup that
 * had already found the copy in the collection.
 *
 * Against a real SQLite, because the ordering IS the fix and an
 * in-memory reimplementation of it would only be testing itself.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { resolveSlots } from '../src/lib/decks.ts';
import { LocalStore } from '../src/lib/store.ts';
import { RealDatabase } from './real-sqlite.mjs';

/** Three printings of one card, in three sets of different ages. */
const PRINTINGS = [
  ['p-old', 'Island', 'lea', '234', 0, 'common', 1.5, null],
  ['p-mid', 'Island', 'ice', '100', 0, 'common', 0.5, null],
  ['p-new', 'Island', 'blb', '280', 0, 'common', 0.25, null],
];

const SETS = [
  { code: 'lea', name: 'Limited Edition Alpha', at: 1993, iconUri: '' },
  { code: 'ice', name: 'Ice Age', at: 1995, iconUri: '' },
  { code: 'blb', name: 'Bloomburrow', at: 2024, iconUri: '' },
];

async function indexed({ owns = [] } = {}) {
  const db = new RealDatabase();
  const store = new LocalStore(db);
  await store.init();
  await store.putCatalogue(PRINTINGS);
  await store.putSets(SETS);
  for (const printing of owns) {
    await db.run(
      `INSERT INTO stacks
         (stack_key, printing_id, card_name, collection_uid, quantity,
          updated_at)
       VALUES (?, ?, 'Island', 'main', 1, '2026-01-01')`,
      [`k-${printing}`, printing]);
  }
  return store;
}

const slot = (over = {}) => ({
  name: 'Island', printing_id: '', set_code: '', collector_number: '',
  ...over,
});

describe('a deck row that names no printing', () => {
  test('shows one you own', async () => {
    const store = await indexed({ owns: ['p-mid'] });
    const [facts] = await store.factsForEntries([slot()]);
    assert.equal(facts.printing_id, 'p-mid');
  });

  test('and not merely the first row in the index', async () => {
    // The bug, stated so it cannot come back: the owned printing is
    // deliberately neither the first inserted nor the newest.
    const store = await indexed({ owns: ['p-mid'] });
    const [facts] = await store.factsForEntries([slot()]);
    assert.notEqual(facts.printing_id, 'p-old');
    assert.notEqual(facts.printing_id, 'p-new');
  });

  test('a copy you no longer have does not count', async () => {
    // quantity 0 is a stack you sold or played out. It is still a row.
    const db = new RealDatabase();
    const store = new LocalStore(db);
    await store.init();
    await store.putCatalogue(PRINTINGS);
    await store.putSets(SETS);
    await db.run(
      `INSERT INTO stacks
         (stack_key, printing_id, card_name, collection_uid, quantity,
          updated_at)
       VALUES ('k', 'p-old', 'Island', 'main', 0, '2026-01-01')`);

    const [facts] = await store.factsForEntries([slot()]);
    assert.equal(facts.printing_id, 'p-new');
  });

  test('owning none of them shows the most recent printing', async () => {
    const store = await indexed();
    const [facts] = await store.factsForEntries([slot()]);
    assert.equal(facts.printing_id, 'p-new');
  });

  test('the set release dates are what decides that', async () => {
    // Not insertion order, which would give p-old. The catalogue is
    // inserted oldest-first here on purpose.
    const store = await indexed();
    const [facts] = await store.factsForEntries([slot()]);
    assert.equal(facts.set_code, 'blb');
  });

  test('an unknown set does not win by having no date', async () => {
    // COALESCE(released_at, 0), not NULL, which sorts unpredictably.
    const db = new RealDatabase();
    const store = new LocalStore(db);
    await store.init();
    await store.putCatalogue([
      ...PRINTINGS,
      ['p-nowhere', 'Island', 'zzz', '1', 0, 'common', null, null],
    ]);
    await store.putSets(SETS);

    const [facts] = await store.factsForEntries([slot()]);
    assert.equal(facts.printing_id, 'p-new');
  });

  test('two runs agree', async () => {
    const store = await indexed();
    const first = await store.factsForEntries([slot()]);
    const again = await store.factsForEntries([slot()]);
    assert.equal(first[0].printing_id, again[0].printing_id);
  });

  test('and it says it was a guess', async () => {
    const store = await indexed({ owns: ['p-mid'] });
    const [facts] = await store.factsForEntries([slot()]);
    assert.equal(facts.via, 'name');
  });
});

describe('a deck row that does name one', () => {
  test('an exact printing is used exactly', async () => {
    const store = await indexed({ owns: ['p-mid'] });
    const [facts] = await store.factsForEntries([
      slot({ printing_id: 'p-old' })]);
    assert.equal(facts.printing_id, 'p-old');
    assert.equal(facts.via, 'printing');
  });

  test('a set and number resolve to that printing', async () => {
    const store = await indexed({ owns: ['p-mid'] });
    const [facts] = await store.factsForEntries([
      slot({ set_code: 'ICE', collector_number: '100' })]);
    assert.equal(facts.printing_id, 'p-mid');
    assert.equal(facts.via, 'key');
  });

  test('a printing id the index does not hold falls back', async () => {
    // A card synced from a desktop whose index is newer than this
    // phone's. Better to show another printing than nothing.
    const store = await indexed();
    const [facts] = await store.factsForEntries([
      slot({ printing_id: 'p-unknown' })]);
    assert.equal(facts.printing_id, 'p-new');
    assert.equal(facts.via, 'name');
  });

  test('a card the index has never heard of has no facts', async () => {
    const store = await indexed();
    const [facts] = await store.factsForEntries([slot({ name: 'Nonesuch' })]);
    assert.equal(facts, undefined);
  });
});

describe('what outranks what', () => {
  // resolveSlots is pure, so these are the merge rules themselves.
  const entry = { name: 'Island', qty: 1 };
  const mine = [{ card_name: 'Island', printing_id: 'p-mid', price_usd: 2 }];

  test('a guess does not overwrite the copy in your box', () => {
    const out = resolveSlots([entry], mine, [{
      printing_id: 'p-new', set_code: 'blb', collector_number: '280',
      price_usd: 0.25, via: 'name', found: true,
    }]);
    assert.equal(out.island.printing_id, 'p-mid');
  });

  test('but it still fills in a card you do not own', () => {
    const out = resolveSlots([entry], [], [{
      printing_id: 'p-new', set_code: 'blb', collector_number: '280',
      price_usd: 0.25, via: 'name', found: true,
    }]);
    assert.equal(out.island.printing_id, 'p-new');
  });

  test('a resolved printing does overwrite it', () => {
    // The desktop, or the phone answering by set and number. That is
    // an answer about this slot rather than about the name.
    const out = resolveSlots([entry], mine, [{
      printing_id: 'p-new', set_code: 'blb', collector_number: '280',
      price_usd: 0.25, found: true,
    }]);
    assert.equal(out.island.printing_id, 'p-new');
  });

  test('the colours survive the guess being set aside', () => {
    // Colour identity belongs to the card, not the printing, so it is
    // correct no matter which one answered -- and a commander colour
    // lock depends on it.
    const out = resolveSlots([entry], mine, [{
      printing_id: 'p-new', set_code: 'blb', collector_number: '280',
      price_usd: 0.25, color_identity: ['U'], type_line: 'Basic Land',
      via: 'name', found: true,
    }]);
    assert.deepEqual(out.island.color_identity, ['U']);
    assert.equal(out.island.type_line, 'Basic Land');
  });

  test('a price you have beats a price from another printing', () => {
    const out = resolveSlots([entry], mine, [{
      printing_id: 'p-new', set_code: 'blb', collector_number: '280',
      price_usd: 0.25, via: 'name', found: true,
    }]);
    assert.equal(out.island.price_usd, 2);
  });

  test('and a guessed price beats no price at all', () => {
    // This is the "82 cards couldn't be priced" case: an owned stack
    // with no price of its own.
    const out = resolveSlots(
      [entry],
      [{ card_name: 'Island', printing_id: 'p-mid', price_usd: null }],
      [{
        printing_id: 'p-new', set_code: 'blb', collector_number: '280',
        price_usd: 0.25, via: 'name', found: true,
      }]);
    assert.equal(out.island.price_usd, 0.25);
    assert.equal(out.island.printing_id, 'p-mid');
  });
});
