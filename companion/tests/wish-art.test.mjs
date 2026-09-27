/**
 * Seeing the card you want, and taking it off the list.
 *
 * Reported as: "I can't remove a wishlist on phone and when looking at them
 * I can't see the card". The remove existed in app-state with nothing on
 * screen calling it; the rows were names only.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { orderForWish } from '../src/lib/wish-art.ts';
import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';

const BOLTS = [
  { printing_id: 'p-m10', set_code: 'm10', collector_number: '146', released_year: 2009 },
  { printing_id: 'p-lea', set_code: 'lea', collector_number: '161', released_year: 1993 },
  { printing_id: 'p-2xm', set_code: '2xm', collector_number: '137', released_year: 2020 },
  { printing_id: 'p-2xm-b', set_code: '2xm', collector_number: '400', released_year: 2020 },
  { printing_id: 'p-old', set_code: 'x', collector_number: '1', released_year: null },
  { printing_id: '', set_code: 'bad', collector_number: '0', released_year: 2030 },
];

describe('which picture a wishlist row shows', () => {
  test('a want that names a printing shows exactly that one first', () => {
    const shown = orderForWish(BOLTS, { set_code: 'LEA', collector_number: '161' });
    assert.equal(shown[0].printing_id, 'p-lea');
  });

  test('a set without a number prefers that set', () => {
    const shown = orderForWish(BOLTS, { set_code: '2xm' });
    assert.deepEqual(shown.slice(0, 2).map((p) => p.set_code), ['2xm', '2xm']);
  });

  test('a want by name alone shows the newest printing first', () => {
    const shown = orderForWish(BOLTS, {});
    assert.equal(shown[0].released_year, 2020);
  });

  test('a printing of unknown age goes last, not first', () => {
    const shown = orderForWish(BOLTS, {});
    assert.equal(shown.at(-1).printing_id, 'p-old');
  });

  test('a row with no printing id has no picture to show and is dropped', () => {
    assert.ok(orderForWish(BOLTS, {}).every((p) => p.printing_id));
  });

  test('a card not in the index shows nothing rather than breaking', () => {
    assert.deepEqual(orderForWish([], { set_code: 'lea' }), []);
  });
});

async function makePhone(desktop) {
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const state = buildAppState(
    store, { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1', testUuid, desktop.fetchImpl, new DeckStore(db));
  return { store, state };
}

beforeEach(() => resetUuid());

describe('taking a card off the wishlist', () => {
  test('a want for a named printing comes off too', async () => {
    const desktop = new FakeDesktop();
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Volrath’s Shapeshifter', 1,
      { set_code: 'sth', collector_number: '46' });
    assert.equal((await state.handWishes()).length, 1);

    await state.removeFromWishlist('Volrath’s Shapeshifter');
    assert.deepEqual(await state.handWishes(), []);
  });

  test('and the removal is queued for the PC, so it does not come back', async () => {
    const desktop = new FakeDesktop();
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Black Lotus', 1);
    const before = await state.pendingCount();
    await state.removeFromWishlist('Black Lotus');
    assert.ok(await state.pendingCount() > before,
      'the PC is never told, and the want returns on the next sync');
  });
});
