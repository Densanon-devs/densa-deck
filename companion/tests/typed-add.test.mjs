/**
 * Adding a card by typing it.
 *
 * The scanner cannot do everything, and two of its limits are
 * structural rather than fixable: a basic land has eight hundred
 * printings and no usable name lookup, and a card printed before 2014
 * carries no set code at all. Fighting the camera over those is a
 * waste of somebody's evening; four letters is faster.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

async function phone(rows) {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  await store.putCatalogue(rows);
  const state = buildAppState(store, { baseUrl: '', token: '' }, 'p1',
                              testUuid);
  return { store, state };
}

const CATALOGUE = [
  ['p1', 'Sol Ring', 'cmm', '410', 1, 'uncommon'],
  ['p2', 'Sol Ring', 'lea', '270', 1, 'uncommon'],
  ['p3', 'Lightning Bolt', 'lea', '161', 1, 'common'],
  ['p4', 'Console', 'unf', '12', 3, 'rare'],
  ['p5', 'Island', 'm11', '237', 0, 'common'],
];

describe('finding a card by typing its name', () => {
  test('a match comes back with how many printings it has', async () => {
    const { state } = await phone(CATALOGUE);
    const out = await state.searchCardNames('Sol Ring');
    assert.equal(out.length, 1);
    assert.equal(out[0].name, 'Sol Ring');
    assert.equal(out[0].printings, 2, 'so the next screen can be predicted');
  });

  test('the memorable word works, not just the first one', async () => {
    // Nobody types "lightning b". They type "bolt".
    const { state } = await phone(CATALOGUE);
    const names = (await state.searchCardNames('bolt')).map((r) => r.name);
    assert.deepEqual(names, ['Lightning Bolt']);
  });

  test('a prefix match sorts above a match in the middle', async () => {
    // Typing "sol" should offer Sol Ring before Console.
    //
    // This pins the INTENDED order, not the SQL that produces it:
    // MemoryDatabase does its own sorting, so deleting the real
    // query's `ORDER BY instr(...)` leaves this green. Found by
    // mutating it. SQLite is what enforces it on a device, and the
    // symptom there would be an alphabetical list rather than a
    // useful one -- annoying, not wrong.
    const { state } = await phone(CATALOGUE);
    const names = (await state.searchCardNames('sol')).map((r) => r.name);
    assert.deepEqual(names, ['Sol Ring', 'Console']);
  });

  test('case does not matter', async () => {
    const { state } = await phone(CATALOGUE);
    assert.equal((await state.searchCardNames('SOL RING')).length, 1);
  });

  test('one letter searches nothing', async () => {
    // Every card in Magic matches "a". Waiting for a second character
    // costs nothing and saves scanning a hundred thousand rows on the
    // first keystroke of every search.
    const { state } = await phone(CATALOGUE);
    assert.deepEqual(await state.searchCardNames('a'), []);
    assert.deepEqual(await state.searchCardNames(''), []);
    assert.deepEqual(await state.searchCardNames('   '), []);
  });

  test('a basic land is findable this way, which is the point', async () => {
    // It cannot be scanned: 828 printings and no name lookup can
    // choose between them. Typed, it is one tap to a list.
    const { state } = await phone(CATALOGUE);
    const out = await state.searchCardNames('Island');
    assert.equal(out[0].name, 'Island');
  });

  test('nothing matching is an empty list, not an error', async () => {
    const { state } = await phone(CATALOGUE);
    assert.deepEqual(await state.searchCardNames('zzzznope'), []);
  });

  test('an index that cannot search says so by returning nothing',
    async () => {
      // A phone mid-download has no catalogue yet.
      const { state } = await phone([]);
      assert.deepEqual(await state.searchCardNames('Sol'), []);
    });
});
