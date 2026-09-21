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

import { deckSize, deckWarnings, totalCards } from '../src/lib/decks.ts';

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

describe('the size warning counts the commander too', () => {
  /**
   * The header said 83 cards and the warning underneath said "82 cards
   * in the deck — 100 needed", about the same deck, two lines apart.
   *
   * `totalCards` fixed the header; `deckWarnings` never received the
   * commander at all, so its arithmetic could not have included it.
   * Two places computing the size, one of them told less than the
   * other.
   */
  test('a 99 + commander deck is a hundred and warns about nothing', () => {
    const warnings = deckWarnings(
      [card('Island', 99)], [], 'commander', undefined,
      [card('Lazav, the Multifarious')]);
    assert.deepEqual(warnings.filter((w) => w.kind === 'size'), []);
  });

  test('and the count in the warning matches the header', () => {
    // 82 in the list plus the commander is 83, which is what the
    // header says. The warning said 82.
    const warnings = deckWarnings(
      [card('Island', 82)], [], 'commander', undefined,
      [card('Lazav, the Multifarious')]);
    const size = warnings.find((w) => w.kind === 'size');
    assert.match(size.text, /^83 cards/,
      `warning disagreed with the header: ${size.text}`);
  });

  test('a deck with no commander is counted as before', () => {
    const warnings = deckWarnings([card('Island', 58)], [], 'commander');
    const size = warnings.find((w) => w.kind === 'size');
    assert.match(size.text, /^58 cards/);
  });

  test('the commander counts against the singleton rule', () => {
    // One copy of a card INCLUDING the commander. A leftover copy in
    // the 99 is an illegal deck and nothing said so.
    const warnings = deckWarnings(
      [card('Lazav, the Multifarious', 1)], [], 'commander', undefined,
      [card('Lazav, the Multifarious')]);
    assert.ok(warnings.some((w) => w.kind === 'copies'),
      'two Lazavs is two Lazavs');
  });
});
