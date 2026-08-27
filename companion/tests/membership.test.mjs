/**
 * Lists on the phone.
 *
 * The desktop already treated collections as filters — a card can be in a set
 * you are completing, a deck, and last weekend's seventy-five at once. The
 * phone still modelled one collection per card, so it showed a different
 * answer to the same question, which is worse than not having the feature.
 *
 * The rule both sides hold: a filter cannot destroy what it filters.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { buildAppState } from '../src/lib/app-state.ts';
import { stackKey } from '../src/lib/protocol.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase } from './harness.mjs';

const SET = 'aaaaaaaa-0000-4000-8000-000000000001';
const DECK = 'bbbbbbbb-0000-4000-8000-000000000002';

const card = {
  printing_id: 'p1',
  card_name: 'Sol Ring',
  oracle_id: '',
  finish: 'nonfoil',
  condition: 'NM',
  language: 'en',
  location: '',
  collection_uid: DEFAULT_COLLECTION_UID,
};

let seq = 0;

async function build() {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  const state = buildAppState(
    store,
    { baseUrl: 'http://100.64.0.1:8792', token: 't' },
    'phone-test',
    () => `id-${(seq += 1)}`,
    () => Promise.reject(new Error('offline on purpose')),
  );
  await state.addCard(card);
  const [stack] = await state.cards();
  return { store, state, stack };
}

describe('a card in several lists', () => {
  test('adding to one does not take it out of another', async () => {
    const { state, stack } = await build();
    await state.setListMembership(stack, SET, true);
    await state.setListMembership(stack, DECK, true);

    const lists = await state.listsFor(stack.stack_key);
    assert.ok(lists.includes(SET));
    assert.ok(lists.includes(DECK));
  });

  test('it still shows under where it is filed', async () => {
    // The default pile is "cards I have not filed anywhere". Being in a named
    // list does not stop a card being owned.
    const { state, stack } = await build();
    await state.setListMembership(stack, DECK, true);
    assert.ok((await state.listsFor(stack.stack_key)).includes(DEFAULT_COLLECTION_UID));
  });

  test('filtering by a list finds it', async () => {
    const { state, stack } = await build();
    await state.setListMembership(stack, SET, true);

    const rows = await state.cards(SET);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].card_name, 'Sol Ring');
  });

  test('filtering by a list it is not in finds nothing', async () => {
    const { state } = await build();
    assert.deepEqual(await state.cards(SET), []);
  });

  test('a card scanned into a collection shows under it without a membership row', async () => {
    // Filing and belonging are different, and a filter that only knew about
    // membership would hide every card from the collection it was scanned
    // into.
    const { state } = await build();
    const rows = await state.cards(DEFAULT_COLLECTION_UID);
    assert.equal(rows.length, 1);
  });
});

describe('a filter cannot destroy what it filters', () => {
  test('removing from a list keeps the card', async () => {
    const { state, stack } = await build();
    await state.setListMembership(stack, DECK, true);
    await state.setListMembership(stack, DECK, false);

    assert.ok(!(await state.listsFor(stack.stack_key)).includes(DECK));
    const owned = (await state.cards()).reduce((n, r) => n + r.quantity, 0);
    assert.equal(owned, 1);
  });

  test('removing from a list it was never in is a no-op', async () => {
    const { state, stack } = await build();
    await state.setListMembership(stack, SET, false);
    assert.equal((await state.cards()).length, 1);
  });
});

describe('telling the desktop', () => {
  test('a list change is queued like any other edit', async () => {
    // Made in a shop, applied at home. If it needed a connection it would not
    // be much use.
    const { state, stack } = await build();
    const before = (await state.sync()).pendingEdits;
    await state.setListMembership(stack, DECK, true);
    const after = (await state.sync()).pendingEdits;
    assert.ok(after > before, 'the change is waiting, not lost');
  });

  test('the event carries the card, not a local row id', async () => {
    // This phone's numbering means nothing on the other machine: two devices
    // scanning the same card offline each mint their own.
    const { store, state, stack } = await build();
    await state.setListMembership(stack, DECK, true);

    const rows = await store.unpushed(50);
    const event = rows.find((e) => e.kind === 'membership');
    assert.ok(event, 'no membership event was logged');
    const payload =
      typeof event.payload === 'string' ? JSON.parse(event.payload) : event.payload;
    assert.equal(payload.printing_id, 'p1');
    assert.equal(payload.collection_uid, DECK);
    assert.equal(payload.member, true);
    assert.ok(!('item_id' in payload), 'a local row id must not travel');
  });
});

describe('applying what the desktop sends', () => {
  test('a membership arriving for a card lands in the list', async () => {
    // Asserted through the store the applier writes to, because the engine
    // applies during a sync and there is no desktop here to sync with. An
    // earlier version of this test fell back to writing the row itself when
    // the assertion failed, which made it pass either way — worse than
    // having no test at all.
    const { store, state, stack } = await build();
    await store.addMembership(stackKey(card), SET);
    assert.ok((await state.listsFor(stack.stack_key)).includes(SET));
    assert.equal((await state.cards(SET)).length, 1);
  });

  test('a membership leaving takes it out of that list only', async () => {
    const { store, state, stack } = await build();
    await store.addMembership(stackKey(card), SET);
    await store.addMembership(stackKey(card), DECK);
    await store.removeMembership(stackKey(card), SET);

    const lists = await state.listsFor(stack.stack_key);
    assert.ok(!lists.includes(SET));
    assert.ok(lists.includes(DECK));
  });
});

describe('lists do not outlive their cards', () => {
  test('removing the last copy takes its lists with it', async () => {
    // A membership for a card you no longer own is a row that outlives its
    // card. Inert today because every count reads from `stacks` — and a trap
    // the moment one does not.
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    const delta = {
      printing_id: 'p1', card_name: 'Sol Ring', oracle_id: '',
      finish: 'nonfoil', condition: 'NM', language: 'en', location: '',
      collection_uid: DEFAULT_COLLECTION_UID, delta: 1, reason: 'test',
    };
    await store.applyDelta(delta);
    const key = stackKey(delta);
    await store.addMembership(key, 'some-list');
    // Filing puts it in the default collection too, so this is two lists.
    assert.ok((await store.membershipsFor(key)).includes('some-list'));

    await store.applyDelta({ ...delta, delta: -1 });
    assert.deepEqual(await store.membershipsFor(key), [],
                     'no list mentions a card that is gone');
  });

  test('discarding a collection clears the memberships too', async () => {
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    const delta = {
      printing_id: 'p1', card_name: 'Sol Ring', oracle_id: '',
      finish: 'nonfoil', condition: 'NM', language: 'en', location: '',
      collection_uid: DEFAULT_COLLECTION_UID, delta: 2, reason: 'test',
    };
    await store.applyDelta(delta);
    const key = stackKey(delta);
    await store.addMembership(key, 'some-list');

    await store.deleteCollection(DEFAULT_COLLECTION_UID, true);
    assert.equal(await store.totalCards(), 0);
    assert.deepEqual(await store.membershipsFor(key), [],
                     'cleared cards leave no lists behind');
  });
});
