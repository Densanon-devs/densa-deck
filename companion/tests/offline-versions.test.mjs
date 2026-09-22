/**
 * Choosing a printing with no PC in reach.
 *
 * Reported as "when adding them into a deck they only show one
 * version". The card browser builds its swipe-through pager from
 * `printingsFor`, that asked the desktop, and with no desktop it
 * caught the failure and set an empty list. Everything downstream is
 * gated on that list having more than one row -- the pager, the set
 * and number under the art, and the "Add this printing" button that
 * is the entire point of the screen -- so a standalone phone could
 * not choose a version of anything.
 *
 * Which is worst exactly where it matters most: a land you own four
 * different printings of.
 *
 * The phone has had the answer the whole time. Its own index holds
 * every printing of every card; only the asking went to the PC.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { buildAppState } from '../src/lib/app-state.ts';
import { LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

/** [id, name, set, number, cmc, rarity, usd, foil, artist, year] */
const PRINTINGS = [
  ['p-old', 'Island', 'lea', '234', 0, 'common', 1.5, null, 'Mark Poole',
   1993],
  ['p-mid', 'Island', 'ice', '100', 0, 'common', 0.5, null, 'Pat Morrissey',
   1995],
  ['p-new', 'Island', 'blb', '280', 0, 'common', 0.25, null, 'Sam Rowan',
   2024],
  ['p-sol', 'Sol Ring', 'cmm', '410', 1, 'uncommon', 1, null, 'Mike Bierek',
   2023],
];

/** A phone with an index and no desktop worth the name. */
async function standalone({ reachable = false } = {}) {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  await store.putCatalogue(PRINTINGS);
  const state = buildAppState(
    store,
    // An empty address is how "no desktop" is spelled; see App.tsx.
    { baseUrl: reachable ? 'http://pc.local' : '', token: '' },
    'p1', testUuid,
    async () => { throw new Error('Network request failed'); },
  );
  return { store, state };
}

describe('every printing of a card, with no PC', () => {
  test('all of them come back, not one', async () => {
    const { state } = await standalone();
    const { printings } = await state.printingsFor('Island');
    assert.equal(printings.length, 3);
  });

  test('newest first, the same order the scan picker uses', async () => {
    // Two places in the app let you choose a printing. They should
    // not disagree about what order printings come in.
    const { state } = await standalone();
    const { printings } = await state.printingsFor('Island');
    assert.deepEqual(printings.map((p) => p.printing_id),
      ['p-new', 'p-mid', 'p-old']);
  });

  test('with the set, the number and the price on each', async () => {
    // The pager prints all three under the art. The price is the new
    // one -- the index carries prices now, so a printing you do not
    // own can be priced, which is the whole reason to look at a list
    // of printings you do not own.
    const { state } = await standalone();
    const { printings } = await state.printingsFor('Island');
    const newest = printings[0];
    assert.equal(newest.set_code, 'blb');
    assert.equal(newest.collector_number, '280');
    assert.equal(newest.price_usd, 0.25);
  });

  test('a card with one printing gives one', async () => {
    const { state } = await standalone();
    const { printings } = await state.printingsFor('Sol Ring');
    assert.equal(printings.length, 1);
  });

  test('a card the index has never heard of gives none', async () => {
    // Not a crash, and not somebody else's printings.
    const { state } = await standalone();
    const { printings } = await state.printingsFor('Nonesuch');
    assert.deepEqual(printings, []);
  });

  test('a desktop that answers is still preferred', async () => {
    // Its catalogue is the complete one. This only fills the gap.
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    await store.putCatalogue(PRINTINGS);
    const state = buildAppState(
      store, { baseUrl: 'http://pc.local', token: 't' }, 'p1', testUuid,
      async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          printings: [{ printing_id: 'from-pc', set_code: 'zzz',
                        collector_number: '1' }],
        }),
      }),
    );
    const { printings } = await state.printingsFor('Island');
    assert.equal(printings.length, 1);
    assert.equal(printings[0].printing_id, 'from-pc');
  });

  test('a desktop that answers with nothing falls through', async () => {
    // An empty answer is not an answer. Before the fallback existed
    // this was indistinguishable from the card having one printing.
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    await store.putCatalogue(PRINTINGS);
    const state = buildAppState(
      store, { baseUrl: 'http://pc.local', token: 't' }, 'p1', testUuid,
      async () => ({ ok: true, status: 200,
                     json: async () => ({ printings: [] }) }),
    );
    const { printings } = await state.printingsFor('Island');
    assert.equal(printings.length, 3);
  });
});

describe('how many of each printing are on a shelf', () => {
  /**
   * Asked for while looking at the versions of a card: "how many of a
   * particular version do I have". Distinct from the deck count the
   * browser already showed, which answers how many are in the LIST.
   */
  async function withStacks(rows) {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    await store.putCatalogue(PRINTINGS);
    const state = buildAppState(
      store, { baseUrl: '', token: '' }, 'p1', testUuid,
      async () => { throw new Error('Network request failed'); },
    );
    for (const row of rows) {
      await state.addCard({
        printing_id: row.id,
        card_name: row.name ?? 'Island',
        finish: row.finish ?? 'nonfoil',
        collection_uid: row.where ?? 'main',
        quantity: row.qty ?? 1,
      });
    }
    return { store, state };
  }

  test('counted per printing, not per card', async () => {
    const { state } = await withStacks([
      { id: 'p-old', qty: 3 }, { id: 'p-new', qty: 1 },
    ]);
    const counts = await state.ownedCountsOf('Island');
    assert.equal(counts.get('p-old')?.total, 3);
    assert.equal(counts.get('p-new')?.total, 1);
    assert.equal(counts.get('p-mid'), undefined);
  });

  test('foils counted separately as well as together', async () => {
    const { state } = await withStacks([
      { id: 'p-old', qty: 2 }, { id: 'p-old', qty: 1, finish: 'foil' },
    ]);
    const held = (await state.ownedCountsOf('Island')).get('p-old');
    assert.equal(held.total, 3);
    assert.equal(held.foil, 1);
  });

  test('etched counts as shiny too', async () => {
    const { state } = await withStacks([
      { id: 'p-old', qty: 1, finish: 'etched' },
    ]);
    assert.equal((await state.ownedCountsOf('Island')).get('p-old').foil, 1);
  });

  test('another card is not counted', async () => {
    const { state } = await withStacks([
      { id: 'p-sol', name: 'Sol Ring', qty: 4 },
    ]);
    assert.equal((await state.ownedCountsOf('Island')).size, 0);
  });

  test('scoped to one collection when asked', async () => {
    // Only Mine can be pointed at one shelf, and the count has to
    // agree with the filter above it.
    const { state } = await withStacks([
      { id: 'p-old', qty: 2, where: 'binder' },
      { id: 'p-old', qty: 5, where: 'bulk' },
    ]);
    assert.equal((await state.ownedCountsOf('Island')).get('p-old').total, 7);
    assert.equal(
      (await state.ownedCountsOf('Island', 'binder')).get('p-old').total, 2);
  });

  test('owning none of anything is an empty answer, not a crash', async () => {
    const { state } = await withStacks([]);
    assert.equal((await state.ownedCountsOf('Island')).size, 0);
  });
});
