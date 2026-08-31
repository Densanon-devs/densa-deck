/**
 * Ordering a collection on the phone, both ways round.
 *
 * The same contract as the desktop's `resolve_order`, because two ends of
 * one app disagreeing about what "sorted by price" means is worse than
 * neither offering it.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  NATURAL,
  SORT_LABELS,
  directionOf,
  sortCards,
} from '../src/lib/sorting.ts';

const CARDS = [
  { card_name: 'Sol Ring', printing_id: 'p-sol', quantity: 4, price_usd: 1.5 },
  { card_name: 'Big Thing', printing_id: 'p-big', quantity: 1, price_usd: 30 },
  { card_name: 'Middle Card', printing_id: 'p-mid', quantity: 2, price_usd: null },
];
const CMC = new Map([['p-sol', 1], ['p-big', 8], ['p-mid', 4]]);
const names = (rows) => rows.map((r) => r.card_name);

describe('mana value', () => {
  test('counts up by default', () => {
    assert.deepEqual(names(sortCards(CARDS, 'cmc', '', CMC)),
      ['Sol Ring', 'Middle Card', 'Big Thing']);
  });

  test('and reverses to count down', () => {
    assert.deepEqual(names(sortCards(CARDS, 'cmc', 'desc', CMC)),
      ['Big Thing', 'Middle Card', 'Sol Ring']);
  });

  test('a card the index does not cover sinks, not floats', () => {
    // Sorted naively, a missing value reverses to the top of the list.
    const partial = new Map([['p-sol', 1]]);
    assert.equal(names(sortCards(CARDS, 'cmc', 'desc', partial))[0], 'Sol Ring');
  });
});

describe('price', () => {
  test('starts high, because nobody looks for their cheapest card', () => {
    assert.equal(NATURAL.price, 'desc');
    assert.equal(names(sortCards(CARDS, 'price'))[0], 'Big Thing');
  });

  test('reverses to low first', () => {
    assert.equal(names(sortCards(CARDS, 'price', 'asc'))[0], 'Sol Ring');
  });

  test('an unpriced card is last BOTH ways', () => {
    // The trap the desktop has too: unknown is not "cheapest", and it is
    // certainly not "most valuable".
    for (const way of ['asc', 'desc']) {
      const order = names(sortCards(CARDS, 'price', way));
      assert.equal(order[order.length - 1], 'Middle Card', way);
    }
  });
});

describe('the rules a reverse must not break', () => {
  test('reversing is the same rows backwards, not a reshuffle', () => {
    const up = names(sortCards(CARDS, 'name', 'asc'));
    const down = names(sortCards(CARDS, 'name', 'desc'));
    assert.deepEqual(down, [...up].reverse());
  });

  test('ties stay alphabetical whichever way the list runs', () => {
    const tied = [
      { card_name: 'Zebra', printing_id: 'z', quantity: 1, price_usd: 5 },
      { card_name: 'Apple', printing_id: 'a', quantity: 1, price_usd: 5 },
    ];
    for (const way of ['asc', 'desc']) {
      assert.deepEqual(names(sortCards(tied, 'price', way)),
        ['Apple', 'Zebra'], way);
    }
  });

  test('it does not rearrange the array it was given', () => {
    const original = [...CARDS];
    sortCards(CARDS, 'price', 'desc');
    assert.deepEqual(CARDS, original);
  });
});

describe('every sort has both directions', () => {
  for (const key of Object.keys(NATURAL)) {
    test(`${key} reverses`, () => {
      const up = names(sortCards(CARDS, key, 'asc', CMC));
      const down = names(sortCards(CARDS, key, 'desc', CMC));
      assert.equal(up.length, 3);
      assert.equal(down.length, 3);
      // Where the values genuinely differ, the ends swap.
      if (key !== 'set') assert.notDeepEqual(up, down, key);
    });
  }

  test('and every one has a label to put on the control', () => {
    for (const key of Object.keys(NATURAL)) {
      assert.ok(SORT_LABELS[key], key);
    }
  });
});

describe('which arrow to show', () => {
  test('an unset direction shows the sort natural one', () => {
    assert.equal(directionOf('price', ''), 'desc');
    assert.equal(directionOf('cmc', ''), 'asc');
  });

  test('and an explicit one wins', () => {
    assert.equal(directionOf('price', 'asc'), 'asc');
  });
});
