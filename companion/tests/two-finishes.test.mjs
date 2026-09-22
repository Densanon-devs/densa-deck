/**
 * Owning a foil AND a nonfoil of the same card.
 *
 * Asked directly: "what if I have a foil and not foil of the same
 * card currently — with all this doesn't it seem to assume all the
 * versions are the same?"
 *
 * Mostly no, and in one place yes, and the yes was worse than
 * assuming they are the same. The collection model keeps them apart
 * properly — finish is part of the stack key, so they are two stacks
 * and every count, add and remove treats them separately. But
 * `resolveSlots` built its owned-lookup with a plain `set` in a
 * loop, so a printing owned twice kept whichever stack the database
 * returned LAST. The deck's value flipped between $2 and $40 on row
 * order alone, with nothing on screen changing.
 *
 * Measured before the fix:
 *
 *   price used           : 40
 *   reversed stack order : 2
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { entryKey, resolveSlots } from '../src/lib/decks.ts';

const SLOT = { name: 'Sol Ring', qty: 1, printing_id: 'p-sol' };
const priceOf = (out) => out[entryKey(SLOT)]?.price_usd;

/** One printing, owned twice: the plain one and the shiny one. */
const BOTH = [
  { card_name: 'Sol Ring', printing_id: 'p-sol', finish: 'nonfoil',
    price_usd: 2 },
  { card_name: 'Sol Ring', printing_id: 'p-sol', finish: 'foil',
    price_usd: 40 },
];

describe('the same printing owned in two finishes', () => {
  test('the price does not depend on row order', () => {
    // The bug, stated so it cannot come back.
    const one = priceOf(resolveSlots([SLOT], BOTH, []));
    const other = priceOf(resolveSlots([SLOT], [...BOTH].reverse(), []));
    assert.equal(one, other);
  });

  test('and it is the lower of the two', () => {
    // The app cannot tell which copy is in the deck. A total that
    // guesses upward reads as a valuation and is a wish.
    assert.equal(priceOf(resolveSlots([SLOT], BOTH, [])), 2);
  });

  test('both finishes are reported, so nothing pretends otherwise', () => {
    const facts = resolveSlots([SLOT], BOTH, [])[entryKey(SLOT)];
    assert.deepEqual([...facts.owned_finishes].sort(), ['foil', 'nonfoil']);
  });
});

describe('owning only the foil', () => {
  const FOIL_ONLY = [
    { card_name: 'Sol Ring', printing_id: 'p-sol', finish: 'foil' },
  ];

  test('it is priced as a foil', () => {
    // The copy in the box IS the foil, so the foil number is not a
    // guess. Everything took `price_usd` -- the nonfoil price -- for
    // every copy, whatever was actually on the shelf.
    const out = resolveSlots([SLOT], FOIL_ONLY, [{
      printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
      price_usd: null, price_usd_foil: 40, found: true,
    }]);
    assert.equal(priceOf(out), 40);
  });

  test('owning the plain one keeps the plain price', () => {
    const out = resolveSlots(
      [SLOT],
      [{ card_name: 'Sol Ring', printing_id: 'p-sol', finish: 'nonfoil' }],
      [{
        printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
        price_usd: 2, price_usd_foil: 40, found: true,
      }]);
    assert.equal(priceOf(out), 2);
  });

  test('owning both takes the plain price, not the foil one', () => {
    const out = resolveSlots([SLOT], BOTH, [{
      printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
      price_usd: null, price_usd_foil: 40, found: true,
    }]);
    assert.equal(priceOf(out), 2);
  });

  test('the foil price beats a per-stack figure', () => {
    /*
      A judgement call, so it is written down.

      Three numbers can price this slot and only one of them knows
      what finish is in the box. The other two are the printing's
      nonfoil price under different names, and preferring either put
      a ten-to-one error on the total.

      The phone never writes `stacks.price_usd` -- the column exists
      and nothing fills it -- so on a standalone phone this ordering
      is a contract rather than something observable. It matters the
      day a desktop starts syncing per-stack prices.
    */
    const out = resolveSlots(
      [SLOT],
      [{ card_name: 'Sol Ring', printing_id: 'p-sol', finish: 'foil',
         price_usd: 33 }],
      [{
        printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
        price_usd: null, price_usd_foil: 40, found: true,
      }]);
    assert.equal(priceOf(out), 40);
  });

  test('no foil price known leaves it unpriced, not wrong', () => {
    const out = resolveSlots([SLOT], FOIL_ONLY, [{
      printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
      price_usd: null, found: true,
    }]);
    assert.equal(priceOf(out), null);
  });
});

describe('a slot that says which finish', () => {
  /*
    The limit this used to document is gone: a deck can record "my
    FOIL Sol Ring" now, and the slot's own answer beats inferring one
    from what happens to be on the shelf -- which worked while you
    owned one of them and could not once you owned both.
  */
  test('a foil slot is a different slot', () => {
    assert.notEqual(entryKey({ ...SLOT, finish: 'foil' }), entryKey(SLOT));
  });

  test('and nonfoil is still the plain key', () => {
    /*
      Not cosmetic. Appending `nonfoil` to every key would change the
      key of every slot in every deck ever saved -- cached slot facts,
      quantities, the lot -- on the first load after this update.
      Absent and 'nonfoil' have to produce the same string.
    */
    assert.equal(entryKey({ ...SLOT, finish: 'nonfoil' }), entryKey(SLOT));
    assert.equal(entryKey({ ...SLOT, finish: '' }), entryKey(SLOT));
    assert.equal(entryKey(SLOT), 'sol ring\u0000p-sol');
  });

  test('the slot beats the shelf', () => {
    // You own both, so the shelf cannot say. The slot can.
    const out = resolveSlots([{ ...SLOT, finish: 'foil' }], BOTH, [{
      printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
      price_usd: 2, price_usd_foil: 40, found: true,
    }]);
    assert.equal(out[entryKey({ ...SLOT, finish: 'foil' })].price_usd, 40);
  });

  test('a slot that says plain is priced plain, whatever you own', () => {
    const out = resolveSlots([{ ...SLOT, finish: 'nonfoil' }], [
      { card_name: 'Sol Ring', printing_id: 'p-sol', finish: 'foil' },
    ], [{
      printing_id: 'p-sol', set_code: 'cmm', collector_number: '410',
      price_usd: 2, price_usd_foil: 40, found: true,
    }]);
    assert.equal(out[entryKey({ ...SLOT, finish: 'nonfoil' })].price_usd, 2);
  });
});
