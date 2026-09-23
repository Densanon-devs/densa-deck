/**
 * Reading a list of decks at a glance.
 *
 * Four decks named after their commanders all read the same as a
 * column of text. The painting and the colours are what make a list
 * legible without reading it, which is what a list is for.
 *
 * The colours on the list come from a batched name lookup rather
 * than from resolving every deck's slots -- that would be a round of
 * queries per deck per card to draw one screen -- and they are fed
 * into the same counting function the deck screen uses, so the two
 * cannot disagree about what colour a deck is.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { deckColours, entryKey } from '../src/lib/decks.ts';
import { LocalStore } from '../src/lib/store.ts';
import { RealDatabase } from './real-sqlite.mjs';

/** [oracle_id, name, type_line, text, mana_cost, cmc, identity] */
const ORACLE = [
  ['o-1', 'Forest', 'Basic Land', '', '', 0, 'G'],
  ['o-2', 'Swamp', 'Basic Land', '', '', 0, 'B'],
  ['o-3', 'Putrefy', 'Instant', '', '{1}{B}{G}', 3, 'BG'],
  ['o-4', 'Sol Ring', 'Artifact', '', '{1}', 1, ''],
];

async function indexed() {
  const store = new LocalStore(new RealDatabase());
  await store.init();
  await store.putOracle(ORACLE);
  return store;
}

describe('looking up a pile of identities at once', () => {
  test('one query answers for every name given', async () => {
    const store = await indexed();
    const out = await store.identitiesFor(['Forest', 'Swamp', 'Putrefy']);
    assert.deepEqual(out.get('forest'), ['G']);
    assert.deepEqual(out.get('swamp'), ['B']);
    assert.deepEqual(out.get('putrefy'), ['B', 'G']);
  });

  test('a colourless card answers with no colours, not with nothing', async () => {
    // Absent means "not in the index"; an empty list means "this
    // card has no colours". The deck list draws a C pip for one and
    // nothing at all for the other.
    const store = await indexed();
    const out = await store.identitiesFor(['Sol Ring']);
    assert.deepEqual(out.get('sol ring'), []);
    assert.equal(out.has('nonesuch'), false);
  });

  test('names are matched however they were typed', async () => {
    const store = await indexed();
    const out = await store.identitiesFor(['Forest']);
    assert.ok(out.has('forest'));
  });

  test('duplicates are asked about once', async () => {
    const store = await indexed();
    const out = await store.identitiesFor(['Forest', 'Forest', 'Forest']);
    assert.equal(out.size, 1);
  });

  test('an empty list asks nothing and answers nothing', async () => {
    const store = await indexed();
    assert.equal((await store.identitiesFor([])).size, 0);
  });

  test('every chunk is asked about, not just the first', async () => {
    /*
      The lookup chunks because SQLite limits how many bound
      parameters one statement may carry -- 999 on the build Android
      ships, 32766 on Node's. So a test here cannot make the real
      limit bite; what it CAN test is the loop, which is the part
      that could be written wrong.

      Four names at a chunk size of one: if only the first slice
      were queried, three of them would come back missing.
    */
    const store = await indexed();
    const out = await store.identitiesFor(
      ['Forest', 'Swamp', 'Putrefy', 'Sol Ring'], 1);
    assert.equal(out.size, 4);
    assert.deepEqual(out.get('putrefy'), ['B', 'G']);
    assert.deepEqual(out.get('sol ring'), []);
  });

  test('an odd last chunk is not dropped', async () => {
    // Three names, two at a time: the leftover one is the case an
    // off-by-one in the stride loses.
    const store = await indexed();
    const out = await store.identitiesFor(['Forest', 'Swamp', 'Putrefy'], 2);
    assert.equal(out.size, 3);
    assert.deepEqual(out.get('putrefy'), ['B', 'G']);
  });
});

describe('and turning them into a deck’s colours', () => {
  test('the list agrees with the deck screen', async () => {
    /*
      The list builds slot-shaped facts from the name lookup and
      hands them to the SAME counting function the deck screen uses.
      Two screens disagreeing about what colour a deck is would be
      worse than neither of them saying.
    */
    const store = await indexed();
    const entries = [
      { name: 'Forest', qty: 9 },
      { name: 'Putrefy', qty: 2 },
      { name: 'Sol Ring', qty: 1 },
    ];
    const identities = await store.identitiesFor(entries.map((e) => e.name));
    const slots = {};
    for (const entry of entries) {
      const identity = identities.get(entry.name.toLowerCase());
      if (!identity) continue;
      slots[entryKey(entry)] = { printing_id: '', color_identity: identity };
    }
    assert.deepEqual(deckColours(entries, slots), [
      { colour: 'B', cards: 2 },
      { colour: 'G', cards: 11 },
      { colour: 'C', cards: 1 },
    ]);
  });

  test('a card the index has never seen is left out, not guessed', async () => {
    const store = await indexed();
    const entries = [{ name: 'Nonesuch', qty: 4 }];
    const identities = await store.identitiesFor(['Nonesuch']);
    const slots = {};
    for (const entry of entries) {
      const identity = identities.get(entry.name.toLowerCase());
      if (identity) {
        slots[entryKey(entry)] = { printing_id: '', color_identity: identity };
      }
    }
    assert.deepEqual(deckColours(entries, slots), []);
  });
});
