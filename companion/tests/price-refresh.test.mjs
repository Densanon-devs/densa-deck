/**
 * Keeping prices current without re-downloading the world.
 *
 * Prices ride in with the card index, because the bulk file carries
 * them and every row is already being read. That is the right way to
 * get a first number for a hundred thousand printings and the wrong
 * way to keep them: the file is 78 MB and prices move daily.
 *
 * The prices worth keeping current are the ones attached to something
 * you have. Scryfall answers seventy-five at a time, so a collection
 * of 88 cards is two requests rather than a gigabyte a fortnight.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  BATCH,
  PRICE_MAX_AGE_MS,
  priceBatches,
  pricedInWords,
  pricesDue,
} from '../src/lib/price-refresh.ts';

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const NOW = Date.parse('2026-09-21T12:00:00Z');

describe('splitting a collection into requests', () => {
  test('a small collection is one request', () => {
    assert.deepEqual(priceBatches(['a', 'b', 'c']), [['a', 'b', 'c']]);
  });

  test('88 cards is two, because Scryfall takes 75', () => {
    // The size that reported the pricing problem.
    const ids = Array.from({ length: 88 }, (_, i) => `p${i}`);
    const batches = priceBatches(ids);
    assert.equal(batches.length, 2);
    assert.equal(batches[0].length, BATCH);
    assert.equal(batches[1].length, 88 - BATCH);
  });

  test('exactly 75 is one request, not two', () => {
    assert.equal(priceBatches(Array.from({ length: 75 }, (_, i) => `p${i}`))
      .length, 1);
  });

  test('duplicates are asked about once', () => {
    // Four copies of a card is one printing and one price.
    assert.deepEqual(priceBatches(['a', 'a', 'b', 'a']), [['a', 'b']]);
  });

  test('blanks are dropped rather than sent', () => {
    // A stack filed before printing ids were recorded has none, and
    // an empty identifier makes Scryfall reject the whole request.
    assert.deepEqual(priceBatches(['a', '', '  ', null, undefined, 'b']),
      [['a', 'b']]);
  });

  test('nothing owned is no requests at all', () => {
    assert.deepEqual(priceBatches([]), []);
  });
});

describe('when prices are worth fetching again', () => {
  test('never fetched is due immediately', () => {
    // A phone whose index predates prices has none; waiting a week
    // for a first number would be a strange way to treat it.
    assert.equal(pricesDue(0, NOW), true);
  });

  test('fresh prices are left alone', () => {
    assert.equal(pricesDue(NOW - HOUR, NOW), false);
    assert.equal(pricesDue(NOW - 6 * DAY, NOW), false);
  });

  test('a week old is due', () => {
    assert.equal(pricesDue(NOW - PRICE_MAX_AGE_MS - 1, NOW), true);
  });

  test('a clock that went backwards does not lock refreshes out', () => {
    // A restored backup or a timezone change puts the stamp in the
    // future, and `now - then` goes negative for as long as it takes
    // the date to catch up.
    assert.equal(pricesDue(NOW + 30 * DAY, NOW), true);
  });
});

describe('saying how old the prices are', () => {
  test('never, when they have never been fetched', () => {
    assert.equal(pricedInWords(0, NOW), 'never');
  });

  test('hours, then days, then months', () => {
    assert.equal(pricedInWords(NOW - 5 * 60 * 1000, NOW), 'just now');
    assert.equal(pricedInWords(NOW - 3 * HOUR, NOW), '3 hours ago');
    assert.equal(pricedInWords(NOW - DAY, NOW), 'yesterday');
    assert.equal(pricedInWords(NOW - 5 * DAY, NOW), '5 days ago');
    assert.equal(pricedInWords(NOW - 60 * DAY, NOW), 'about 2 months ago');
  });
});
