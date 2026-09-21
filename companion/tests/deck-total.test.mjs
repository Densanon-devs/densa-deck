/**
 * How big a Commander deck is.
 *
 * A hundred cards, and the commander is one of them. Choosing one
 * moves the card out of the list into its own slot -- correctly, or it
 * would be in two places and fail its own size check -- and the count
 * then read the list alone. A sixty-card deck showed fifty-nine with
 * the commander sitting above it as an apparent sixtieth. The deck had
 * not changed size; only the arithmetic had.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { deckSize, totalCards } from '../src/lib/decks.ts';

const card = (name, qty = 1) => ({ name, qty });

describe('counting a deck with a commander', () => {
  test('the commander counts toward the total', () => {
    const deck = {
      decklist: [card('Island', 59)],
      commander: [card('Lazav, the Multifarious')],
    };
    assert.equal(totalCards(deck), 60,
      'the deck did not get smaller by naming its commander');
  });

  test('and the list alone is one short, which is the bug', () => {
    // Stated so the difference between the two is on the record.
    const deck = {
      decklist: [card('Island', 59)],
      commander: [card('Lazav, the Multifarious')],
    };
    assert.equal(deckSize(deck.decklist), 59);
    assert.notEqual(deckSize(deck.decklist), totalCards(deck));
  });

  test('a full Commander deck is a hundred, not ninety-nine', () => {
    const deck = {
      decklist: [card('Island', 99)],
      commander: [card('Lazav, the Multifarious')],
    };
    assert.equal(totalCards(deck), 100);
  });

  test('a format with no commander is unaffected', () => {
    assert.equal(totalCards({ decklist: [card('Island', 60)] }), 60);
    assert.equal(totalCards({ decklist: [card('Island', 60)], commander: [] }),
      60);
  });

  test('a partner pair counts as two', () => {
    // Two commanders and ninety-eight others is still a hundred.
    const deck = {
      decklist: [card('Island', 98)],
      commander: [card('Tymna the Weaver'), card('Thrasios, Triton Hero')],
    };
    assert.equal(totalCards(deck), 100);
  });

  test('nothing at all is zero rather than a crash', () => {
    assert.equal(totalCards(null), 0);
    assert.equal(totalCards(undefined), 0);
    assert.equal(totalCards({}), 0);
  });
});
