/**
 * What the connection strip says.
 *
 * There was no connection status anywhere in the app: a banner when something
 * was wrong, and nothing at all when things were fine. That sounds reasonable
 * until you are standing in a shop wondering whether the card you just scanned
 * reached the PC, and the answer is a blank space.
 *
 * "Nothing is wrong" and "I have not checked yet" are both silence, and they
 * are completely different situations. Most of what is checked here is that
 * they never read the same.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { agoInWords, describeConnection } from '../src/lib/status.ts';

const NOW = Date.parse('2026-08-26T12:00:00Z');
const ago = (seconds) => new Date(NOW - seconds * 1000).toISOString();

describe('the four states never read the same', () => {
  const states = [
    { connection: 'connected', pendingEdits: 0, via: 'lan' },
    { connection: 'offline', pendingEdits: 0 },
    { connection: 'unpaired', pendingEdits: 0 },
    { connection: 'unknown', pendingEdits: 0 },
  ];

  test('each has its own words and its own colour', () => {
    const headlines = states.map((s) => describeConnection(s, NOW).headline);
    const tones = states.map((s) => describeConnection(s, NOW).tone);
    assert.equal(new Set(headlines).size, states.length, headlines.join(' | '));
    assert.equal(new Set(tones).size, states.length, tones.join(' | '));
  });

  test('none of them is empty', () => {
    // An empty string is how "everything is fine" became indistinguishable
    // from "the strip is not there".
    for (const state of states) {
      const status = describeConnection(state, NOW);
      assert.ok(status.headline.length > 0, JSON.stringify(state));
      assert.ok(status.text.length > 0, JSON.stringify(state));
    }
  });
});

describe('connected', () => {
  test('it names the path it took', () => {
    // Connected over Tailscale from the sofa and connected over Wi-Fi in the
    // same room are different enough to be worth saying.
    assert.match(
      describeConnection({ connection: 'connected', pendingEdits: 0, via: 'lan' }, NOW)
        .headline,
      /Wi-Fi/,
    );
    assert.match(
      describeConnection(
        { connection: 'connected', pendingEdits: 0, via: 'tunnel' },
        NOW,
      ).headline,
      /Tailscale/,
    );
  });

  test('an unknown path still reads as connected', () => {
    const status = describeConnection(
      { connection: 'connected', pendingEdits: 0 },
      NOW,
    );
    assert.equal(status.tone, 'good');
    assert.match(status.headline, /Connected/);
  });

  test('it says when it last synced', () => {
    const status = describeConnection(
      {
        connection: 'connected',
        pendingEdits: 0,
        via: 'lan',
        lastSyncAt: ago(120),
      },
      NOW,
    );
    assert.match(status.text, /2 min ago/);
  });

  test('waiting edits are not reported as everything being fine', () => {
    // Connected with work outstanding is not the same as connected and done.
    // A green "Connected" over three unsent changes is a small lie.
    const status = describeConnection(
      { connection: 'connected', pendingEdits: 3, via: 'lan' },
      NOW,
    );
    assert.equal(status.tone, 'warn');
    assert.match(status.headline, /3 changes waiting/);
  });
});

describe('offline', () => {
  test('it says the collection is still there', () => {
    // The first fear is that the cards are gone.
    const status = describeConnection({ connection: 'offline', pendingEdits: 0 }, NOW);
    assert.equal(status.tone, 'warn');
    assert.match(status.text, /still here/);
  });

  test('it says what is waiting, and that it is not lost', () => {
    const status = describeConnection({ connection: 'offline', pendingEdits: 1 }, NOW);
    assert.match(status.headline, /1 change waiting/);
    assert.match(status.text, /come(s)? back/);
  });

  test('one change is singular', () => {
    assert.match(
      describeConnection({ connection: 'offline', pendingEdits: 1 }, NOW).headline,
      /1 change waiting/,
    );
    assert.match(
      describeConnection({ connection: 'offline', pendingEdits: 2 }, NOW).headline,
      /2 changes waiting/,
    );
  });
});

describe('unpaired', () => {
  test('it says what to do rather than what happened', () => {
    // The only state with one obvious action, so it names the action.
    const status = describeConnection({ connection: 'unpaired', pendingEdits: 0 }, NOW);
    assert.equal(status.tone, 'bad');
    assert.match(status.text, /QR code/);
  });
});

describe('how long ago, in words', () => {
  test('recent is "just now" rather than a number', () => {
    assert.equal(agoInWords(ago(5), NOW), 'just now');
    assert.equal(agoInWords(ago(44), NOW), 'just now');
  });

  test('minutes, then hours, then days', () => {
    assert.equal(agoInWords(ago(60 * 5), NOW), '5 min ago');
    assert.equal(agoInWords(ago(60 * 90), NOW), '2 hr ago');
    assert.equal(agoInWords(ago(3600 * 26), NOW), 'yesterday');
    assert.equal(agoInWords(ago(3600 * 24 * 3), NOW), '3 days ago');
  });

  test('a clock that has gone backwards does not print a negative', () => {
    // Phones adjust their clocks, and "-4 min ago" reads as a bug in the app
    // rather than a bug in the clock.
    assert.equal(agoInWords(new Date(NOW + 60_000).toISOString(), NOW), 'just now');
  });

  test('nonsense is nothing rather than NaN', () => {
    assert.equal(agoInWords(undefined, NOW), '');
    assert.equal(agoInWords('not a date', NOW), '');
  });
});
