/**
 * Noticing that a new set came out.
 *
 * The index is a snapshot and Magic prints a set every few weeks, so a
 * phone that downloaded once is wrong by the next prerelease. The desktop
 * always had an update path; the phone had none, and a standalone phone
 * could not see a new set by ANY means.
 *
 * These pin the decision of when to look, which is the half that is easy
 * to get subtly wrong and impossible to notice: a check that never fires
 * and a check that fires on every app open both look like "working" until
 * someone reads a bill or misses a release.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  COOLDOWN_MS,
  QUARTER_MS,
  dueForCheck,
  lastCheckedInWords,
  missingSets,
} from '../src/lib/index-freshness.ts';
import { setReleases } from '../src/lib/scryfall.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase, testUuid } from './harness.mjs';

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse('2026-09-20T12:00:00Z');

describe('whether to go and look', () => {
  test('not at all unless it was turned on', () => {
    // Opt-in means opt-in. Everything below assumes enabled: true, so
    // this is the one guarding all of it.
    assert.equal(dueForCheck({
      lastCheckedAt: 0, now: NOW, releases: [{ code: 'aaa', at: NOW - DAY }],
    }), null);
  });

  test('a phone that has never looked, looks', () => {
    assert.equal(dueForCheck({
      lastCheckedAt: 0, now: NOW, enabled: true,
    }), 'never');
  });

  test('a set that came out since the last look is the whole point', () => {
    // The reason the release calendar is fetched rather than a timer:
    // this fires the day the set lands, not up to three months later.
    assert.equal(dueForCheck({
      lastCheckedAt: NOW - 10 * DAY,
      now: NOW,
      releases: [{ code: 'aaa', at: NOW - 2 * DAY }],
      enabled: true,
    }), 'release');
  });

  test('but a set that is still to come is not news yet', () => {
    // Prerelease weekend is not release day, and checking early would
    // burn the cooldown on an answer of "no".
    assert.equal(dueForCheck({
      lastCheckedAt: NOW - 10 * DAY,
      now: NOW,
      releases: [{ code: 'aaa', at: NOW + 7 * DAY }],
      enabled: true,
    }), null);
  });

  test('nor one that was already out at the last look', () => {
    // Either it was picked up then or it was declined. Re-offering the
    // same set every day is nagging.
    assert.equal(dueForCheck({
      lastCheckedAt: NOW - 2 * DAY,
      now: NOW,
      releases: [{ code: 'aaa', at: NOW - 30 * DAY }],
      enabled: true,
    }), null);
  });

  test('a quarter with no releases at all still gets a look', () => {
    // The floor. Promos, Secret Lairs and errata have no release date we
    // track, and the set list itself may have been unreachable.
    assert.equal(dueForCheck({
      lastCheckedAt: NOW - QUARTER_MS - DAY,
      now: NOW,
      releases: [],
      enabled: true,
    }), 'quarter');
  });

  test('and a quarter less a day does not', () => {
    assert.equal(dueForCheck({
      lastCheckedAt: NOW - QUARTER_MS + DAY,
      now: NOW,
      releases: [],
      enabled: true,
    }), null);
  });

  test('release day does not re-ask twenty times', () => {
    /*
     * An app gets opened all day and "a set came out" stays true all
     * day. Without the cooldown that is twenty set-list fetches and
     * twenty prompts, on the very day the user is most likely to be
     * using it.
     *
     * The release has to fall INSIDE the window since the last check,
     * or the cooldown is not what is holding it back and this passes
     * whether or not the cooldown exists -- which is how it was written
     * the first time.
     */
    const lastCheckedAt = NOW - 2 * 60 * 60 * 1000;
    const justOut = NOW - 60 * 60 * 1000;
    assert.ok(justOut > lastCheckedAt && justOut <= NOW,
      'precondition: without the cooldown this would fire');

    assert.equal(dueForCheck({
      lastCheckedAt, now: NOW, enabled: true,
      releases: [{ code: 'aaa', at: justOut }],
    }), null);

    // And it DOES fire once the cooldown has passed.
    assert.equal(dueForCheck({
      lastCheckedAt: NOW - COOLDOWN_MS - 1000,
      now: NOW,
      releases: [{ code: 'aaa', at: NOW - 60 * 60 * 1000 }],
      enabled: true,
    }), 'release');
  });

  test('the cooldown does not outrank never having looked', () => {
    // A fresh install checks immediately; there is nothing to cool down
    // from, and lastCheckedAt of 0 is not "checked at the epoch".
    assert.equal(dueForCheck({
      lastCheckedAt: 0, now: NOW, enabled: true,
    }), 'never');
  });
});

describe('whether we are missing a set', () => {
  /**
   * Deliberately NOT a timestamp comparison.
   *
   * Scryfall rebuilds its bulk files daily whether or not a card changed,
   * so `updated_at` says "newer" every single day. An app built on that
   * would offer a 74 MB download every morning and be proved wrong every
   * morning, and within a week nobody reads the prompt.
   *
   * Which sets are missing is exact, survives any number of no-op
   * rebuilds, and gives the prompt something worth saying.
   */
  test('a released set we hold no cards from is missing', () => {
    assert.deepEqual(
      missingSets(new Set(['cmm']), [{ code: 'xyz', at: NOW - DAY }], NOW),
      ['xyz']);
  });

  test('a set we already hold is not', () => {
    assert.deepEqual(
      missingSets(new Set(['cmm', 'xyz']), [{ code: 'xyz', at: NOW - DAY }],
                  NOW),
      []);
  });

  test('a set that has not come out yet is not missing', () => {
    // Spoiled is not released. Being "behind" on a set nobody can own
    // yet would put a permanent download prompt on the screen for the
    // weeks between preview season and release day.
    assert.deepEqual(
      missingSets(new Set(['cmm']), [{ code: 'soon', at: NOW + DAY }], NOW),
      []);
  });

  test('case does not decide it', () => {
    // Scryfall sends lower case and the index has held both over time.
    assert.deepEqual(
      missingSets(new Set(['xyz']), [{ code: 'XYZ', at: NOW - DAY }], NOW),
      []);
  });

  test('an empty release list means nothing is missing, not everything',
    () => {
      // The list failing to load must not read as "you are behind on
      // every set in Magic".
      assert.deepEqual(missingSets(new Set(), [], NOW), []);
    });
});

describe('reading the release calendar', () => {
  function reply(data) {
    return async () => ({ ok: true, status: 200, json: async () => ({ data }) });
  }

  test('paper sets come back as dates, in order', async () => {
    const out = await setReleases(reply([
      { code: 'b', set_type: 'expansion', released_at: '2026-11-14' },
      { code: 'a', set_type: 'expansion', released_at: '2026-09-26' },
    ]));
    assert.deepEqual(out, [
      { code: 'a', at: Date.parse('2026-09-26') },
      { code: 'b', at: Date.parse('2026-11-14') },
    ]);
  });

  test('tokens and memorabilia are not releases', async () => {
    // They have release dates and they put no card in anybody's hand.
    // Counting them would fire the check on noise.
    const out = await setReleases(reply([
      { code: 't', set_type: 'token', released_at: '2026-09-26' },
      { code: 'm', set_type: 'memorabilia', released_at: '2026-09-26' },
    ]));
    assert.deepEqual(out, []);
  });

  test('nor are digital-only sets', async () => {
    const out = await setReleases(reply([
      { code: 'd', set_type: 'expansion', released_at: '2026-09-26',
        digital: true },
    ]));
    assert.deepEqual(out, []);
  });

  test('a set with no date is skipped rather than counted as 1970',
    async () => {
      // An unparseable date as epoch 0 is "came out in 1970", which is
      // before every possible last-check and would never fire -- or,
      // worse, NaN leaking into a comparison.
      const out = await setReleases(reply([
        { code: 'x', set_type: 'expansion' },
        { code: 'y', set_type: 'expansion', released_at: 'soon' },
      ]));
      assert.deepEqual(out, []);
    });

  test('a refusal from Scryfall says so', async () => {
    await assert.rejects(() => setReleases(async () => ({
      ok: false, status: 503, json: async () => ({}),
    })), /503/);
  });
});

describe('saying when we last looked', () => {
  test('never is never, not "56 years ago"', () => {
    assert.equal(lastCheckedInWords(0, NOW), 'never');
  });

  test('today, yesterday, days, months', () => {
    assert.equal(lastCheckedInWords(NOW - 60 * 1000, NOW), 'today');
    assert.equal(lastCheckedInWords(NOW - DAY, NOW), 'yesterday');
    assert.equal(lastCheckedInWords(NOW - 5 * DAY, NOW), '5 days ago');
    assert.equal(lastCheckedInWords(NOW - 90 * DAY, NOW), 'about 3 months ago');
  });
});

describe('asking Scryfall, on a real index', () => {
  /**
   * End to end over the store, because the parts being joined are the
   * ones that were wrong: which sets the phone holds, which sets exist,
   * and the arithmetic between them.
   */
  async function phone(sets) {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    const state = buildAppState(
      store, { baseUrl: '', token: '' }, 'phone-1', testUuid,
      undefined, undefined, undefined,
      async () => ({
        ok: true,
        status: 200,
        json: async () => ({ data: sets }),
      }),
    );
    return { store, state };
  }

  test('a phone holding the current sets is up to date', async () => {
    const { store, state } = await phone([
      { code: 'cmm', set_type: 'expansion', released_at: '2026-01-01' },
    ]);
    await store.putCatalogue([['p-1', 'Sol Ring', 'cmm', '410', 1, 'common']]);

    const out = await state.indexFreshness(NOW);
    assert.equal(out.stale, false);
    assert.deepEqual(out.missing, []);
  });

  test('and one that missed a release says which', async () => {
    const { store, state } = await phone([
      { code: 'cmm', set_type: 'expansion', released_at: '2026-01-01' },
      { code: 'xyz', set_type: 'expansion', released_at: '2026-09-01' },
    ]);
    await store.putCatalogue([['p-1', 'Sol Ring', 'cmm', '410', 1, 'common']]);

    const out = await state.indexFreshness(NOW);
    assert.equal(out.stale, true);
    assert.deepEqual(out.missing, ['xyz']);
  });

  test('checking records when we looked', async () => {
    // So the cooldown and the quarterly floor have something to measure
    // from -- without this the check fires on every single app open.
    const { store, state } = await phone([]);
    assert.equal(await state.lastCheckedAt(), 0);
    await state.indexFreshness(NOW);
    assert.equal(await state.lastCheckedAt(), NOW);
  });

  test('the scheduled check does nothing until it is turned on', async () => {
    const { state } = await phone([
      { code: 'xyz', set_type: 'expansion', released_at: '2026-09-01' },
    ]);
    assert.equal(await state.autoCheck(NOW), null, 'opt-in means opt-in');

    await state.setAutoCheck(true);
    const out = await state.autoCheck(NOW);
    assert.deepEqual(out, { stale: true, reason: 'never' });
  });

  test('and it does not run twice in a day', async () => {
    const { state } = await phone([
      { code: 'xyz', set_type: 'expansion', released_at: '2026-09-01' },
    ]);
    await state.setAutoCheck(true);
    await state.autoCheck(NOW);
    assert.equal(await state.autoCheck(NOW + 60 * 1000), null);
  });
});
