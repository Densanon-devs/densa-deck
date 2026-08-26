/**
 * Scanning on a timer.
 *
 * The reason this logic is not in the screen is that none of its failures can
 * be reproduced on a device by hand. Every one is about timing: a request that
 * outlasts its own interval, a PC that stopped answering, a camera handing
 * back the same still forever. They are trivial to provoke here and nearly
 * impossible to provoke while holding a phone.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  AutoScanner,
  FAILURE_LIMIT,
  SCAN_INTERVAL_MS,
  explain,
} from '../src/lib/autoscan.ts';

const running = (over = {}) => ({
  running: true,
  busy: false,
  connection: 'connected',
  now: 0,
  ...over,
});

describe('taking the next picture', () => {
  test('it fires immediately when switched on', () => {
    // Waiting a full interval before the first shot makes the button feel
    // broken.
    const scanner = new AutoScanner();
    scanner.reset(10_000);
    assert.equal(scanner.next(running({ now: 10_000 })).act, 'capture');
  });

  test('it waits out the interval between pictures', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    assert.equal(scanner.next(running({ now: 0 })).act, 'capture');
    assert.equal(scanner.next(running({ now: 100 })).act, 'wait');
    assert.equal(
      scanner.next(running({ now: SCAN_INTERVAL_MS })).act,
      'capture',
    );
  });

  test('a request in flight is never doubled up on', () => {
    // A round trip can outlast the interval. Firing anyway queues photographs
    // faster than the PC can read them.
    const scanner = new AutoScanner();
    scanner.reset(0);
    scanner.next(running({ now: 0 }));
    assert.equal(
      scanner.next(running({ now: 10_000, busy: true })).act,
      'wait',
    );
  });

  test('switched off, it stops', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    const decision = scanner.next(running({ running: false }));
    assert.deepEqual(decision, { act: 'stop', reason: 'stopped' });
  });
});

describe('giving up', () => {
  test('no route to the PC stops it rather than draining the battery', () => {
    // The picture is read on the PC. With nowhere to send it, a loop that
    // keeps firing takes a photograph every second and a half forever and
    // reports nothing at all.
    const scanner = new AutoScanner();
    scanner.reset(0);
    const decision = scanner.next(running({ connection: 'offline' }));
    assert.deepEqual(decision, { act: 'stop', reason: 'offline' });
  });

  test('an unpaired phone stops too', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    assert.equal(
      scanner.next(running({ connection: 'unpaired' })).act,
      'stop',
    );
  });

  test('it still starts before the first sync has answered', () => {
    // 'unknown' is the state on a cold open. Refusing there would mean auto
    // scan never works at the moment a box of cards is about to be filed.
    const scanner = new AutoScanner();
    scanner.reset(0);
    assert.equal(
      scanner.next(running({ connection: 'unknown', now: 0 })).act,
      'capture',
    );
  });

  test('repeated failures stop it', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    for (let i = 0; i < FAILURE_LIMIT; i += 1) scanner.failed();
    assert.deepEqual(scanner.next(running({ now: 99_999 })), {
      act: 'stop',
      reason: 'too-many-failures',
    });
  });

  test('one answer clears the run of failures', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    scanner.failed();
    scanner.failed();
    scanner.succeeded();
    assert.equal(scanner.next(running({ now: 99_999 })).act, 'capture');
  });

  test('a failure while busy still counts against it', () => {
    // Busy is checked last on purpose: a request stuck in flight must not be
    // able to hold the loop open past the point it should have given up.
    const scanner = new AutoScanner();
    scanner.reset(0);
    for (let i = 0; i < FAILURE_LIMIT; i += 1) scanner.failed();
    assert.equal(
      scanner.next(running({ now: 99_999, busy: true })).act,
      'stop',
    );
  });
});

describe('a camera that has stopped taking pictures', () => {
  test('identical captures in a row stop it', () => {
    // Measured on the web version: twenty byte-identical captures over
    // forty-one seconds, so every card after the first was read from a stale
    // image of the first.
    const scanner = new AutoScanner();
    scanner.reset(0);
    scanner.captured('same');
    scanner.captured('same');
    scanner.captured('same');
    scanner.captured('same');
    assert.deepEqual(scanner.next(running({ now: 99_999 })), {
      act: 'stop',
      reason: 'frozen-camera',
    });
  });

  test('a different picture clears the suspicion', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    scanner.captured('same');
    scanner.captured('same');
    scanner.captured('same');
    scanner.captured('different');
    assert.equal(scanner.next(running({ now: 99_999 })).act, 'capture');
  });

  test('two identical frames are not enough to stop', () => {
    // A card that genuinely has not moved can produce a repeat. Stopping on
    // the first one would make auto scan quit constantly.
    const scanner = new AutoScanner();
    scanner.reset(0);
    scanner.captured('same');
    scanner.captured('same');
    assert.equal(scanner.next(running({ now: 99_999 })).act, 'capture');
  });

  test('empty captures are not counted as identical', () => {
    const scanner = new AutoScanner();
    scanner.reset(0);
    scanner.captured('');
    scanner.captured('');
    scanner.captured('');
    scanner.captured('');
    assert.equal(scanner.next(running({ now: 99_999 })).act, 'capture');
  });
});

describe('starting again', () => {
  test('reset clears whatever made it stop', () => {
    const scanner = new AutoScanner();
    for (let i = 0; i < FAILURE_LIMIT; i += 1) scanner.failed();
    scanner.captured('same');
    scanner.captured('same');
    scanner.captured('same');
    scanner.captured('same');

    scanner.reset(5_000);

    assert.equal(scanner.next(running({ now: 5_000 })).act, 'capture');
  });
});

describe('what the user is told', () => {
  test('every stopping reason says something, except stopping on purpose', () => {
    // A loop that quits without saying why is the same silence that made the
    // last three bugs look like nothing happening.
    for (const reason of ['offline', 'frozen-camera', 'too-many-failures']) {
      assert.ok(explain(reason).length > 20, reason);
    }
    assert.equal(explain('stopped'), '');
  });
});
