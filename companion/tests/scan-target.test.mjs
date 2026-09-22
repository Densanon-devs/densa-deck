/**
 * Where a scanned card goes.
 *
 * Asked for: "add the exact same scanning system into decks where
 * you can choose to scan a card from your collection directly into
 * the deck or scan a new card into selected collection and deck."
 *
 * Three destinations, one switch. Getting it wrong is silent in both
 * directions — filing when you meant not to inflates what you own,
 * not filing when you meant to loses a card you have just bought —
 * which is why the decision is here and not in the screen.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  askBeforeDeck,
  flashVerb,
  needsCollection,
  notOwnedLine,
  planFor,
} from '../src/lib/scan-target.ts';

describe('on the Scan tab, with no deck', () => {
  test('Add cards files into the collection', () => {
    assert.deepEqual(planFor('add', false),
      { file: true, deck: false, tag: false });
  });

  test('Tag what I own changes no quantities', () => {
    // The whole point of that mode: it groups, it does not add.
    assert.deepEqual(planFor('tag', false),
      { file: false, deck: false, tag: true });
  });

  test('neither puts anything in a deck', () => {
    assert.equal(planFor('add', false).deck, false);
    assert.equal(planFor('tag', false).deck, false);
  });
});

describe('opened from inside a deck', () => {
  test('From my collection puts it in the deck and files nothing', () => {
    // Walking a deck out of a box you have already catalogued. Filing
    // here would give you a second copy of every card you own.
    assert.deepEqual(planFor('tag', true),
      { file: false, deck: true, tag: false });
  });

  test('New card files it AND puts it in the deck', () => {
    // Fresh out of a booster: it is new to the collection and it is
    // going in the deck, and doing one without the other means going
    // round twice.
    assert.deepEqual(planFor('add', true),
      { file: true, deck: true, tag: false });
  });

  test('neither of them tags', () => {
    // Tagging is a third operation and it is not on offer here. A
    // plan that quietly did it as well would put cards in groups
    // nobody asked about.
    assert.equal(planFor('add', true).tag, false);
    assert.equal(planFor('tag', true).tag, false);
  });

  test('both always reach the deck, which is why you opened it', () => {
    assert.equal(planFor('add', true).deck, true);
    assert.equal(planFor('tag', true).deck, true);
  });
});

describe('the word on the flash', () => {
  test('says which of the three just happened', () => {
    assert.equal(flashVerb(planFor('add', true)), 'ADDED → DECK');
    assert.equal(flashVerb(planFor('tag', true)), 'INTO DECK');
    assert.equal(flashVerb(planFor('tag', false)), 'TAGGED');
    assert.equal(flashVerb(planFor('add', false)), 'ADDED');
  });

  test('no two of them read the same', () => {
    // A flash that said the same thing for all of them would be
    // lying about two, and the flash is the only confirmation there
    // is.
    const said = [
      flashVerb(planFor('add', true)),
      flashVerb(planFor('tag', true)),
      flashVerb(planFor('tag', false)),
      flashVerb(planFor('add', false)),
    ];
    assert.equal(new Set(said).size, said.length);
  });

  test('extra groups are still counted on the plain add', () => {
    assert.equal(flashVerb(planFor('add', false), 2), 'ADDED +2');
  });
});

describe('whether to show the collection picker', () => {
  test('yes wherever a card is filed or tagged', () => {
    assert.equal(needsCollection(planFor('add', false)), true);
    assert.equal(needsCollection(planFor('tag', false)), true);
    assert.equal(needsCollection(planFor('add', true)), true);
  });

  test('and no in the one mode that writes nothing', () => {
    // A control that does nothing reads as one that is broken.
    assert.equal(needsCollection(planFor('tag', true)), false);
  });
});

describe('a card the collection has never heard of', () => {
  /**
   * Asked for: warn that the card is not in the collection, offer to
   * add it to one, and keep the right to refuse and put it in the
   * deck anyway.
   *
   * "From my collection" is a claim about the card. When the claim
   * is wrong the deck fills with cards the collection does not
   * have, and the shortfall, the value and the cost-to-finish are
   * all computed against a collection missing them.
   */
  const none = { thisPrinting: 0, otherPrintings: 0 };
  const other = { thisPrinting: 0, otherPrintings: 4 };
  const have = { thisPrinting: 2, otherPrintings: 0 };

  test('it asks', () => {
    assert.equal(askBeforeDeck(planFor('tag', true), none), true);
  });

  test('and does not when you have that printing', () => {
    // The ordinary case, which must stay silent or the warning gets
    // dismissed without being read.
    assert.equal(askBeforeDeck(planFor('tag', true), have), false);
  });

  test('owning a DIFFERENT printing still asks', () => {
    // You are holding a specific card. Owning another printing of
    // it does not mean this one is recorded, and a deck slot naming
    // this printing against a collection holding another is exactly
    // the mismatch worth catching.
    assert.equal(askBeforeDeck(planFor('tag', true), other), true);
  });

  test('never in the mode that files anyway', () => {
    // "New card" is already filing it. Asking would be asking a
    // question that has just been answered.
    assert.equal(askBeforeDeck(planFor('add', true), none), false);
  });

  test('never on the Scan tab, where there is no deck', () => {
    assert.equal(askBeforeDeck(planFor('tag', false), none), false);
    assert.equal(askBeforeDeck(planFor('add', false), none), false);
  });
});

describe('what the warning says', () => {
  test('when you own none of it', () => {
    assert.equal(
      notOwnedLine('Ash Barrens', { thisPrinting: 0, otherPrintings: 0 }),
      'Ash Barrens is not in your collection yet.');
  });

  test('when you own another printing, which is a different sentence', () => {
    // Telling someone their card is missing while four of it are on
    // the shelf is how a warning stops being read.
    assert.equal(
      notOwnedLine('Island', { thisPrinting: 0, otherPrintings: 4 }),
      'You own 4 Island, but not this printing.');
  });
});
