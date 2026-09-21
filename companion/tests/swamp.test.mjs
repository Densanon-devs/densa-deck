/**
 * The Revised Swamp, which took three builds.
 *
 * Every guess about it was wrong, and the thing that settled it was
 * making the app quote what it had read. It said:
 *
 *   Read 'Swamp', which has 744 printings — too many to choose from.
 *   The artist line along the bottom edge did not read:
 *   "Illus, Dan Firazier your mnana pool. D: Add # to Land Swamp"
 *
 * Which says the opposite of what the message claimed. The artist
 * line DID read. `Dan Firazier` is `Dan Frazier` with one inserted
 * letter, the one-edit tolerance matched it, and the index cut 744
 * Swamps down to his 42.
 *
 * Then forty was the cap, and the answer was thrown away two rows
 * over a line drawn for a different kind of list.
 *
 * The fixture is every English paper Swamp, with its real artist. The
 * OCR string below is verbatim from the phone.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';

import { artistIn, withinOneEdit } from '../src/lib/footer-credits.ts';
import { CREDIT_SHORTLIST, identifyLocally } from '../src/lib/identify.ts';
import { LocalStore } from '../src/lib/store.ts';
import { RealDatabase } from './real-sqlite.mjs';

const SWAMPS = JSON.parse(
  readFileSync(new URL('./fixtures/swamp-printings.json', import.meta.url),
    'utf-8'));

/** Word for word what the phone reported reading off that card. */
const OCR = 'Swamp\nLand\nIllus, Dan Firazier your mnana pool. '
  + 'D: Add # to Land Swamp';

const ARTISTS = [...new Set(SWAMPS.map((r) => r[4]).filter(Boolean))];

async function indexed() {
  const store = new LocalStore(new RealDatabase());
  await store.init();
  await store.putCatalogue(SWAMPS.map(
    ([id, name, set, num, artist, year]) =>
      [id, name, set, num, 0, 'common', 0.2, null, artist, year]));
  return store;
}

describe('what the OCR actually gave us', () => {
  test('Firazier is Frazier with one letter inserted', () => {
    assert.equal(withinOneEdit('dan firazier', 'dan frazier'), true);
  });

  test('so the artist was found, despite the message saying otherwise', () => {
    assert.equal(artistIn(OCR, ARTISTS), 'Dan Frazier');
  });

  test('the rules text around it does not match somebody else', () => {
    // "your mnana pool", "Add # to Land Swamp" -- a page of mangled
    // words next to a fuzzy matcher is where a wrong answer would
    // come from, so this is the case that says it does not.
    const noArtist = OCR.replace('Dan Firazier', '');
    assert.equal(artistIn(noArtist, ARTISTS), '');
  });
});

describe('and what was done with it', () => {
  test('it is offered, not refused', async () => {
    const store = await indexed();
    const out = await identifyLocally(OCR, store);
    assert.ok(out.candidates.length > 0,
      `still refused: ${out.reason}`);
  });

  test('every one of them is a Dan Frazier Swamp', async () => {
    const store = await indexed();
    const out = await identifyLocally(OCR, store);
    for (const row of out.candidates) {
      assert.equal(row.artist, 'Dan Frazier');
      assert.equal(row.name, 'Swamp');
    }
  });

  test('which is far fewer than all of them', async () => {
    // The whole claim: a credit line turns "too many to choose from"
    // into a list. It does not have to be short to be an answer.
    const store = await indexed();
    const out = await identifyLocally(OCR, store);
    assert.ok(out.candidates.length < SWAMPS.length / 10,
      `${out.candidates.length} of ${SWAMPS.length} is not narrowing`);
  });

  test('forty-two was over the old cap, which is the bug', async () => {
    // Stated as a number so the regression is explicit: this list is
    // longer than NAME_SHORTLIST and must still be shown.
    const store = await indexed();
    const out = await identifyLocally(OCR, store);
    assert.ok(out.candidates.length > 40,
      'the fixture no longer reproduces the reported case');
    assert.ok(out.candidates.length <= CREDIT_SHORTLIST);
  });

  test('still never filed on its own', async () => {
    const store = await indexed();
    const out = await identifyLocally(OCR, store);
    assert.equal(out.autoAddable, false);
  });

  test('a copyright year would have cut it to a handful', async () => {
    // Which is what happens on a card whose bottom line reads fully.
    // Dan Frazier's Swamps span many years; one of them is small.
    const store = await indexed();
    const withYear = await identifyLocally(`${OCR}\n© 1994 Wizards`,
      store);
    assert.ok(withYear.candidates.length > 0);
    assert.ok(withYear.candidates.length < 10,
      `${withYear.candidates.length} with a year is not a handful`);
  });
});

describe('the cap has an end', () => {
  test('an artist with more than the cap is still refused', async () => {
    // A scroll is not a shortlist. At that point the honest answer
    // is still to type the set.
    const many = Array.from({ length: CREDIT_SHORTLIST + 5 }, (_, i) =>
      [`id-${i}`, 'Swamp', 'xxx', String(i + 1), 0, 'common', 0.2, null,
       'Prolific Painter', 2000 + (i % 3)]);
    const store = new LocalStore(new RealDatabase());
    await store.init();
    await store.putCatalogue([...SWAMPS.map(
      ([id, name, set, num, artist, year]) =>
        [id, name, set, num, 0, 'common', 0.2, null, artist, year]), ...many]);

    const out = await identifyLocally(
      'Swamp\nLand\nIllus. Prolific Painter', store);
    assert.equal(out.candidates.length, 0);
    assert.match(out.reason, /too many to choose from/);
  });
});
