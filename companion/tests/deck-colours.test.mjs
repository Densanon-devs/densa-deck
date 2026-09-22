/**
 * What colours a deck is, and how much of each.
 *
 * Asked for while looking at a deck. The pips alone answer "is this
 * a Golgari deck"; the counts answer the question people actually
 * have in front of a decklist — whether it is a green deck
 * splashing black or an even pair — and that is a mana-base
 * decision rather than a label.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { deckColours, entryKey } from '../src/lib/decks.ts';

/** Build the slot-facts map the screen passes in. */
function factsFor(rows) {
  const out = {};
  for (const [entry, identity] of rows) {
    out[entryKey(entry)] = { printing_id: 'p', color_identity: identity };
  }
  return out;
}

const FOREST = { name: 'Forest', qty: 9 };
const SWAMP = { name: 'Swamp', qty: 3 };
const GOLGARI = { name: 'Putrefy', qty: 2 };
const ROCK = { name: 'Sol Ring', qty: 1 };

describe('counting a deck', () => {
  test('one colour', () => {
    assert.deepEqual(
      deckColours([FOREST], factsFor([[FOREST, ['G']]])),
      [{ colour: 'G', cards: 9 }]);
  });

  test('in Magic order rather than alphabetical', () => {
    /*
      Everyone already reads WUBRG; sorting it any other way makes a
      reader translate before they can look.

      Chosen so the two orders DISAGREE. B before G is true either
      way, and a test that used those would pass against an
      alphabetical sort -- which is exactly what it did until a
      mutation went green and said so.
    */
    const plains = { name: 'Plains', qty: 5 };
    const slots = factsFor([[SWAMP, ['B']], [plains, ['W']]]);
    assert.deepEqual(deckColours([SWAMP, plains], slots).map((c) => c.colour),
      ['W', 'B']);
  });

  test('all five, in that order', () => {
    const one = (name, pip) => [{ name, qty: 1 }, [pip]];
    const rows = [one('a', 'W'), one('b', 'U'), one('c', 'B'),
      one('d', 'R'), one('e', 'G')];
    const slots = factsFor(rows);
    assert.deepEqual(
      deckColours(rows.map(([entry]) => entry), slots).map((c) => c.colour),
      ['W', 'U', 'B', 'R', 'G']);
  });

  test('and colourless sits last, after the five', () => {
    const rows = [[{ name: 'a', qty: 1 }, []], [{ name: 'b', qty: 1 }, ['W']]];
    const slots = factsFor(rows);
    assert.deepEqual(
      deckColours(rows.map(([entry]) => entry), slots).map((c) => c.colour),
      ['W', 'C']);
  });

  test('by copies, not by slot', () => {
    // Nine Forests are nine green cards. Counting the slot would
    // make every mana base look like a splash.
    const slots = factsFor([[FOREST, ['G']], [SWAMP, ['B']]]);
    assert.deepEqual(deckColours([FOREST, SWAMP], slots), [
      { colour: 'B', cards: 3 },
      { colour: 'G', cards: 9 },
    ]);
  });

  test('a gold card counts for both of its colours', () => {
    // It IS both. A deck with eight Golgari cards is not a
    // four-card deck.
    const slots = factsFor([[GOLGARI, ['B', 'G']]]);
    assert.deepEqual(deckColours([GOLGARI], slots), [
      { colour: 'B', cards: 2 },
      { colour: 'G', cards: 2 },
    ]);
  });

  test('colourless is its own pip', () => {
    assert.deepEqual(deckColours([ROCK], factsFor([[ROCK, []]])),
      [{ colour: 'C', cards: 1 }]);
  });

  test('a colour with nothing in it is not listed', () => {
    // An empty pip is a colour the deck does not play, and showing
    // five of them makes the two that matter harder to see.
    const out = deckColours([FOREST], factsFor([[FOREST, ['G']]]));
    assert.equal(out.length, 1);
  });
});

describe('what it refuses to guess', () => {
  test('a slot with no facts yet is left out, not called colourless', () => {
    /*
      "We do not know yet" and "this is an artifact" are different
      things, and one of them resolves itself a moment later. Counting
      an unresolved slot as C would make every deck flash colourless
      while its facts loaded.
    */
    assert.deepEqual(deckColours([FOREST], {}), []);
  });

  test('a slot resolved with no colours IS colourless', () => {
    assert.deepEqual(deckColours([ROCK], factsFor([[ROCK, []]])),
      [{ colour: 'C', cards: 1 }]);
  });

  test('a quantity of zero counts for nothing', () => {
    const empty = { name: 'Forest', qty: 0 };
    assert.deepEqual(deckColours([empty], factsFor([[empty, ['G']]])), []);
  });

  test('an empty deck has no colours', () => {
    assert.deepEqual(deckColours([], {}), []);
    assert.deepEqual(deckColours(), []);
  });

  test('junk in the identity is ignored rather than shown', () => {
    // The two index sources spell colours differently and one of
    // them brackets them. A pip called "[" helps nobody.
    const slots = factsFor([[FOREST, ['G', '[', ',']]]);
    assert.deepEqual(deckColours([FOREST], slots), [{ colour: 'G', cards: 9 }]);
  });
});
