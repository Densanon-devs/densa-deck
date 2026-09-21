import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { LocalStore, DEFAULT_COLLECTION_UID } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

/**
 * Refreshing the prices of the cards you own.
 *
 * End to end, because the value of this feature is entirely in the
 * plumbing: which ids get asked about, what comes back, and whether a
 * deck can be valued afterwards without a PC.
 */
async function phone(reply) {
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  await store.putCatalogue([
    ['p-sol', 'Sol Ring', 'cmm', '410', 1, 'uncommon', 1.00, null],
    ['p-bolt', 'Lightning Bolt', 'lea', '161', 1, 'common', 2.00, null],
  ]);
  const decks = new DeckStore(db);
  const asked = [];
  const state = buildAppState(
    store, { baseUrl: '', token: '' }, 'p1', testUuid,
    undefined, decks, undefined,
    async (url, init) => {
      asked.push(JSON.parse(String(init?.body ?? '{}')));
      return { ok: true, status: 200, json: async () => reply };
    });
  return { store, state, decks, asked };
}

describe('refreshing prices for what you own', () => {
  test('only owned printings are asked about', async () => {
    const { state, asked } = await phone({ data: [] });
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring',
                          collection_uid: DEFAULT_COLLECTION_UID, quantity: 1 });
    await state.refreshPrices();
    assert.equal(asked.length, 1, 'one request');
    assert.deepEqual(asked[0].identifiers, [{ id: 'p-sol' }],
      'the card not owned is not asked about');
  });

  test('the new price replaces the one the index shipped', async () => {
    const { store, state } = await phone({
      data: [{ id: 'p-sol', prices: { usd: '3.09', usd_foil: '4.69' } }],
    });
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring',
                          collection_uid: DEFAULT_COLLECTION_UID, quantity: 1 });
    await state.refreshPrices();
    const prices = await store.pricesFor(['p-sol']);
    assert.equal(prices['p-sol'].usd, 3.09);
    assert.equal(prices['p-sol'].usdFoil, 4.69);
  });

  test('a card that lost its price goes back to null, not zero', async () => {
    const { store, state } = await phone({
      data: [{ id: 'p-sol', prices: { usd: null } }],
    });
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring',
                          collection_uid: DEFAULT_COLLECTION_UID, quantity: 1 });
    await state.refreshPrices();
    assert.equal((await store.pricesFor(['p-sol']))['p-sol'].usd, null);
  });

  test('owning nothing stamps the time and sends no request', async () => {
    // Otherwise it reads as permanently overdue and retries for ever.
    const { state, asked } = await phone({ data: [] });
    await state.refreshPrices(1000);
    assert.equal(asked.length, 0);
    assert.equal(await state.pricesUpdatedAt(), 1000);
  });

  test('a refusal is reported rather than silently doing nothing',
    async () => {
      const store = new LocalStore(new MemoryDatabase());
      await store.init();
      const state = buildAppState(
        store, { baseUrl: '', token: '' }, 'p1', testUuid,
        undefined, undefined, undefined,
        async () => ({ ok: false, status: 503, json: async () => ({}) }));
      await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring',
                            collection_uid: DEFAULT_COLLECTION_UID,
                            quantity: 1 });
      await assert.rejects(() => state.refreshPrices(), /503/);
    });
});

describe('and for what your decks point at', () => {
  /**
   * A deck is mostly cards that are not in a box yet -- that is what
   * cost-to-finish MEANS -- and pricing only what you own left every
   * one of them blank. The ids are already in the deck; asking about
   * them costs nothing extra.
   */
  const deck = (entries) => ({
    deck_id: 'd1', name: 'Atraxa', format: 'commander',
    decklist: entries, sideboard: [], commander: [],
    notes: '', updated_at: '2026-01-01',
  });

  test('a card in a deck but not in a box is priced', async () => {
    const { state, decks, asked } = await phone({ data: [] });
    await decks.save(deck([
      { name: 'Lightning Bolt', qty: 1, printing_id: 'p-bolt' },
    ]));
    await state.refreshPrices();
    assert.deepEqual(asked[0].identifiers, [{ id: 'p-bolt' }]);
  });

  test('the commander counts too', async () => {
    const { state, decks, asked } = await phone({ data: [] });
    await decks.save({
      ...deck([]),
      commander: [{ name: 'Atraxa', qty: 1, printing_id: 'p-atraxa' }],
    });
    await state.refreshPrices();
    assert.deepEqual(asked[0].identifiers, [{ id: 'p-atraxa' }]);
  });

  test('a card both owned and in a deck is asked about once', async () => {
    const { state, decks, asked } = await phone({ data: [] });
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring',
                          collection_uid: DEFAULT_COLLECTION_UID,
                          quantity: 1 });
    await decks.save(deck([
      { name: 'Sol Ring', qty: 1, printing_id: 'p-sol' },
    ]));
    await state.refreshPrices();
    assert.deepEqual(asked[0].identifiers, [{ id: 'p-sol' }]);
  });

  test('a slot with no printing id is not asked about', async () => {
    // There is nothing to ask. The catalogue already holds a price
    // for whichever printing answers for that name.
    const { state, decks, asked } = await phone({ data: [] });
    await decks.save(deck([{ name: 'Lightning Bolt', qty: 1 }]));
    await state.refreshPrices(1000);
    assert.equal(asked.length, 0);
  });

  test('a deck price comes back and is stored', async () => {
    const { store, state, decks } = await phone({
      data: [{ id: 'p-bolt', prices: { usd: '5.50', usd_foil: null } }],
    });
    await decks.save(deck([
      { name: 'Lightning Bolt', qty: 1, printing_id: 'p-bolt' },
    ]));
    await state.refreshPrices();
    assert.equal((await store.pricesFor(['p-bolt']))['p-bolt'].usd, 5.5);
  });
});
