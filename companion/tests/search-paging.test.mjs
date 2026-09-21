/**
 * Seeing all of your cards, not the first page of them.
 *
 * Reported: a collection of 88 cards showed 60 under "Only mine". 60
 * is the default page size, which is the tell — nothing was being
 * filtered out, the rest was simply never sent.
 *
 * Three things were wrong together and each hid the others. The local
 * search sliced to the limit and ignored `offset`, so every page was
 * page one. AppState reported `offset: 0` whatever was asked. And
 * `total` was set to the length of the truncated page, so the browser
 * was told 60 of 60 and had no reason to ask again.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

/** 88 distinct cards, the size of the collection that reported this. */
const COUNT = 88;
const CATALOGUE = Array.from({ length: COUNT }, (_, i) => [
  `p${i}`, `Test Card ${String(i).padStart(3, '0')}`, 'tst', String(i),
  1, 'common',
]);
const ORACLE = Array.from({ length: COUNT }, (_, i) => [
  `o${i}`, `Test Card ${String(i).padStart(3, '0')}`, 'Artifact',
  'Does a thing.', '{1}', 1, '',
]);

async function phone() {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  await store.putCatalogue(CATALOGUE);
  await store.putOracle(ORACLE);
  const state = buildAppState(store, { baseUrl: '', token: '' }, 'p1',
                              testUuid);
  for (let i = 0; i < COUNT; i += 1) {
    await state.addCard({
      printing_id: `p${i}`, card_name: `Test Card ${String(i).padStart(3, '0')}`,
      collection_uid: 'binder', quantity: 1,
    });
  }
  return { store, state };
}

describe('paging through your own cards', () => {
  test('the total is how many there ARE, not how many fit on a page',
    async () => {
      // The browser decides whether to ask for more from this number.
      // Reporting the page size means it never asks.
      const { state } = await phone();
      const reply = await state.searchCards({
        anywhere: 'Test Card', ownership: 'owned', owned_in: 'binder',
      });
      assert.equal(reply.total, COUNT,
        `88 owned cards should report 88, not ${reply.total}`);
    });

  test('a second page is different cards, not the first page again',
    async () => {
      const { state } = await phone();
      const query = {
        anywhere: 'Test Card', ownership: 'owned', owned_in: 'binder',
      };
      const first = await state.searchCards(query);
      const second = await state.searchCards({
        ...query, offset: first.cards.length,
      });

      assert.ok(second.cards.length, 'there is a second page at all');
      const firstIds = new Set(first.cards.map((c) => c.printing_id));
      const overlap = second.cards.filter((c) => firstIds.has(c.printing_id));
      assert.deepEqual(overlap, [], 'the pages must not repeat cards');
    });

  test('and the two pages together are the whole collection', async () => {
    const { state } = await phone();
    const query = {
      anywhere: 'Test Card', ownership: 'owned', owned_in: 'binder',
    };
    const seen = new Set();
    let offset = 0;
    for (let page = 0; page < 10; page += 1) {
      const reply = await state.searchCards({ ...query, offset });
      if (!reply.cards.length) break;
      reply.cards.forEach((c) => seen.add(c.printing_id));
      offset += reply.cards.length;
    }
    assert.equal(seen.size, COUNT, 'every owned card is reachable');
  });

  test('the offset asked for is the offset reported', async () => {
    // The browser works out the next page from this; a constant zero
    // means it asks for the same page for ever.
    const { state } = await phone();
    const reply = await state.searchCards({
      anywhere: 'Test Card', ownership: 'owned', offset: 60,
    });
    assert.equal(reply.offset, 60);
  });

  test('past the end is empty rather than wrapping round', async () => {
    const { state } = await phone();
    const reply = await state.searchCards({
      anywhere: 'Test Card', ownership: 'owned', offset: 500,
    });
    assert.deepEqual(reply.cards, []);
  });
});
