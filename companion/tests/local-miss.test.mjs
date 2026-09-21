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

import { asScanResult, describeLocalMiss } from '../src/lib/scan-miss.ts';
import { defaultFinish } from '../src/lib/scanner.ts';

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
    assert.match(out, /Deathtouch/,
      'the END of the read, where the footer would be — quoting the '
      + 'title back shows the part that plainly worked');
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

  test('and truncation keeps the END, which is where the footer is', () => {
    // The footer is the last thing on a card. Quoting the first sixty
    // characters showed the title and type line -- the part that
    // plainly worked -- and hid the only region in question.
    const out = describeLocalMiss(`STARTMARK ${'pad '.repeat(40)}TAILMARK`);
    assert.match(out, /TAILMARK/, 'the end is what matters');
    assert.doesNotMatch(out, /STARTMARK/, 'the beginning is not');
    assert.match(out, /Read "\.\.\./, 'and it says it was cut');
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

describe('offering the printings the phone found', () => {
  /**
   * The message said "which of its 7 printings this is needs a tap" and
   * there was nothing to tap. `identifyOffline` returns null whenever it
   * cannot auto-file, so the candidates it had just found were thrown
   * away before the screen ever saw them.
   *
   * The desktop path has always shown a picker for exactly this case.
   * The phone found the same kind of answer and had no way to say it.
   */
  const LAZAV = { printing_id: 'rna-212', name: 'Lazav, the Multifarious',
                  set_code: 'rna', collector_number: '212' };
  const RVR = { ...LAZAV, printing_id: 'rvr-1', set_code: 'rvr',
                collector_number: '1' };

  test('candidates become something the picker can render', () => {
    const out = asScanResult(
      { identity: { name: 'Lazav, the Multifarious', setCode: '',
                    collectorNumber: '', foilHint: false },
        candidates: [LAZAV, RVR], autoAddable: false, reason: 'pick one' });

    assert.equal(out.candidates.length, 2);
    assert.equal(out.candidates[0].printing_id, 'rna-212');
    assert.equal(out.auto_addable, false);
    // The picker reads these two off every candidate.
    assert.equal(typeof out.candidates[0].set_code, 'string');
    assert.equal(typeof out.candidates[0].collector_number, 'string');
  });

  test('a foil read carries through to the finish', () => {
    // The star in the footer is often the one thing that DID read, and
    // losing it would file a foil as an ordinary copy.
    const out = asScanResult(
      { identity: { name: 'x', setCode: '', collectorNumber: '',
                    foilHint: true },
        candidates: [LAZAV], autoAddable: false, reason: '' });
    assert.equal(out.suggested_finish, 'foil');
    assert.equal(out.foil_detected, true);
  });

  test('and a candidate with unknown finishes still files', () => {
    // The phone index does not store finishes, so `defaultFinish` must
    // have something sane to work from rather than crash or pick ''.
    const out = asScanResult(
      { identity: { name: 'x', setCode: '', collectorNumber: '',
                    foilHint: false },
        candidates: [LAZAV], autoAddable: false, reason: '' });
    assert.equal(defaultFinish(out.candidates[0], out), 'nonfoil');
  });

  test('a foil candidate with unknown finishes still comes back foil', () => {
    const out = asScanResult(
      { identity: { name: 'x', setCode: '', collectorNumber: '',
                    foilHint: true },
        candidates: [LAZAV], autoAddable: false, reason: '' });
    assert.equal(defaultFinish(out.candidates[0], out), 'foil');
  });

  test('nothing found is nothing offered', () => {
    // An empty picker is worse than no picker: it looks like the app
    // lost the list.
    const out = asScanResult(
      { identity: { name: '', setCode: '', collectorNumber: '',
                    foilHint: false },
        candidates: [], autoAddable: false, reason: 'no' });
    assert.equal(out.candidates.length, 0);
  });
});
