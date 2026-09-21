/**
 * Sets the phone is told about and can never have.
 *
 * Reported as: a complete index download finishes, you press Check,
 * and it says the same 28 sets are still missing — with a button
 * offering to download 78 MB to fix it.
 *
 * The 28 are real sets and the index is right not to have them. It
 * keeps English paper cards, and these have none:
 *
 *   FBB   Foreign Black Border           0 English paper cards
 *   4BB   Fourth Edition FBB             0
 *   BCHR  Chronicles FBB                 0
 *   REN   Renaissance                    0
 *   RIN   Rinascimento                   0
 *
 * They are non-English printings of cards that exist in English
 * elsewhere. Scryfall lists them as released; no download will ever
 * bring them; and comparing the two lists says "behind" for ever.
 *
 * The fix is to let a finished download settle the question. Whatever
 * a complete file did not bring is not late, it is not coming.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { buildAppState } from '../src/lib/app-state.ts';
import {
  SETTLE_MS,
  absentAfterRefresh,
  missingSets,
} from '../src/lib/index-freshness.ts';
import { LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse('2026-09-21T00:00:00Z');

/** Real codes, real shape: five that cannot come and one that can. */
const RELEASES = [
  { code: 'fbb', at: Date.parse('1994-04-01') },
  { code: '4bb', at: Date.parse('1995-05-01') },
  { code: 'bchr', at: Date.parse('1995-07-01') },
  { code: 'ren', at: Date.parse('1995-08-01') },
  { code: 'rin', at: Date.parse('1995-08-01') },
  { code: 'blb', at: Date.parse('2024-08-02') },
];

describe('what a finished download rules out', () => {
  test('a released set the file did not bring is not coming', () => {
    const held = new Set(['blb']);
    assert.deepEqual(absentAfterRefresh(held, RELEASES, NOW),
      ['fbb', '4bb', 'bchr', 'ren', 'rin']);
  });

  test('a set the file DID bring is obviously not absent', () => {
    const held = new Set(['blb', 'fbb', '4bb', 'bchr', 'ren', 'rin']);
    assert.deepEqual(absentAfterRefresh(held, RELEASES, NOW), []);
  });

  test('a set released this week is late, not absent', () => {
    // Scryfall rebuilds the bulk files daily, so a set out yesterday
    // and not in today's file is a matter of hours. Writing it off
    // would mean never being told about it again.
    const fresh = [...RELEASES, { code: 'new', at: NOW - 2 * DAY }];
    assert.ok(!absentAfterRefresh(new Set(['blb']), fresh, NOW)
      .includes('new'));
  });

  test('and a set released a fortnight ago is absent', () => {
    const old = [...RELEASES, { code: 'old', at: NOW - SETTLE_MS - DAY }];
    assert.ok(absentAfterRefresh(new Set(['blb']), old, NOW)
      .includes('old'));
  });

  test('an unreleased set is neither', () => {
    const soon = [...RELEASES, { code: 'soon', at: NOW + 30 * DAY }];
    assert.ok(!absentAfterRefresh(new Set(['blb']), soon, NOW)
      .includes('soon'));
  });
});

describe('and what the prompt then says', () => {
  test('nothing, once the download has settled it', () => {
    // The reported bug, stated as a test: download everything, then
    // ask, and be told you are up to date.
    const held = new Set(['blb']);
    const absent = new Set(absentAfterRefresh(held, RELEASES, NOW));
    assert.deepEqual(missingSets(held, RELEASES, NOW, absent), []);
  });

  test('without it, the same five for ever', () => {
    // The old behaviour, kept so the difference is visible.
    const held = new Set(['blb']);
    assert.equal(missingSets(held, RELEASES, NOW).length, 5);
  });

  test('a genuinely new set still gets through', () => {
    // The whole feature has to keep working: writing off the
    // unreachable must not write off the next real release.
    const held = new Set(['blb']);
    const absent = new Set(absentAfterRefresh(held, RELEASES, NOW));
    const later = [...RELEASES, { code: 'dsk', at: NOW - DAY }];
    assert.deepEqual(missingSets(held, later, NOW, absent), ['dsk']);
  });

  test('a set that later gains an English printing comes back', () => {
    // The list is replaced on every refresh rather than added to, so
    // this needs no one to notice. Here: it is in `held` now, so a
    // recomputed absent list no longer contains it.
    const held = new Set(['blb', 'ren']);
    const absent = new Set(absentAfterRefresh(held, RELEASES, NOW));
    assert.ok(!absent.has('ren'));
  });

  test('no absent list at all behaves exactly as before', () => {
    // A phone that has not completed a refresh since this shipped.
    const held = new Set(['blb']);
    assert.equal(missingSets(held, RELEASES, NOW, new Set()).length, 5);
  });
});

describe('through the app, which is where it was wrong', () => {
  /*
    The rule above was never the problem -- nothing computed it at
    all. `indexFreshness` compared the two lists and reported the
    difference, and no part of the app had any way to say "that
    difference is permanent".
  */
  const SETS_JSON = {
    data: [
      { code: 'fbb', released_at: '1994-04-01', set_type: 'core',
        name: 'Foreign Black Border' },
      { code: 'ren', released_at: '1995-08-01', set_type: 'masters',
        name: 'Renaissance' },
      { code: 'blb', released_at: '2024-08-02', set_type: 'expansion',
        name: 'Bloomburrow' },
    ],
  };

  /** Two empty files, so the walk runs and finds nothing new. */
  const BULK_JSON = {
    data: [
      { type: 'default_cards', jsonl_download_uri: 'https://x/d.jsonl.gz',
        compressed_size: 10, updated_at: '2026-09-21' },
      { type: 'oracle_cards', jsonl_download_uri: 'https://x/o.jsonl.gz',
        compressed_size: 10, updated_at: '2026-09-21' },
    ],
  };

  /** A phone whose index holds Bloomburrow and nothing else. */
  async function phone() {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    await store.putCatalogue([
      ['p-1', 'Island', 'blb', '280', 0, 'common', 0.25, null, 'Sam R', 2024],
    ]);
    // URL-aware, because the index fetch asks Scryfall two different
    // questions and a fake that answers both with the same body makes
    // `bulkSources` throw before any of this is reached.
    const state = buildAppState(
      store, { baseUrl: '', token: '' }, 'p1', testUuid,
      undefined, undefined, undefined,
      async (url) => ({
        ok: true,
        status: 200,
        json: async () => (String(url).includes('/bulk-data')
          ? BULK_JSON : SETS_JSON),
      }),
    );
    return { store, state };
  }

  test('before a refresh, both are reported missing', async () => {
    const { state } = await phone();
    const { stale, missing } = await state.indexFreshness();
    assert.equal(stale, true);
    assert.deepEqual(missing.sort(), ['fbb', 'ren']);
  });

  test('a finished refresh writes them off', async () => {
    const { store, state } = await phone();
    // The download itself is not what is being tested; the bookkeeping
    // after it is. A no-op chunk source stands in for the 78 MB.
    await state.startIndexFetch(async function* () {}, 'scryfall', true);
    const raw = await store.getMeta('index.absentSets');
    assert.deepEqual(JSON.parse(raw ?? '[]').sort(), ['fbb', 'ren']);
  });

  test('and the next check says up to date', async () => {
    const { state } = await phone();
    await state.startIndexFetch(async function* () {}, 'scryfall', true);
    const { stale, missing } = await state.indexFreshness();
    assert.deepEqual(missing, []);
    assert.equal(stale, false);
  });
});
