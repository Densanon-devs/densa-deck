/**
 * Scanning a basic land.
 *
 * Reported as "several lands didn't trigger". They are the one card
 * in the game with no fallback: every other unreadable footer drops
 * through to the name, and the catalogue is shaped so that usually
 * works -- 46% of names have exactly one printing, 77% have three or
 * fewer. `Island` has 671 in this phone's index and a list of that
 * length is not a shortlist, so the scanner refused, which left a
 * basic scannable only when its collector number read cleanly.
 *
 * The credit line is the way out, and it is printed in the same
 * footer strip the collector number is in.
 *
 * The fixture is every English paper Island Scryfall knows, with its
 * real artist and year. That matters more than usual here: the whole
 * claim is a distribution -- how often artist-and-year gets a person
 * down to a few cards -- and invented data would answer a question
 * nobody asked.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';

import { identifyLocally } from '../src/lib/identify.ts';
import { LocalStore } from '../src/lib/store.ts';
import { RealDatabase } from './real-sqlite.mjs';

/** [id, name, set, number, artist, year] */
const ISLANDS = JSON.parse(
  readFileSync(new URL('./fixtures/island-printings.json', import.meta.url),
    'utf-8'));

/**
 * Exactly one of the 671 has no artist recorded at all (Secret Lair
 * #255). It cannot be narrowed by a credit line and is kept out of
 * the coverage figures rather than quietly counted as a failure --
 * and there is a test below for what it does instead.
 */
const CREDITED = ISLANDS.filter(([, , , , artist]) => artist);

/** The phone's index, holding every Island there is. */
async function indexed() {
  const store = new LocalStore(new RealDatabase());
  await store.init();
  await store.putCatalogue(ISLANDS.map(
    ([id, name, set, num, artist, year]) =>
      [id, name, set, num, 0, 'common', 0.25, null, artist, year]));
  return store;
}

/** What the OCR would hand back off the bottom of a card. */
const footer = (artist, year) =>
  `Island\nBasic Land — Island\nIllus. ${artist}\n`
  + `™ & © ${year} Wizards of the Coast`;

describe('a basic land whose number will not read', () => {
  test('the credit line narrows it to a shortlist', async () => {
    const store = await indexed();
    const out = await identifyLocally(footer('John Avon', 2004), store);
    assert.ok(out.candidates.length > 0, 'it used to return none');
    assert.ok(out.candidates.length <= 5,
      `still ${out.candidates.length} to choose from`);
    for (const row of out.candidates) {
      assert.equal(row.artist, 'John Avon');
      assert.equal(row.released_year, 2004);
    }
  });

  test('an artist who painted one Island gives exactly one', async () => {
    const counts = new Map();
    for (const [, , , , artist] of CREDITED) {
      counts.set(artist, (counts.get(artist) ?? 0) + 1);
    }
    const [only] = [...counts].find(([a, n]) => a && n === 1);
    const store = await indexed();
    const out = await identifyLocally(footer(only, 2004), store);
    assert.equal(out.candidates.length, 1);
    assert.equal(out.candidates[0].artist, only);
  });

  test('it is never filed without a person looking', async () => {
    // A shortlist is not an identification. The card did not show
    // its number, which is exactly the situation this must not
    // resolve on its own.
    const store = await indexed();
    const out = await identifyLocally(footer('John Avon', 2004), store);
    assert.equal(out.autoAddable, false);
  });

  test('the name alone still refuses, and says why', async () => {
    const store = await indexed();
    const out = await identifyLocally('Island\nBasic Land — Island',
      store);
    assert.equal(out.candidates.length, 0);
    assert.match(out.reason, /671 printings/);
    assert.match(out.reason, /artist line/);
  });

  test('an unreadable artist falls back to refusing', async () => {
    const store = await indexed();
    const out = await identifyLocally(
      'Island\nIllus. Xxxx Yyyy\n© 2004 Wizards', store);
    assert.equal(out.candidates.length, 0);
  });

  test('a year that matches no printing keeps the artist list', async () => {
    // A misread year must cost detail, never the answer.
    const store = await indexed();
    const out = await identifyLocally(footer('John Avon', 2019), store);
    assert.ok(out.candidates.length > 0);
    for (const row of out.candidates) assert.equal(row.artist, 'John Avon');
  });

  test('a readable collector number still wins outright', async () => {
    // The narrowing is a fallback and must not get in front of the
    // thing that actually identifies a card.
    const [id, , set, num, artist, year] = ISLANDS.find(
      ([, , s, n]) => s && n && /^\d+$/.test(n));
    const store = await indexed();
    const out = await identifyLocally(
      `Island\n${set.toUpperCase()} • EN ${num}\nIllus. ${artist}\n`
      + `© ${year}`, store);
    assert.equal(out.candidates[0]?.printing_id, id);
  });
});

describe('how much of the problem this actually solves', () => {
  /**
   * The claim, locked in.
   *
   * Every Island in the index, each one turned back into the footer
   * it would have shown, run through the identifier. The numbers
   * below were measured before the code was written and are asserted
   * with room underneath, so a change that quietly makes the
   * narrowing worse fails here rather than on somebody's kitchen
   * table.
   */
  test('most Islands reach a shortlist a person can read', async () => {
    const store = await indexed();
    let toThree = 0;
    let toFive = 0;
    let none = 0;
    for (const [, , , , artist, year] of CREDITED) {
      const out = await identifyLocally(footer(artist, year), store);
      const n = out.candidates.length;
      if (!n) none += 1;
      if (n && n <= 3) toThree += 1;
      if (n && n <= 5) toFive += 1;
    }
    const pct = (k) => (k / CREDITED.length) * 100;
    assert.ok(pct(toThree) >= 65,
      `only ${pct(toThree).toFixed(1)}% reached three or fewer`);
    assert.ok(pct(toFive) >= 80,
      `only ${pct(toFive).toFixed(1)}% reached five or fewer`);
    assert.equal(none, 0, 'every credit line should narrow to something');
  });

  test('and the right card is always among them', async () => {
    // A shortlist that does not contain the card is worse than no
    // shortlist: it invites a confident wrong tap.
    const store = await indexed();
    for (const [id, , , , artist, year] of CREDITED) {
      const out = await identifyLocally(footer(artist, year), store);
      assert.ok(out.candidates.some((c) => c.printing_id === id),
        `${artist} ${year} lost its own printing`);
    }
  });
});

describe('an index from before the artist column', () => {
  test('narrowing is skipped rather than crashing', async () => {
    // A phone that has not refreshed its index has artist '' on
    // every row. It should get the old refusal, not an exception and
    // not a shortlist of everything.
    const store = new LocalStore(new RealDatabase());
    await store.init();
    await store.putCatalogue(ISLANDS.map(
      ([id, name, set, num]) => [id, name, set, num, 0, 'common']));

    const out = await identifyLocally(footer('John Avon', 2004), store);
    assert.equal(out.candidates.length, 0);
    assert.match(out.reason, /too many to choose from/);
  });

  test('and it says to refresh the index, rather than blaming the card',
    async () => {
      // The migration gives an existing install the column; only a
      // fresh index download fills it. Without this the phone would
      // never scan a basic land and never say why -- it would read
      // as the artist line being unreadable on every card.
      const store = new LocalStore(new RealDatabase());
      await store.init();
      await store.putCatalogue(ISLANDS.map(
        ([id, name, set, num]) => [id, name, set, num, 0, 'common']));

      const out = await identifyLocally(footer('John Avon', 2004), store);
      assert.match(out.reason, /refresh the index/i);
      assert.doesNotMatch(out.reason, /did not read/);
    });

  test('a current index blames the card, not the index', async () => {
    const store = await indexed();
    const out = await identifyLocally(
      'Island\nIllus. Xxxx Yyyy\n© 2004', store);
    assert.match(out.reason, /did not read/);
    assert.doesNotMatch(out.reason, /refresh the index/i);
  });
});

describe('a printing with no artist recorded', () => {
  test('is not matched by an empty credit line', async () => {
    // One real Island has no artist in Scryfall's data. An empty
    // string is a substring of every card's text, so a careless
    // search would return this one for everything.
    const store = await indexed();
    const out = await identifyLocally(
      'Island\nBasic Land\n© 2021 Wizards', store);
    assert.equal(out.candidates.length, 0);
  });
});
