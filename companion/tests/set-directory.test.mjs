/**
 * What a set code means.
 *
 * "GRN #184" is not an answer to "which of these seven printings am I
 * holding", and for an old card nobody has the codes memorised. The
 * name, the year and the set's own symbol are what a person recognises
 * a printing by.
 *
 * Worth its own tests because the write happens inside a swallowing
 * try/catch on the index path -- a broken query there would be
 * completely silent, and would only show up as a pick list that never
 * learned any names.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

const GRN = { code: 'grn', name: 'Guilds of Ravnica',
              at: Date.parse('2018-10-05'),
              iconUri: 'https://svgs.scryfall.io/sets/grn.svg' };

async function store() {
  const s = new LocalStore(new MemoryDatabase());
  await s.init();
  return s;
}

describe('remembering what the set codes mean', () => {
  test('a set goes in and comes back with its year', async () => {
    const s = await store();
    await s.putSets([GRN]);
    const out = await s.setDirectory();
    assert.equal(out.grn.name, 'Guilds of Ravnica');
    assert.equal(out.grn.year, 2018);
    assert.match(out.grn.iconUri, /grn\.svg$/);
  });

  test('writing the same set twice updates rather than duplicates',
    async () => {
      // Every index refresh rewrites the whole list, so this happens on
      // the second fetch and every one after it.
      //
      // NOT a guard on the ON CONFLICT clause, and it should not be
      // mistaken for one: MemoryDatabase upserts by the primary key it
      // was told about whether or not the SQL asks it to, so removing
      // the clause leaves this test green. On a device SQLite enforces
      // it -- a plain re-INSERT would raise a constraint error, and the
      // symptom would be set names that never update rather than
      // duplicate rows.
      const s = await store();
      await s.putSets([GRN]);
      await s.putSets([{ ...GRN, name: 'Guilds of Ravnica (fixed)' }]);
      const out = await s.setDirectory();
      assert.equal(Object.keys(out).length, 1);
      assert.match(out.grn.name, /fixed/);
    });

  test('an unknown code is simply absent, not a blank row', async () => {
    // The pick list falls back to the printed code when a set is
    // missing, which must be distinguishable from a set named "".
    const s = await store();
    await s.putSets([GRN]);
    assert.equal((await s.setDirectory()).zzz, undefined);
  });

  test('a set with no release date has no year rather than 1970',
    async () => {
      const s = await store();
      await s.putSets([{ code: 'x', name: 'Unknown', at: 0, iconUri: '' }]);
      assert.equal((await s.setDirectory()).x.year, 0);
    });
});

describe('a phone whose index predates set names', () => {
  test('fills the directory on first use rather than re-downloading',
    async () => {
      // Every phone already in the wild. Making them re-fetch a hundred
      // thousand cards to learn what "GRN" stands for is not a trade
      // anyone would take.
      const s = await store();
      let asked = 0;
      const state = buildAppState(
        s, { baseUrl: '', token: '' }, 'phone-1', testUuid,
        undefined, undefined, undefined,
        async () => {
          asked += 1;
          return {
            ok: true,
            status: 200,
            json: async () => ({ data: [{
              code: 'grn', set_type: 'expansion', released_at: '2018-10-05',
              name: 'Guilds of Ravnica',
              icon_svg_uri: 'https://svgs.scryfall.io/sets/grn.svg',
            }] }),
          };
        });

      const out = await state.setDirectory();
      assert.equal(asked, 1);
      assert.equal(out.grn.name, 'Guilds of Ravnica');

      // And not again once it is known.
      await state.setDirectory();
      assert.equal(asked, 1);
    });

  test('offline it falls back to the codes rather than failing', async () => {
    const s = await store();
    const state = buildAppState(
      s, { baseUrl: '', token: '' }, 'phone-1', testUuid,
      undefined, undefined, undefined,
      async () => { throw new Error('no signal'); });

    assert.deepEqual(await state.setDirectory(), {});
  });
});
