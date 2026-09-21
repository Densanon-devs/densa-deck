/**
 * Putting a card on the right printing after the fact.
 *
 * Scanning gets the name right far more often than the version: a card
 * with twenty-nine printings has one footer and twenty-nine ways to be
 * filed wrong. Several real passes did exactly that — right card,
 * wrong printing — so correcting it afterwards matters as much as
 * getting it right the first time.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { LocalStore, DEFAULT_COLLECTION_UID } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

async function phone() {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  const state = buildAppState(store, { baseUrl: '', token: '' }, 'phone-1',
                              testUuid);
  return { store, state };
}

/** How many of a printing are filed, across every stack. */
async function held(store, printingId) {
  const rows = await store.stacksByPrinting(printingId);
  return rows.reduce((n, r) => n + Number(r.quantity || 0), 0);
}

describe('moving a stack onto another printing', () => {
  test('the cards end up on the new printing', async () => {
    const { store, state } = await phone();
    await state.addCard({
      printing_id: 'old', card_name: 'Royal Assassin', quantity: 3,
      collection_uid: DEFAULT_COLLECTION_UID,
    });

    await state.changePrinting(
      { printing_id: 'old', card_name: 'Royal Assassin', quantity: 3,
        collection_uid: DEFAULT_COLLECTION_UID },
      'new');

    assert.equal(await held(store, 'new'), 3);
    assert.equal(await held(store, 'old'), 0);
  });

  test('the whole quantity moves, not one copy', async () => {
    // A playset filed wrong is four cards wrong.
    const { store, state } = await phone();
    await state.addCard({
      printing_id: 'old', card_name: 'Sol Ring', quantity: 4,
      collection_uid: DEFAULT_COLLECTION_UID,
    });
    await state.changePrinting(
      { printing_id: 'old', card_name: 'Sol Ring', quantity: 4,
        collection_uid: DEFAULT_COLLECTION_UID },
      'new');
    assert.equal(await held(store, 'new'), 4);
  });

  test('finish travels with it', async () => {
    // A foil that becomes a nonfoil on the way is a different card at
    // a different price.
    const { store, state } = await phone();
    await state.addCard({
      printing_id: 'old', card_name: 'Sol Ring', finish: 'foil',
      quantity: 1, collection_uid: DEFAULT_COLLECTION_UID,
    });
    await state.changePrinting(
      { printing_id: 'old', card_name: 'Sol Ring', finish: 'foil',
        quantity: 1, collection_uid: DEFAULT_COLLECTION_UID },
      'new');

    const moved = (await store.stacksByPrinting('new'))
      .find((r) => Number(r.quantity) > 0);
    assert.equal(moved.finish, 'foil');
  });

  test('adding happens BEFORE removing', async () => {
    /*
     * Not a detail. If the second half fails, this order leaves two
     * stacks of the same card -- visible, and fixable by the person
     * looking at them. The other order leaves none, silently, with no
     * record of what was lost.
     *
     * Duplicates are an annoyance. A deletion is a missing card.
     */
    const { store, state } = await phone();
    await state.addCard({
      printing_id: 'old', card_name: 'Royal Assassin', quantity: 2,
      collection_uid: DEFAULT_COLLECTION_UID,
    });

    const order = [];
    const real = state.addCard.bind(state);
    state.addCard = async (card) => {
      order.push(Number(card.quantity) > 0 ? 'add' : 'remove');
      if (order.length === 2) throw new Error('interrupted');
      return real(card);
    };

    await assert.rejects(() => state.changePrinting(
      { printing_id: 'old', card_name: 'Royal Assassin', quantity: 2,
        collection_uid: DEFAULT_COLLECTION_UID },
      'new'));

    assert.deepEqual(order, ['add', 'remove']);
    assert.equal(await held(store, 'new'), 2, 'the new stack exists');
    assert.equal(await held(store, 'old'), 2,
      'and the old one survives rather than the cards vanishing');
  });

  test('moving to the printing it is already on does nothing', async () => {
    // Otherwise the add and the remove cancel and the stack is emptied.
    const { store, state } = await phone();
    await state.addCard({
      printing_id: 'same', card_name: 'Sol Ring', quantity: 2,
      collection_uid: DEFAULT_COLLECTION_UID,
    });
    await state.changePrinting(
      { printing_id: 'same', card_name: 'Sol Ring', quantity: 2,
        collection_uid: DEFAULT_COLLECTION_UID },
      'same');
    assert.equal(await held(store, 'same'), 2);
  });

  test('an empty stack is not moved', async () => {
    const { store, state } = await phone();
    await state.changePrinting(
      { printing_id: 'old', card_name: 'Sol Ring', quantity: 0 }, 'new');
    assert.equal(await held(store, 'new'), 0);
  });
});
