/**
 * A card-index page that does not carry a field leaves that field alone.
 *
 * Audit finding: fetching the index from the PC wiped artist, prices,
 * release year and finishes on the phone. The PC has no artist column and
 * older PCs send six fields of eleven, and putCatalogue overwrote every
 * column with whatever arrived -- null or ''.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { LocalStore } from '../src/lib/store.ts';
import { RealDatabase } from './real-sqlite.mjs';

async function freshStore() {
  const store = new LocalStore(new RealDatabase());
  await store.init();
  return store;
}

const FULL = ['p-sol', 'Sol Ring', 'cmm', '410', 1, 'uncommon',
  1.48, 3.25, 'Mark Tedin', 2023, 'nonfoil,foil'];

test('a six-field page keeps the richer data already stored', async () => {
  const store = await freshStore();
  await store.putCatalogue([FULL]);
  // What an older PC sends: no prices, artist, year or finishes.
  await store.putCatalogue([['p-sol', 'Sol Ring', 'cmm', '410', 1, 'uncommon']]);

  const [row] = await store.printingsByName('Sol Ring');
  assert.equal(row.artist, 'Mark Tedin');
  assert.equal(row.released_year, 2023);
  assert.equal(row.price_usd, 1.48);
  assert.equal(row.price_usd_foil, 3.25);
  assert.equal(row.finishes, 'nonfoil,foil');
});

test("the PC's page (no artist) keeps the artist but updates the price", async () => {
  const store = await freshStore();
  await store.putCatalogue([FULL]);
  await store.putCatalogue([['p-sol', 'Sol Ring', 'cmm', '410', 1, 'uncommon',
    1.99, null, null, 2023, 'nonfoil,foil']]);

  const [row] = await store.printingsByName('Sol Ring');
  assert.equal(row.artist, 'Mark Tedin');
  assert.equal(row.price_usd, 1.99, 'a price that DID arrive must replace the old one');
  // A row that carries the price columns is authoritative for them: the
  // PC sends foil prices, so its null means there isn't one.
  assert.equal(row.price_usd_foil, null);
});

test('a brand-new printing from a short page still goes in', async () => {
  const store = await freshStore();
  await store.putCatalogue([['p-bolt', 'Lightning Bolt', 'lea', '161', 1, 'common']]);
  const [row] = await store.printingsByName('Lightning Bolt');
  assert.equal(row.printing_id, 'p-bolt');
});

test('a full row whose price has gone clears the price', async () => {
  // Keeping the old value is only for rows that do not carry prices at
  // all; a real null from Scryfall must not leave a stale price showing.
  const store = await freshStore();
  await store.putCatalogue([FULL]);
  await store.putCatalogue([['p-sol', 'Sol Ring', 'cmm', '410', 1, 'uncommon',
    null, null, 'Mark Tedin', 2023, 'nonfoil,foil']]);
  const [row] = await store.printingsByName('Sol Ring');
  assert.equal(row.price_usd, null);
  assert.equal(row.price_usd_foil, null);
});
