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

describe('what a deck slot still cannot say', () => {
  test('it has no finish of its own', () => {
    /*
      A real limit, written down rather than papered over. `entryKey`
      is name plus printing, so a deck cannot record "my FOIL Sol
      Ring" -- only "Sol Ring, this printing". Everything above
      infers the finish from what is on the shelf, which works while
      you own one of them and cannot while you own both.
    */
    assert.equal(entryKey(SLOT), 'sol ring\u0000p-sol');
    assert.equal(entryKey({ ...SLOT, finish: 'foil' }), entryKey(SLOT));
  });
});
