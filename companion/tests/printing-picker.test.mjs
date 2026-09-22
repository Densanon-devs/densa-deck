/**
 * Choosing between printings of one card.
 *
 * The list is short after a scan and very long after typing a name:
 * 671 Islands. Sorting those newest-first makes the top of the list
 * reasonable and leaves the other 631 unreachable, so the set box in
 * front of the sort is the only way to the rest of them.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  matchesSet,
  orderPrintings,
  pickerCount,
  variantsLine,
  yoursFirst,
} from '../src/lib/printing-picker.ts';

const SETS = {
  blb: { name: 'Bloomburrow', year: 2024 },
  lea: { name: 'Limited Edition Alpha', year: 1993 },
  ice: { name: 'Ice Age', year: 1995 },
  zzz: { name: '' },
};

const ROWS = [
  { printing_id: 'a', set_code: 'lea' },
  { printing_id: 'b', set_code: 'blb' },
  { printing_id: 'c', set_code: 'ice' },
  { printing_id: 'd', set_code: 'zzz' },
];

describe('the set box', () => {
  test('matches the code printed on the card', () => {
    assert.equal(matchesSet({ set_code: 'blb' }, 'blb', SETS), true);
  });

  test('and the name nobody sees printed anywhere', () => {
    // People know a set by whichever of the two they have seen most.
    assert.equal(matchesSet({ set_code: 'blb' }, 'bloom', SETS), true);
  });

  test('case does not matter either way', () => {
    assert.equal(matchesSet({ set_code: 'BLB' }, 'bloom', SETS), true);
    assert.equal(matchesSet({ set_code: 'blb' }, 'BLOOM', SETS), true);
  });

  test('an empty box matches everything', () => {
    // It is a filter, not a requirement.
    assert.equal(matchesSet({ set_code: 'blb' }, '', SETS), true);
    assert.equal(matchesSet({ set_code: 'blb' }, '   ', SETS), true);
  });

  test('a set the phone has no name for still matches by code', () => {
    assert.equal(matchesSet({ set_code: 'zzz' }, 'zzz', SETS), true);
  });

  test('something that matches nothing matches nothing', () => {
    assert.equal(matchesSet({ set_code: 'blb' }, 'kamigawa', SETS), false);
  });
});

describe('the order', () => {
  test('newest first', () => {
    assert.deepEqual(orderPrintings(ROWS, '', SETS).map((r) => r.printing_id),
      ['b', 'c', 'a', 'd']);
  });

  test('a set with no date sinks rather than floating', () => {
    // An unknown year is not 1993. Of the two wrong answers, last is
    // the one that does not push real recent printings off the page.
    const out = orderPrintings(ROWS, '', SETS);
    assert.equal(out[out.length - 1].printing_id, 'd');
  });

  test('filtering happens before the sort, not after', () => {
    assert.deepEqual(
      orderPrintings(ROWS, 'ice', SETS).map((r) => r.printing_id), ['c']);
  });

  test('the input is not reordered underneath the caller', () => {
    const before = ROWS.map((r) => r.printing_id);
    orderPrintings(ROWS, '', SETS);
    assert.deepEqual(ROWS.map((r) => r.printing_id), before);
  });

  test('no rows, no crash', () => {
    assert.deepEqual(orderPrintings([], '', SETS), []);
    assert.deepEqual(orderPrintings(undefined, '', SETS), []);
  });

  test('no set directory at all still returns the rows', () => {
    // The phone has no set list until it has fetched one, and a
    // picker that showed nothing until then would look broken.
    assert.equal(orderPrintings(ROWS, '').length, 4);
  });
});

describe('saying how many there are', () => {
  test('a short list says what it is', () => {
    assert.equal(pickerCount(3, 3, 40), '3 printings');
    assert.equal(pickerCount(1, 1, 40), '1 printing');
  });

  test('a long list admits it is showing a slice', () => {
    // Forty of 671 rows with nothing said reads as "these are the
    // printings". They are six per cent of them.
    assert.equal(pickerCount(671, 671, 40), '671 printings, 40 shown');
  });

  test('a filtered list counts what survived', () => {
    assert.equal(pickerCount(671, 12, 40), '12 of 671 match');
  });

  test('a filter that matched nothing says so plainly', () => {
    assert.equal(pickerCount(671, 0, 40), '0 of 671 match');
  });
});

describe('the versions you own come first', () => {
  /**
   * The pager lists every printing newest-first, which is right for a
   * card you are choosing to acquire and wrong for one you already
   * have: the copy in your box is the one going on the table, and it
   * should not be four swipes in behind reprints you never held.
   */
  const ROWS = [
    { printing_id: 'new', set_code: 'blb' },
    { printing_id: 'mid', set_code: 'ice' },
    { printing_id: 'old', set_code: 'lea' },
  ];

  test('an owned printing moves to the front', () => {
    assert.deepEqual(
      yoursFirst(ROWS, new Set(['mid'])).map((r) => r.printing_id),
      ['mid', 'new', 'old']);
  });

  test('several owned keep their order among themselves', () => {
    // Newest-first still decides between two you own.
    assert.deepEqual(
      yoursFirst(ROWS, new Set(['old', 'new'])).map((r) => r.printing_id),
      ['new', 'old', 'mid']);
  });

  test('owning none changes nothing', () => {
    assert.deepEqual(yoursFirst(ROWS, new Set()).map((r) => r.printing_id),
      ['new', 'mid', 'old']);
  });

  test('the input is not reordered underneath the caller', () => {
    const before = ROWS.map((r) => r.printing_id);
    yoursFirst(ROWS, new Set(['old']));
    assert.deepEqual(ROWS.map((r) => r.printing_id), before);
  });
});

describe('with Only Mine on, only yours', () => {
  const ROWS = [
    { printing_id: 'new', set_code: 'blb' },
    { printing_id: 'mid', set_code: 'ice' },
    { printing_id: 'old', set_code: 'lea' },
  ];

  test('the rest are not shown at all', () => {
    // Not an ordering problem: the filter means "build from what I
    // have", so offering a version you do not own contradicts it.
    assert.deepEqual(
      yoursFirst(ROWS, new Set(['mid']), true).map((r) => r.printing_id),
      ['mid']);
  });

  test('two owned printings both show', () => {
    assert.equal(yoursFirst(ROWS, new Set(['mid', 'old']), true).length, 2);
  });

  test('owning it by name with no printing recorded shows all', () => {
    // A stack filed before printing ids were kept, or one whose
    // printing is not in this phone's index. The card IS owned and no
    // variant can be matched to it, and an empty pager is a worse
    // answer than a full one. Lose detail, never the screen.
    assert.equal(yoursFirst(ROWS, new Set(), true).length, 3);
  });

  test('an owned printing this index does not hold shows all', () => {
    assert.equal(yoursFirst(ROWS, new Set(['not-here']), true).length, 3);
  });
});

describe('the line above the pager', () => {
  test('says which list this is when it has been cut', () => {
    assert.equal(variantsLine(3, 41, true),
      '3 of 41 printings — the ones you own');
  });

  test('and the plain one otherwise', () => {
    assert.equal(variantsLine(41, 41, false),
      '41 printings — swipe to see them');
  });

  test('Only Mine that cut nothing does not claim it did', () => {
    // You own every printing there is. Saying "3 of 3 you own" is
    // technically true and reads as a filter having done something.
    assert.equal(variantsLine(3, 3, true),
      '3 printings — swipe to see them');
  });

  test('one printing needs no line, because there is nothing to swipe', () => {
    assert.equal(variantsLine(1, 41, true), '');
  });
});
