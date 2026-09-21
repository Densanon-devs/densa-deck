/**
 * Saying which kind of nothing happened.
 *
 * A prerelease Etrata would not scan. The app said "saw a card but could
 * not place it" — true, and useless. It cannot distinguish a footer the
 * recogniser never read from one it read and could not match, and those
 * want opposite things from the person holding the card: better light in
 * the first case, a different printing in the second.
 *
 * Worse, it hid the interesting possibility entirely. The name and rules
 * text of that card are black on white and read perfectly; the collector
 * line is grey on a black border, on a foil, at an angle. "Saw a card"
 * was satisfied by the rules text while the only part that identifies
 * anything was never read at all.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { describeLocalMiss } from '../src/lib/scan-miss.ts';

describe('why a local scan came to nothing', () => {
  test('no text at all is a focus or light problem', () => {
    const out = describeLocalMiss('');
    assert.match(out, /nothing legible/i);
    assert.match(out, /focus|light/i);
  });

  test('whitespace only counts as nothing', () => {
    assert.match(describeLocalMiss('   \n  \n '), /nothing legible/i);
  });

  test('text without a footer key points at the footer', () => {
    // The Etrata case: everything above the collector line read fine.
    const out = describeLocalMiss(
      ['Etrata, Deadly Fugitive', 'Legendary Creature - Vampire Assassin',
       'Deathtouch'].join('\n'));
    assert.match(out, /bottom edge|set code/i);
    assert.match(out, /Etrata/, 'it should quote what it did read');
    assert.doesNotMatch(out, /not in this phone/i,
      'nothing was looked up, so nothing can be missing from the index');
  });

  test('and it quotes the text rather than describing it', () => {
    // So a screenshot of the failure is a diagnosis, not a prompt for
    // twenty more questions.
    assert.match(describeLocalMiss('Sol Ring'), /"Sol Ring"/);
  });

  test('a long read is truncated rather than filling the screen', () => {
    const out = describeLocalMiss('x'.repeat(400));
    assert.ok(out.length < 250, `status was ${out.length} characters`);
  });

  test('a key that was read but matched nothing says so', () => {
    // A different failure entirely: photography is fine, the printing
    // is simply not in the index. More light will not help.
    const out = describeLocalMiss(
      ['Etrata, Deadly Fugitive', 'M 0200', 'MKM★EN'].join('\n'));
    assert.match(out, /MKM #200/);
    assert.match(out, /not in this phone/i);
  });

  test('and points at the prerelease toggle, which is the usual cause',
    () => {
      // A stamped promo is filed under pmkm/200s, so the key read off
      // the card genuinely is absent. That is one tap away from working
      // and nothing else on screen says so.
      const out = describeLocalMiss(
        ['Etrata, Deadly Fugitive', 'M 0200', 'MKM★EN'].join('\n'));
      assert.match(out, /prerelease/i);
    });
});
