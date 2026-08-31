/**
 * What the phone can do alone, and what needs the PC.
 *
 * The rule the app is built around: the phone is the COLLECTION and works
 * standalone; the PC is the ANALYSIS and is offered only when it is
 * actually there. "There" means two things — a PC has been paired at all,
 * and it is answering right now — because they fail differently and the
 * user can act on the difference.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { barrier, canAnalyse, explainBarrier } from '../src/lib/capability.ts';

describe('when the PC is available', () => {
  test('paired and answering', () => {
    assert.equal(canAnalyse({ connection: 'connected', paired: true }), true);
  });

  test('paired but out of range is not available', () => {
    assert.equal(canAnalyse({ connection: 'offline', paired: true }), false);
  });

  test('never paired is not available however good the network is', () => {
    // Both halves are required. A phone with perfect signal and no desktop
    // has nothing to ask.
    assert.equal(canAnalyse({ connection: 'connected', paired: false }), false);
  });

  test('revoked by the desktop is not available', () => {
    assert.equal(canAnalyse({ connection: 'unpaired', paired: true }), false);
  });

  test('before the first sync, a paired phone is treated as available', () => {
    // Refusing here blanks the analysis on every cold open and then fills
    // it in a second later, which reads as the app changing its mind.
    assert.equal(canAnalyse({ connection: 'unknown', paired: true }), true);
  });

  test('but an unpaired phone is not, even before the first sync', () => {
    assert.equal(canAnalyse({ connection: 'unknown', paired: false }), false);
  });
});

describe('saying WHY it is not', () => {
  test('nothing in the way when it is available', () => {
    assert.equal(barrier({ connection: 'connected', paired: true }), null);
  });

  test('unpaired is reported as unpaired, not as offline', () => {
    // The difference between "you do not have this" and "you cannot have
    // this right now" is the difference between an upsell and a status
    // line, and only one of them is worth a user's attention.
    assert.equal(barrier({ connection: 'offline', paired: false }), 'unpaired');
  });

  test('paired and out of range is offline', () => {
    assert.equal(barrier({ connection: 'offline', paired: true }), 'offline');
  });

  test('each reads as its own situation, and neither as a fault', () => {
    const unpaired = explainBarrier('unpaired', 'Deck analysis');
    const offline = explainBarrier('offline', 'Deck analysis');
    assert.notEqual(unpaired, offline);
    // The standalone case says the collection still works — that is the
    // whole promise of the phone half.
    assert.match(unpaired, /collection works without it/i);
    assert.match(offline, /not in reach/i);
    for (const line of [unpaired, offline]) {
      assert.doesNotMatch(line, /error|failed|cannot connect/i);
    }
  });
});

describe('a phone that has never had a PC', () => {
  /**
   * Standalone is a supported way to own this app, not a degraded mode —
   * so the app has to OPEN without a desktop. It did not: no pairing sent
   * you to the pairing screen and left you there, which made the phone's
   * main job conditional on the accessory.
   */
  test('nothing is analysed, and that is not an error', () => {
    const reach = { connection: 'offline', paired: false };
    assert.equal(canAnalyse(reach), false);
    assert.equal(barrier(reach), 'unpaired');
    assert.doesNotMatch(explainBarrier('unpaired'), /error|failed/i);
  });

  test('and it is told its collection still works', () => {
    // The one thing a standalone user needs to know: they have not lost
    // anything by not owning a desktop.
    assert.match(explainBarrier('unpaired'), /collection works without it/i);
  });

  test('pairing later turns analysis on without anything else changing', () => {
    assert.equal(canAnalyse({ connection: 'connected', paired: false }), false);
    assert.equal(canAnalyse({ connection: 'connected', paired: true }), true);
  });
});
