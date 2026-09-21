/**
 * Buzzing once per card, not once per photograph.
 *
 * The camera shutter clicked on every auto-scan frame, which is a
 * constant rattle reporting nothing — most frames hold no card at all.
 * The replacement has to be felt when a card is SEEN, pass or fail, and
 * stay quiet while that same card sits in frame; otherwise it is the
 * rattle again with a motor behind it.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { BuzzGuard, SAME_CARD_MS } from '../src/lib/buzz-policy.ts';

const T = 1_000_000;
const nothing = { kind: 'nothing' };
const unreadable = { kind: 'unreadable' };
const card = (name) => ({ kind: 'card', name });

describe('an empty frame', () => {
  test('never buzzes', () => {
    // The majority of frames during a real scan: the gap between one
    // card and the next, a hand moving, the edge of the box.
    const g = new BuzzGuard();
    assert.equal(g.consider(nothing, T), false);
    assert.equal(g.consider(nothing, T + 500), false);
    assert.equal(g.consider(nothing, T + 1000), false);
  });
});

describe('a card that was placed', () => {
  test('buzzes when it appears', () => {
    const g = new BuzzGuard();
    assert.equal(g.consider(card('Sol Ring'), T), true);
  });

  test('but not while it is still sitting there', () => {
    // The whole point. A card held under the camera is photographed
    // every tick and is not news after the first one.
    const g = new BuzzGuard();
    g.consider(card('Sol Ring'), T);
    assert.equal(g.consider(card('Sol Ring'), T + 500), false);
    assert.equal(g.consider(card('Sol Ring'), T + 1500), false);
  });

  test('a different card buzzes immediately', () => {
    const g = new BuzzGuard();
    g.consider(card('Sol Ring'), T);
    assert.equal(g.consider(card('Lightning Bolt'), T + 500), true);
  });

  test('and the same card again later does too', () => {
    // The second copy of a playset, deliberately re-scanned. Silence
    // there reads as the app having missed it.
    const g = new BuzzGuard();
    g.consider(card('Sol Ring'), T);
    assert.equal(
      g.consider(card('Sol Ring'), T + SAME_CARD_MS + 1), true);
  });
});

describe('a card it could not place', () => {
  test('buzzes once — something is there', () => {
    // Worth feeling: it means stop moving your hand, not "nothing yet".
    const g = new BuzzGuard();
    assert.equal(g.consider(unreadable, T), true);
  });

  test('and not again while it keeps failing', () => {
    // A card the phone cannot read sits there failing every tick until
    // the loop gives up. Buzzing each time is the rattle with a motor.
    const g = new BuzzGuard();
    g.consider(unreadable, T);
    assert.equal(g.consider(unreadable, T + 500), false);
    assert.equal(g.consider(unreadable, T + 5000), false);
  });

  test('but a fresh failure after an empty frame does buzz', () => {
    // Card taken away, another unreadable one put down. Staying silent
    // for the second one reads as the app having stopped.
    const g = new BuzzGuard();
    g.consider(unreadable, T);
    g.consider(nothing, T + 500);
    assert.equal(g.consider(unreadable, T + 1000), true);
  });

  test('and one that fails then succeeds buzzes for the success', () => {
    // Refocus lands and the card resolves: a different outcome for the
    // same piece of cardboard, and the one the user was waiting for.
    const g = new BuzzGuard();
    g.consider(unreadable, T);
    assert.equal(g.consider(card('Sol Ring'), T + 400), true);
  });

  test('and after that success, failing again buzzes once more', () => {
    const g = new BuzzGuard();
    g.consider(unreadable, T);
    g.consider(card('Sol Ring'), T + 400);
    assert.equal(g.consider(unreadable, T + 800), true);
    assert.equal(g.consider(unreadable, T + 1200), false);
  });
});

describe('over a whole box', () => {
  test('one buzz per card, not one per photograph', () => {
    /*
     * The acceptance test. Three cards, each held in frame for several
     * ticks with gaps between them — thirteen photographs, three events
     * worth feeling.
     */
    const g = new BuzzGuard();
    const frames = [
      nothing,
      card('Sol Ring'), card('Sol Ring'), card('Sol Ring'),
      nothing, nothing,
      card('Lightning Bolt'), card('Lightning Bolt'),
      nothing,
      unreadable, unreadable, unreadable,
      nothing,
    ];
    let buzzes = 0;
    frames.forEach((f, i) => {
      if (g.consider(f, T + i * 800)) buzzes += 1;
    });
    assert.equal(buzzes, 3, 'two cards placed, one that could not be');
  });

  test('turning the loop off and on starts clean', () => {
    const g = new BuzzGuard();
    g.consider(card('Sol Ring'), T);
    g.reset();
    assert.equal(g.consider(card('Sol Ring'), T + 100), true);
  });
});
