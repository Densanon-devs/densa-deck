/**
 * A deck that knows which of its cards are foil.
 *
 * Asked for: "let's do the full accuracy so we can cover collections
 * properly but also have it so the deck list strips the reading
 * portion but it still internally tracks for pricing and showcase."
 *
 * So: the finish is real slot data, it is read out of anything
 * pasted in, it is kept out of the list a person reads, and it comes
 * home again across a trip through the text box. The same bargain
 * the Scryfall id has always had — the text cannot carry it, so
 * `carryPrintings` puts it back.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  addToDeck,
  removeFromDeck,
  carryPrintings,
  entryKey,
  formatDecklist,
  parseDecklist,
} from '../src/lib/decks.ts';

describe('reading a list that says foil', () => {
  test("Moxfield's marker", () => {
    const { cards } = parseDecklist('1 Sol Ring (CMM) 410 *F*');
    assert.equal(cards[0].name, 'Sol Ring');
    assert.equal(cards[0].finish, 'foil');
  });

  test('and the printing still comes through with it', () => {
    // The marker sits AFTER the set and number, so stripping it
    // second would stop the printing suffix matching at all.
    const { cards } = parseDecklist('1 Sol Ring (CMM) 410 *F*');
    assert.equal(cards[0].set_code, 'CMM');
    assert.equal(cards[0].collector_number, '410');
  });

  test('etched is its own thing', () => {
    assert.equal(parseDecklist('1 Sol Ring *E*').cards[0].finish, 'etched');
  });

  test('the spellings people write by hand', () => {
    assert.equal(parseDecklist('1 Sol Ring (foil)').cards[0].finish, 'foil');
    assert.equal(parseDecklist('1 Sol Ring [foil]').cards[0].finish, 'foil');
    assert.equal(parseDecklist('1 Sol Ring [etched]').cards[0].finish,
      'etched');
  });

  test('the marker never ends up in the name', () => {
    // There is no card called "Sol Ring *F*", and a name with markup
    // in it matches nothing in the index.
    for (const line of ['1 Sol Ring *F*', '1 Sol Ring (foil)',
      '1 Sol Ring (CMM) 410 *F*']) {
      assert.equal(parseDecklist(line).cards[0].name, 'Sol Ring');
    }
  });

  test('a plain line has no finish at all, not a nonfoil one', () => {
    // Absent and 'nonfoil' have to stay distinguishable in the data
    // even though they mean the same thing, or every deck ever saved
    // gets rewritten on its first save.
    assert.equal(parseDecklist('1 Sol Ring').cards[0].finish, undefined);
  });
});

describe('the list a person reads', () => {
  const FOILY = [
    { name: 'Sol Ring', qty: 1, set_code: 'CMM', collector_number: '410',
      finish: 'foil' },
    { name: 'Island', qty: 9 },
  ];

  test('does not carry the marker', () => {
    // Asked for directly. A decklist is something you read; `*F*`
    // down the side of it is markup for a machine.
    const text = formatDecklist(FOILY);
    assert.ok(!text.includes('*F*'), text);
    assert.match(text, /1 Sol Ring \(CMM\) 410/);
  });

  test('but an export can, when asked', () => {
    // The other direction: sending this somewhere that speaks *F*,
    // where leaving it out loses the fact rather than hiding it.
    const text = formatDecklist(FOILY, [], [], true);
    assert.match(text, /1 Sol Ring \(CMM\) 410 \*F\*/);
  });

  test('etched exports as its own marker', () => {
    const text = formatDecklist(
      [{ name: 'Sol Ring', qty: 1, finish: 'etched' }], [], [], true);
    assert.match(text, /\*E\*/);
  });

  test('a plain slot gets no marker either way', () => {
    const text = formatDecklist([{ name: 'Island', qty: 9 }], [], [], true);
    assert.equal(text.includes('*'), false);
  });
});

describe('and it survives the trip home', () => {
  const BEFORE = [
    { name: 'Sol Ring', qty: 1, printing_id: 'p-sol', set_code: 'CMM',
      collector_number: '410', finish: 'foil' },
    { name: 'Island', qty: 9, printing_id: 'p-isl', set_code: 'BLB',
      collector_number: '280' },
  ];

  test('the text drops it and carryPrintings puts it back', () => {
    const { cards } = parseDecklist(formatDecklist(BEFORE));
    assert.equal(cards.find((c) => c.name === 'Sol Ring').finish, undefined);

    const home = carryPrintings(cards, BEFORE);
    assert.equal(home.find((c) => c.name === 'Sol Ring').finish, 'foil');
  });

  test('along with the printing id, as before', () => {
    const home = carryPrintings(parseDecklist(formatDecklist(BEFORE)).cards, BEFORE);
    assert.equal(home.find((c) => c.name === 'Sol Ring').printing_id,
      'p-sol');
  });

  test('a plain card stays plain', () => {
    const home = carryPrintings(parseDecklist(formatDecklist(BEFORE)).cards, BEFORE);
    assert.equal(home.find((c) => c.name === 'Island').finish, undefined);
  });

  test('typing the marker in by hand is honoured', () => {
    // An explicit answer in the text is a decision, not a memory.
    const plain = carryPrintings(
      parseDecklist('1 Island (BLB) 280 *F*').cards, BEFORE);
    assert.equal(plain[0].finish, 'foil');
  });

  test('two finishes of one card in one deck are not guessed at', () => {
    /*
      The case the text genuinely cannot express. Both slots come
      back bare and there is no way to tell which line was the foil,
      so neither is claimed — visibly plain on screen rather than
      silently wrong in the total.
    */
    const both = [
      { name: 'Sol Ring', qty: 1, printing_id: 'p-sol', finish: 'foil' },
      { name: 'Sol Ring', qty: 1, printing_id: 'p-sol' },
    ];
    const home = carryPrintings(
      parseDecklist('1 Sol Ring\n1 Sol Ring').cards, both);
    for (const slot of home) assert.equal(slot.finish, undefined);
  });

  test('a deck with no previous state is left alone', () => {
    const home = carryPrintings(parseDecklist('1 Sol Ring *F*').cards, []);
    assert.equal(home[0].finish, 'foil');
  });
});

describe('two finishes of one card are two slots', () => {
  test('so a deck can hold both and count them apart', () => {
    const foil = { name: 'Sol Ring', qty: 1, printing_id: 'p', finish: 'foil' };
    const plain = { name: 'Sol Ring', qty: 1, printing_id: 'p' };
    assert.notEqual(entryKey(foil), entryKey(plain));
  });

  test('and parsing keeps them as two lines', () => {
    const { cards } = parseDecklist('1 Sol Ring *F*\n1 Sol Ring');
    assert.equal(cards.length, 2);
  });

  test('while two of the SAME finish still merge', () => {
    const { cards } = parseDecklist('1 Sol Ring *F*\n1 Sol Ring *F*');
    assert.equal(cards.length, 1);
    assert.equal(cards[0].qty, 2);
  });
});

describe('scanning a run of cards into one deck', () => {
  /**
   * Each scan builds on the deck as it is, not as it was when the
   * camera opened. The screen keeps the growing deck in a ref for
   * exactly this reason -- holding it in state would re-render a
   * live camera preview on every card AND hand the next scan a
   * decklist from before the last one went in, so each card would
   * overwrite the one before it.
   *
   * The React half of that cannot be tested here. This is the
   * library half: applying the same edit repeatedly has to
   * accumulate.
   */
  const scan = (deck, slot) => addToDeck(deck, slot);

  test('ten different cards make a deck of ten', () => {
    let deck = [];
    for (let i = 0; i < 10; i += 1) {
      deck = scan(deck, { name: `Card ${i}`, qty: 1, printing_id: `p${i}` });
    }
    assert.equal(deck.length, 10);
    assert.equal(deck.reduce((n, e) => n + e.qty, 0), 10);
  });

  test('the same card four times is one slot of four', () => {
    let deck = [];
    for (let i = 0; i < 4; i += 1) {
      deck = scan(deck, { name: 'Sol Ring', qty: 1, printing_id: 'p' });
    }
    assert.equal(deck.length, 1);
    assert.equal(deck[0].qty, 4);
  });

  test('a foil and a plain copy stay apart across a run', () => {
    // The finish is part of the key now, so a run holding both does
    // not merge them into one line.
    let deck = [];
    deck = scan(deck, { name: 'Sol Ring', qty: 1, printing_id: 'p',
                        finish: 'foil' });
    deck = scan(deck, { name: 'Sol Ring', qty: 1, printing_id: 'p' });
    deck = scan(deck, { name: 'Sol Ring', qty: 1, printing_id: 'p',
                        finish: 'foil' });
    assert.equal(deck.length, 2);
    assert.equal(deck.find((e) => e.finish === 'foil').qty, 2);
    assert.equal(deck.find((e) => !e.finish).qty, 1);
  });

  test('nothing already in the deck is lost', () => {
    // The failure the ref exists to prevent, stated at the level
    // where it can be asserted.
    const before = [{ name: 'Island', qty: 9, printing_id: 'i' }];
    const after = scan(before, { name: 'Sol Ring', qty: 1, printing_id: 'p' });
    assert.equal(after.find((e) => e.name === 'Island').qty, 9);
    assert.equal(after.length, 2);
  });
});

describe('adding and removing agree about what a slot is', () => {
  /**
   * `removeFromDeck` has keyed on the finish since the key learned
   * about it; `addToDeck` was still dropping it. So a scanned foil
   * went in as an ordinary copy -- merging with the plain one
   * already there -- and then could not be taken out again as a
   * foil, because no slot matched.
   *
   * Two halves of one pair disagreeing about what a slot is, which
   * is the kind of thing that only shows up as cards quietly going
   * missing.
   */
  test('a foil added is a foil in the deck', () => {
    const deck = addToDeck([], { name: 'Sol Ring', printing_id: 'p',
                                 finish: 'foil' });
    assert.equal(deck[0].finish, 'foil');
  });

  test('and can be taken back out', () => {
    let deck = addToDeck([], { name: 'Sol Ring', printing_id: 'p',
                               finish: 'foil' });
    deck = removeFromDeck(deck, { name: 'Sol Ring', printing_id: 'p',
                                  finish: 'foil' });
    assert.deepEqual(deck, []);
  });

  test('a plain copy is not stored as a nonfoil one', () => {
    // Absent and 'nonfoil' mean the same thing and have to produce
    // the same key, or every deck ever saved changes shape.
    const deck = addToDeck([], { name: 'Sol Ring', printing_id: 'p',
                                 finish: 'nonfoil' });
    assert.equal(deck[0].finish, undefined);
  });

  test('removing the plain one leaves the foil alone', () => {
    let deck = addToDeck([], { name: 'Sol Ring', printing_id: 'p',
                               finish: 'foil' });
    deck = addToDeck(deck, { name: 'Sol Ring', printing_id: 'p' });
    deck = removeFromDeck(deck, { name: 'Sol Ring', printing_id: 'p' });
    assert.equal(deck.length, 1);
    assert.equal(deck[0].finish, 'foil');
  });
});
