/**
 * "Only mine", meaning the cards in ONE of my collections.
 *
 * Collections are filters rather than boxes, and "the cards in my
 * Modern binder" is a far more useful question while deckbuilding than
 * "cards I own somewhere". The chips that ask it have been in the
 * browser since collections existed and `owned_in` has been in the
 * query shape the whole time — but the phone's own search ignored it,
 * so choosing a collection while offline silently widened back out to
 * everything owned and the filter appeared to do nothing.
 *
 * End to end through AppState, because the scoping happens there: the
 * search is handed a narrower set, it does not do the narrowing.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

const CATALOGUE = [
  ['p-sol-new', 'Sol Ring', 'cmm', '410', 1, 'uncommon'],
  ['p-sol-old', 'Sol Ring', 'lea', '270', 1, 'uncommon'],
  ['p-bolt', 'Lightning Bolt', 'lea', '161', 1, 'common'],
];
const ORACLE = [
  ['o-sol', 'Sol Ring', 'Artifact', 'Adds mana.', '{1}', 1, ''],
  ['o-bolt', 'Lightning Bolt', 'Instant', 'Three damage.', '{R}', 1, 'R'],
];

async function phone() {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  await store.putCatalogue(CATALOGUE);
  await store.putOracle(ORACLE);
  // No desktop, so every search falls to the phone's own indexes.
  const state = buildAppState(store, { baseUrl: '', token: '' }, 'p1',
                              testUuid);
  return { store, state };
}

describe('owned, scoped to a collection', () => {
  test('only the chosen collection\'s cards come back', async () => {
    const { state } = await phone();
    await state.addCard({ printing_id: 'p-sol-old', card_name: 'Sol Ring',
                          collection_uid: 'binder', quantity: 1 });
    await state.addCard({ printing_id: 'p-bolt', card_name: 'Lightning Bolt',
                          collection_uid: 'shoebox', quantity: 1 });

    const reply = await state.searchCards({
      anywhere: 'o', ownership: 'owned', owned_in: 'binder',
    });
    const names = reply.cards.map((c) => c.name);
    assert.deepEqual(names, ['Sol Ring'],
      'a card owned in another collection is not owned HERE');
  });

  test('and no collection means everything owned', async () => {
    const { state } = await phone();
    await state.addCard({ printing_id: 'p-sol-old', card_name: 'Sol Ring',
                          collection_uid: 'binder', quantity: 1 });
    await state.addCard({ printing_id: 'p-bolt', card_name: 'Lightning Bolt',
                          collection_uid: 'shoebox', quantity: 1 });

    const reply = await state.searchCards({
      anywhere: 'o', ownership: 'owned',
    });
    assert.equal(reply.cards.length, 2);
  });

  test('the printing offered is the one in that collection', async () => {
    // Both failures compound: the wrong scope, and then the newest
    // printing within it rather than the copy on the shelf.
    const { state } = await phone();
    await state.addCard({ printing_id: 'p-sol-old', card_name: 'Sol Ring',
                          collection_uid: 'binder', quantity: 1 });

    const reply = await state.searchCards({
      name: 'Sol Ring', ownership: 'owned', owned_in: 'binder',
    });
    assert.equal(reply.cards[0].printing_id, 'p-sol-old',
      'the copy owned, not the newest reprint');
  });
});
