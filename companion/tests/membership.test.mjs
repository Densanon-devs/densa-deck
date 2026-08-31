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

describe('tagging at scan time', () => {
  /**
   * One pass over a box is usually several answers at once: these are mine,
   * these are for the Modern deck, these are going in the sale binder. Scan
   * time is the only cheap moment to say so — afterwards the cards are back
   * in the box and the knowledge is gone.
   *
   * All of it offline on purpose. Scanning a box happens where the box is,
   * which is rarely next to the PC.
   */
  async function scanned(extras, quantity = 1) {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    const state = buildAppState(
      store,
      { baseUrl: 'http://100.64.0.1:8792', token: 't' },
      'phone-test',
      () => `id-${(seq += 1)}`,
      () => Promise.reject(new Error('offline on purpose')),
    );
    await state.addCard({ ...card, quantity, also_collection_uids: extras });
    return { store, state };
  }

  test('a card scanned into two extra lists is in both', async () => {
    const { state } = await scanned([SET, DECK]);
    const [stack] = await state.cards();
    const lists = await state.listsFor(stack.stack_key);
    assert.ok(lists.includes(SET), lists);
    assert.ok(lists.includes(DECK), lists);
  });

  test('and it is still ONE card', async () => {
    // The whole hazard. A stack is keyed by the list it lives in, so filing
    // it once per list would claim you own three of a card you scanned once.
    const { state } = await scanned([SET, DECK]);
    const stacks = await state.cards();
    assert.equal(stacks.length, 1, `${stacks.length} stacks from one scan`);
    assert.equal(stacks[0].quantity, 1);
  });

  test('the list it is filed in is not tagged again', async () => {
    // Measured in QUEUED EVENTS, not in the local list: adding a membership
    // twice is idempotent locally, so it would hide the extra work. Ticking
    // the filing list is a reasonable thing for a UI to do, and it must cost
    // the desktop nothing.
    const both = await scanned([DEFAULT_COLLECTION_UID, SET]);
    const one = await scanned([SET]);
    assert.equal(await both.state.pendingCount(),
      await one.state.pendingCount(),
      'tagging the list it is already filed in queued extra work');
    const [stack] = await both.state.cards();
    assert.ok((await both.state.listsFor(stack.stack_key)).includes(SET));
  });

  test('scanning with no extras behaves exactly as before', async () => {
    const { state } = await scanned([]);
    const stacks = await state.cards();
    assert.equal(stacks.length, 1);
    assert.equal(stacks[0].quantity, 1);
  });

  test('taking one copy back out does not untag the rest', async () => {
    // The OTHER copies are still in those lists. A slip of the finger must
    // not quietly empty them.
    //
    // Two copies deliberately: undoing the last one removes the card
    // itself, and a list is right to stop mentioning a card you no longer
    // own. That is the cascade, not this.
    const { state } = await scanned([SET], 2);
    const [stack] = await state.cards();
    await state.addCard({ ...card, quantity: -1, also_collection_uids: [SET] });

    const lists = await state.listsFor(stack.stack_key);
    assert.ok(lists.includes(SET), 'undoing one copy emptied the list');
    const [after] = await state.cards();
    assert.equal(after.quantity, 1);
  });

  test('and undoing queues no tag of its own', async () => {
    // Taking a copy out is not a statement about lists. Sending one would
    // have the desktop re-apply a tag on the strength of a removal.
    const { state } = await scanned([SET], 2);
    const before = await state.pendingCount();
    await state.addCard({ ...card, quantity: -1, also_collection_uids: [SET] });
    assert.equal(await state.pendingCount(), before + 1,
      'undoing one copy queued a membership as well as the removal');
  });

  test('but the last copy leaving takes its memberships with it', async () => {
    // A list that mentions a card you do not own is a list that lies.
    const { state } = await scanned([SET], 1);
    const [stack] = await state.cards();
    await state.addCard({ ...card, quantity: -1 });

    assert.deepEqual(await state.listsFor(stack.stack_key), []);
  });

  test('the tags are queued for the desktop, not just kept locally', async () => {
    // A tag that never syncs is a list that only exists on one device.
    const { state } = await scanned([SET, DECK]);
    const pending = await state.pendingCount();
    assert.ok(pending >= 3,
      `expected the add plus two tags to be queued, saw ${pending}`);
  });
});

describe('how many groups free keeps', () => {
  /**
   * Groups are made locally and offline, so the phone has to know the
   * limit itself. A limit only the PC knows is one the user discovers by
   * having a sync rejected — long after they made the group and put forty
   * cards in it.
   */
  async function phoneOn(allowances) {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    const state = buildAppState(
      store,
      { baseUrl: 'http://100.64.0.1:8792', token: 't' },
      'phone-test',
      () => `id-${(seq += 1)}`,
      async () => ({ status: 200, json: async () => ({ ok: true,
        tier: 'free', is_pro: false, allowances }) }),
    );
    await state.tier(true);
    return state;
  }

  test('three of your own are allowed', async () => {
    const state = await phoneOn({ collections: 3 });
    for (let n = 0; n < 3; n += 1) {
      assert.ok(await state.newCollection(`Box ${n}`), `box ${n}`);
    }
  });

  test('and the fourth is refused, in words rather than a stack trace',
    async () => {
      const state = await phoneOn({ collections: 3 });
      for (let n = 0; n < 3; n += 1) await state.newCollection(`Box ${n}`);
      await assert.rejects(() => state.newCollection('Box 4'),
        /Pro keeps as many/);
    });

  test('the main collection does not spend a slot', async () => {
    // It is made for you and cannot be opted out of.
    const state = await phoneOn({ collections: 3 });
    for (let n = 0; n < 3; n += 1) await state.newCollection(`Box ${n}`);
    const all = await state.collections();
    assert.equal(all.length, 4, 'the default should be there as well');
  });

  test('naming one you already have is not a new group', async () => {
    const state = await phoneOn({ collections: 3 });
    for (let n = 0; n < 3; n += 1) await state.newCollection(`Box ${n}`);
    assert.ok(await state.newCollection('Box 1'));
  });

  test('a phone that has never been told a number is not restricted',
    async () => {
      // Refusing on a value it has never been given would lock a paying
      // user out of their own phone.
      const state = await phoneOn({});
      for (let n = 0; n < 6; n += 1) {
        assert.ok(await state.newCollection(`Box ${n}`), n);
      }
    });

  test('and Pro is not restricted either', async () => {
    const state = await phoneOn({ collections: -1 });
    for (let n = 0; n < 6; n += 1) {
      assert.ok(await state.newCollection(`Box ${n}`), n);
    }
  });
});
