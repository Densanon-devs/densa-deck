/**
 * Changing how many you own, and taking back a scan that was wrong.
 *
 * There was no way to do either from the phone. A scan that filed the wrong
 * card could only be fixed at the desktop — which is exactly where you are not
 * standing when it happens — and a second copy of a card you already had could
 * not be added at all.
 *
 * Both are the same operation as scanning with the sign flipped, because
 * quantities are deltas the whole way down to the sync log. What these check
 * is that the arithmetic behaves at the edges, where "remove one more than
 * exists" and "remove while offline" live.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { buildAppState } from '../src/lib/app-state.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase } from './harness.mjs';

let counter = 0;

async function build() {
  const database = new MemoryDatabase();
  const store = new LocalStore(database);
  await store.init();
  const state = buildAppState(
    store,
    { baseUrl: 'http://100.64.0.1:8792', token: 't' },
    'phone-test',
    () => `id-${(counter += 1)}`,
    // Fails immediately instead of dialling an address that is not there.
    // The real timeout is fifteen seconds and this test is about the queue,
    // not about how patiently the client waits.
    () => Promise.reject(new Error('no route to host')),
  );
  return { store, state };
}

const card = {
  printing_id: 'p1',
  card_name: 'Sol Ring',
  finish: 'nonfoil',
  collection_uid: DEFAULT_COLLECTION_UID,
};

async function owned(state, name = 'Sol Ring') {
  const rows = await state.cards();
  return rows
    .filter((r) => r.card_name === name)
    .reduce((total, r) => total + r.quantity, 0);
}

describe('adding another copy', () => {
  test('a second copy stacks rather than making a new row', async () => {
    const { state } = await build();
    await state.addCard(card);
    await state.addCard(card);
    const rows = await state.cards();
    assert.equal(rows.length, 1);
    assert.equal(rows[0].quantity, 2);
  });

  test('several at once', async () => {
    const { state } = await build();
    await state.addCard({ ...card, quantity: 4 });
    assert.equal(await owned(state), 4);
  });

  test('a different finish is a different stack', async () => {
    // A foil and a nonfoil are not interchangeable, and merging them would
    // silently misprice the collection.
    const { state } = await build();
    await state.addCard(card);
    await state.addCard({ ...card, finish: 'foil' });
    const rows = await state.cards();
    assert.equal(rows.length, 2);
  });
});

describe('taking one back out', () => {
  test('one fewer', async () => {
    const { state } = await build();
    await state.addCard({ ...card, quantity: 3 });
    await state.addCard({ ...card, quantity: -1 });
    assert.equal(await owned(state), 2);
  });

  test('removing the last one removes the row', async () => {
    // A stack sitting at zero would show as a card you own none of, which is
    // not a thing anybody wants in a list of their cards.
    const { state } = await build();
    await state.addCard(card);
    await state.addCard({ ...card, quantity: -1 });
    assert.deepEqual(await state.cards(), []);
  });

  test('removing all of them at once', async () => {
    const { state } = await build();
    await state.addCard({ ...card, quantity: 5 });
    await state.addCard({ ...card, quantity: -5 });
    assert.deepEqual(await state.cards(), []);
  });

  test('removing more than exists does not go negative', async () => {
    // The button offers "remove all N", but N came from a render that may be
    // a moment stale — a sync could have landed in between.
    const { state } = await build();
    await state.addCard({ ...card, quantity: 2 });
    await state.addCard({ ...card, quantity: -9 });
    assert.equal(await owned(state), 0);
    assert.deepEqual(await state.cards(), []);
  });

  test('removing something you do not have is a no-op, not an error', async () => {
    const { state } = await build();
    await state.addCard({ ...card, quantity: -1 });
    assert.deepEqual(await state.cards(), []);
  });
});

describe('it all still works with no signal', () => {
  test('an undo made offline is queued like any other edit', async () => {
    // The moment you notice a wrong card is the moment you are away from the
    // desktop. If the fix needed a connection it would not be a fix.
    const { state } = await build();
    await state.addCard(card);
    await state.addCard({ ...card, quantity: -1 });

    const snapshot = await state.sync();
    assert.equal(snapshot.connection, 'offline');
    assert.ok(snapshot.pendingEdits > 0, 'the edits are waiting, not lost');
    assert.deepEqual(await state.cards(), []);
  });
});
