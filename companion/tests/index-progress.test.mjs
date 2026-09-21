/**
 * Saying how a card-index download is going.
 *
 * Reported as "it never actually seems to finish or update me on how
 * long or how far", and "going out and back resets". Three causes,
 * only one of which was the download:
 *
 *   * `File.downloadFileAsync` is one await over 78 MB and reports
 *     nothing until it is done, so the slow half showed no movement;
 *   * the fetch and the read-back shared one bar, so when it did move
 *     it filled twice and looked like it had started over;
 *   * Settings never rendered the progress it was already handed.
 *
 * These are the words. The wiring is in app-state and the screen.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  fractionDone,
  inMegabytes,
  progressLine,
} from '../src/lib/index-progress.ts';

describe('how far along', () => {
  test('a normal fraction', () => {
    assert.equal(fractionDone({ stage: 'printings', done: 25, total: 100 }),
      0.25);
  });

  test('nothing yet is zero, not unknown', () => {
    assert.equal(fractionDone({ stage: 'printings', done: 0, total: 100 }), 0);
  });

  test('no total is unknown, not zero', () => {
    // A bar pinned at the left reads as a stall. Null lets the screen
    // show an empty rail, which reads as waiting -- which it is.
    assert.equal(fractionDone({ stage: 'printings', done: 10, total: 0 }),
      null);
    assert.equal(fractionDone(null), null);
  });

  test('past the end is clamped', () => {
    // A server that under-reports its own length would otherwise
    // produce 114%, which makes the whole number look made up.
    assert.equal(fractionDone({ stage: 'cards', done: 120, total: 100 }), 1);
  });
});

describe('bytes as a person would say them', () => {
  test('megabytes, to one place while that means anything', () => {
    // The real default_cards size. One decimal place, because
    // "78.8" moving to "79.2" is visible progress and "79" is not.
    assert.equal(inMegabytes(78_768_616), '78.8 MB');
    assert.equal(inMegabytes(150_000_000), '150 MB');
    assert.equal(inMegabytes(24_710_646), '24.7 MB');
    assert.equal(inMegabytes(1_500_000), '1.5 MB');
  });

  test('kilobytes below a megabyte', () => {
    assert.equal(inMegabytes(512_000), '512 kB');
  });

  test('nothing is not a negative number', () => {
    assert.equal(inMegabytes(0), '0 kB');
    assert.equal(inMegabytes(-5), '0 kB');
  });
});

describe('the line it shows', () => {
  test('downloading names the size, because that is the wait', () => {
    assert.equal(
      progressLine({ stage: 'printings', phase: 'downloading',
                     done: 34_000_000, total: 78_768_616 }),
      'Downloading card printings: 34.0 MB of 78.8 MB');
  });

  test('reading names a percentage, because that part is quick', () => {
    assert.equal(
      progressLine({ stage: 'printings', phase: 'reading',
                     done: 40, total: 100 }),
      'Reading in card printings: 40%');
  });

  test('the two phases read differently', () => {
    // The whole point: watching the bar fill twice should not look
    // like the first one failing.
    const down = progressLine({ stage: 'cards', phase: 'downloading',
                                done: 1, total: 2 });
    const read = progressLine({ stage: 'cards', phase: 'reading',
                                done: 1, total: 2 });
    assert.notEqual(down, read);
    assert.match(down, /^Downloading/);
    assert.match(read, /^Reading in/);
  });

  test('the two files are named, not numbered', () => {
    assert.match(progressLine({ stage: 'printings', phase: 'reading',
                                done: 1, total: 2 }), /card printings/);
    assert.match(progressLine({ stage: 'cards', phase: 'reading',
                                done: 1, total: 2 }), /card rules/);
  });

  test('before anything is known it says so', () => {
    assert.equal(progressLine({ stage: 'starting', done: 0, total: 0 }),
      'Starting…');
  });

  test('a download with no declared length still says it is downloading',
    () => {
      assert.equal(
        progressLine({ stage: 'cards', phase: 'downloading',
                       done: 500, total: 0 }),
        'Downloading card rules…');
    });

  test('no progress at all is no line at all', () => {
    assert.equal(progressLine(null), '');
  });
});
