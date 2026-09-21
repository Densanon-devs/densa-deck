/**
 * Reading the credit line, against the real artist list.
 *
 * The fixture is the 168 artists who have painted an Island. That
 * matters: the failure modes here are all about real names -- accents
 * that OCR drops, single-word signatures, one artist who signs in
 * CJK -- and a made-up list would have none of them.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';

import {
  artistIn,
  flatten,
  narrowByCredits,
  yearsIn,
} from '../src/lib/footer-credits.ts';

const ARTISTS = JSON.parse(
  readFileSync(new URL('./fixtures/island-artists.json', import.meta.url),
    'utf-8'));

describe('finding the artist', () => {
  test('the ordinary case', () => {
    assert.equal(
      artistIn('Island\nIllus. John Avon\n™ & © 2004 Wizards',
        ARTISTS),
      'John Avon');
  });

  test('without the label, which OCR often loses', () => {
    // Nothing here parses "Illus." -- the artist is looked for
    // directly, so the label and its punctuation need not survive.
    assert.equal(artistIn('Island 234 Rebecca Guay 1998', ARTISTS),
      'Rebecca Guay');
  });

  test('with the punctuation run together', () => {
    assert.equal(artistIn('Illus.John Avon', ARTISTS), 'John Avon');
  });

  test('an accent the scan did not pick up', () => {
    // Alexandre Honore, printed Honoré. Both spellings have to land
    // on the same artist or the accented half of the catalogue is
    // unreachable.
    assert.equal(artistIn('Illus. Alexandre Honore', ARTISTS),
      'Alexandre Honoré');
    assert.equal(artistIn('Illus. Alexandre Honoré', ARTISTS),
      'Alexandre Honoré');
  });

  test('a signature that is one word', () => {
    assert.equal(artistIn('Illus. Chippy', ARTISTS), 'Chippy');
    assert.equal(artistIn('Illus. kozyndan', ARTISTS), 'kozyndan');
  });

  test('a signature that is not in the Latin alphabet', () => {
    assert.equal(artistIn('库雪明', ARTISTS),
      '库雪明');
  });

  test('case does not matter', () => {
    assert.equal(artistIn('ILLUS. JOHN AVON', ARTISTS), 'John Avon');
  });

  test('no artist is no answer, not a wrong one', () => {
    assert.equal(artistIn('Island\n™ & © 2004 Wizards', ARTISTS),
      '');
    assert.equal(artistIn('', ARTISTS), '');
  });

  test('a partial word does not count as a name', () => {
    // "Avo" is not "John Avon", and a fragment matching would put a
    // confident wrong shortlist in front of somebody.
    assert.equal(artistIn('Illus. Avo', ARTISTS), '');
  });

  test('the empty artist in the index matches nothing', () => {
    // Some rows have no artist recorded. An empty string is a
    // substring of everything and would match every card.
    assert.equal(artistIn('Island', ['', '  ']), '');
  });

  test('every real artist finds itself', () => {
    // The whole fixture, because one unmatched spelling is a whole
    // artist's printings unreachable.
    for (const artist of ARTISTS) {
      assert.equal(artistIn(`Illus. ${artist}`, ARTISTS), artist,
        `${artist} did not match itself`);
    }
  });

  test('and does not find a different one', () => {
    for (const artist of ARTISTS) {
      const hit = artistIn(`Island\nIllus. ${artist}\n2004 Wizards`, ARTISTS);
      assert.equal(hit, artist);
    }
  });
});

describe('finding the year', () => {
  test('the copyright line', () => {
    assert.deepEqual(yearsIn('™ & © 2004 Wizards of the Coast'),
      [2004]);
  });

  test('both dates on a reprint, newest first', () => {
    assert.deepEqual(yearsIn('© 1995, 2018 Wizards'), [2018, 1995]);
  });

  test('nothing before Magic existed', () => {
    assert.deepEqual(yearsIn('1976 1992'), []);
  });

  test('nothing from the far future', () => {
    assert.deepEqual(yearsIn('2099 2004', 2027), [2004]);
  });

  test('a collector number is not a year', () => {
    assert.deepEqual(yearsIn('234/280'), []);
  });

  test('no date at all', () => {
    assert.deepEqual(yearsIn('Island'), []);
  });
});

describe('narrowing by both', () => {
  const rows = [
    { printing_id: 'a', artist: 'John Avon', released_year: 2004 },
    { printing_id: 'b', artist: 'John Avon', released_year: 2004 },
    { printing_id: 'c', artist: 'John Avon', released_year: 1998 },
  ];

  test('the year cuts the artist down', () => {
    assert.deepEqual(narrowByCredits(rows, [1998]).map((r) => r.printing_id),
      ['c']);
  });

  test('no year read means no cut', () => {
    assert.equal(narrowByCredits(rows, []).length, 3);
  });

  test('a year that matches nothing is ignored, not obeyed', () => {
    // A misread year must not turn a good shortlist into none. This
    // is the case that makes the year safe to use at all.
    assert.equal(narrowByCredits(rows, [2019]).length, 3);
  });

  test('a row with no year recorded is not claimed by one', () => {
    const mixed = [...rows, { printing_id: 'd', artist: 'John Avon' }];
    assert.deepEqual(narrowByCredits(mixed, [2004]).map((r) => r.printing_id),
      ['a', 'b']);
  });
});

describe('flattening', () => {
  test('keeps digits, drops punctuation', () => {
    assert.equal(flatten('Illus. John Avon, 2004!'), 'illus john avon 2004');
  });

  test('is stable on something already flat', () => {
    assert.equal(flatten('john avon'), 'john avon');
  });
});
