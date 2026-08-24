/**
 * The scanner's judgement, which is the only part of scanning the phone owns.
 *
 * Every case here is a failure that actually happened on the web version and
 * cost either a wrong card in the inventory or a scanning run that silently
 * did nothing.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  MOTION_CEILING,
  RepeatGuard,
  defaultFinish,
  motionBetween,
  sameImage,
  shouldSend,
} from '../src/lib/scanner.ts';

const frame = (fill) => Float32Array.from({ length: 64 }, () => fill);

describe('not filing the same card twice', () => {
  test('a card held in frame is filed once', () => {
    const guard = new RepeatGuard();
    assert.equal(guard.consider('Sol Ring', 1000).file, true);
    assert.equal(guard.consider('Sol Ring', 1500).file, false);
    assert.equal(guard.consider('Sol Ring', 2000).file, false);
  });

  test('a frame that read nothing does not clear the guard', () => {
    // This is the exact bug: one blurred frame between two good ones cleared
    // the guard and filed the card again. Six copies of one card went in.
    const guard = new RepeatGuard();
    guard.consider('Sol Ring', 1000);
    guard.consider('', 1200); // a frame that read nothing
    assert.equal(guard.consider('Sol Ring', 1400).file, false);
  });

  test('a genuine second copy can still be scanned', () => {
    const guard = new RepeatGuard();
    guard.consider('Sol Ring', 1000);
    const again = guard.consider('Sol Ring', 1000 + 5000);
    assert.equal(again.file, true);
    assert.equal(again.copy, 2, 'and it is announced as the second copy');
  });

  test('holding one card does not expire its own guard', () => {
    // The window has to be from the last SIGHTING, not the first, or a card
    // left in frame gets refiled the moment the timer runs out.
    const guard = new RepeatGuard(1000);
    guard.consider('Sol Ring', 0);
    for (let t = 500; t <= 5000; t += 500) guard.consider('Sol Ring', t);
    assert.equal(guard.consider('Sol Ring', 5300).file, false);
  });

  test('a different card is filed straight away', () => {
    const guard = new RepeatGuard();
    guard.consider('Sol Ring', 1000);
    assert.equal(guard.consider('Lightning Bolt', 1100).file, true);
  });
});

describe('deciding whether a frame is worth sending', () => {
  test('a moving phone is not photographing a card', () => {
    // Carrying the phone between cards uploaded a 2 MB picture of the floor
    // every 1.3 seconds and read nothing from any of them.
    const decision = shouldSend({
      busy: false, motion: MOTION_CEILING + 5,
      previousStill: null, currentStill: frame(10),
    });
    assert.equal(decision.send, false);
    assert.equal(decision.reason, 'moving');
  });

  test('a still phone is', () => {
    assert.equal(
      shouldSend({ busy: false, motion: 1, previousStill: null,
                   currentStill: frame(10) }).send,
      true,
    );
  });

  test('an identical capture means the camera is frozen', () => {
    // Measured once as twenty byte-identical stills over 41 seconds, so every
    // card after the first was read from a frozen image of the first — which
    // reports the WRONG card rather than no card.
    const decision = shouldSend({
      busy: false, motion: 0,
      previousStill: frame(10), currentStill: frame(10),
    });
    assert.equal(decision.send, false);
    assert.equal(decision.reason, 'frozen-camera');
  });

  test('two real captures of the same scene still differ', () => {
    const a = frame(10);
    const b = Float32Array.from(a, (v, i) => v + (i % 3));  // sensor noise
    assert.equal(sameImage(a, b), false);
  });

  test('a request already in flight is not piled onto', () => {
    assert.equal(
      shouldSend({ busy: true, motion: 0, previousStill: null,
                   currentStill: frame(1) }).reason,
      'busy',
    );
  });
});

describe('measuring movement', () => {
  test('an unchanged scene reads as no movement', () => {
    assert.equal(motionBetween(frame(10), frame(10)), 0);
  });

  test('a changed scene reads as movement', () => {
    assert.equal(motionBetween(frame(10), frame(40)), 30);
  });

  test('the first frame has nothing to compare against', () => {
    assert.equal(motionBetween(null, frame(10)), 0);
  });
});

describe('choosing a finish', () => {
  const nonfoilOnly = {
    printing_id: 'p1', name: 'Plainscard', set_code: 'xyz', set_name: 'XYZ',
    collector_number: '5', finishes: ['nonfoil'],
  };
  const both = { ...nonfoilOnly, finishes: ['nonfoil', 'foil'] };

  test('a foil hint preselects foil when the printing has one', () => {
    assert.equal(
      defaultFinish(both, { suggested_finish: 'foil' }),
      'foil',
    );
  });

  test('a misread star cannot file a finish that was never printed', () => {
    assert.equal(
      defaultFinish(nonfoilOnly, { suggested_finish: 'foil' }),
      'nonfoil',
    );
  });

  test('no hint means nonfoil', () => {
    assert.equal(defaultFinish(both, { suggested_finish: 'nonfoil' }), 'nonfoil');
  });
});
