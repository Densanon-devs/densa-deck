/**
 * The phone hears about new versions of itself.
 *
 * It had no update check at all; every APK reached people only because
 * somebody told them. Same pattern as the desktop and Table of War: one GET
 * of a static JSON, a banner, a manual download, on by default.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { compareVersions, MOBILE_FEED_URL, updateFrom } from '../src/lib/app-update.ts';
import { VERSION } from '../src/lib/version.ts';
import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';

const PINNED = 'https://github.com/Densanon-devs/densa-deck/releases/download/companion-v9.0.0/DensaDeck-Companion-9.0.0.apk';

describe('comparing versions', () => {
  test('numerically, part by part', () => {
    assert.equal(compareVersions('0.62.0', '0.61.9'), 1);
    assert.equal(compareVersions('0.62.0', '0.62.0'), 0);
    assert.equal(compareVersions('0.9.0', '0.10.0'), -1, 'string comparison would say 9 > 10');
    assert.equal(compareVersions('1.0', '1.0.0'), 0);
  });
});

describe('reading the feed', () => {
  test('a newer version with a pinned https link is an update', () => {
    const u = updateFrom({ version: '9.0.0', downloadUrl: PINNED, changelog: ['a', 5] }, '0.62.0');
    assert.equal(u.latest, '9.0.0');
    assert.equal(u.url, PINNED);
    assert.deepEqual(u.changelog, ['a']);
  });

  test('the same or an older version is not', () => {
    assert.equal(updateFrom({ version: '0.62.0', downloadUrl: PINNED }, '0.62.0'), null);
    assert.equal(updateFrom({ version: '0.1.0', downloadUrl: PINNED }, '0.62.0'), null);
  });

  test('a malformed feed or a non-https link is ignored, not shown', () => {
    assert.equal(updateFrom(null, '0.1.0'), null);
    assert.equal(updateFrom('nope', '0.1.0'), null);
    assert.equal(updateFrom({ version: '9.0.0' }, '0.1.0'), null);
    assert.equal(updateFrom({ version: '9.0.0', downloadUrl: 'http://x/app.apk' }, '0.1.0'), null);
    assert.equal(updateFrom({ version: '9.0.0', downloadUrl: 'javascript:alert(1)' }, '0.1.0'), null);
  });
});

async function phone(feed, { failWith } = {}) {
  const calls = [];
  const plainFetch = async (url) => {
    calls.push(url);
    if (failWith) throw new Error(failWith);
    return { ok: true, status: 200, json: async () => feed };
  };
  const desktop = new FakeDesktop();
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const state = buildAppState(store, { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1', testUuid, desktop.fetchImpl, new DeckStore(db), undefined, plainFetch);
  return { state, calls };
}

beforeEach(() => resetUuid());

describe('checking from the phone', () => {
  test('on by default, and it asks the toolkit site, not the PC', async () => {
    const { state, calls } = await phone({ version: '9.0.0', downloadUrl: PINNED });
    assert.equal(await state.appUpdatesEnabled(), true);
    const found = await state.checkAppUpdate();
    assert.equal(found.latest, '9.0.0');
    assert.deepEqual(calls, [MOBILE_FEED_URL]);
  });

  test('switched off, no request is made at all', async () => {
    const { state, calls } = await phone({ version: '9.0.0', downloadUrl: PINNED });
    await state.setAppUpdatesEnabled(false);
    assert.equal(await state.checkAppUpdate(), null);
    assert.deepEqual(calls, []);
  });

  test('"Check now" still checks when the automatic one is off', async () => {
    const { state, calls } = await phone({ version: '9.0.0', downloadUrl: PINNED });
    await state.setAppUpdatesEnabled(false);
    assert.equal((await state.checkAppUpdate(true)).latest, '9.0.0');
    assert.equal(calls.length, 1);
  });

  test('no connection: the on-open check is silent, Check now says why', async () => {
    const { state } = await phone(null, { failWith: 'offline' });
    assert.equal(await state.checkAppUpdate(), null);
    await assert.rejects(state.checkAppUpdate(true), /offline/);
  });

  test('this build is never offered to itself', async () => {
    const { state } = await phone({ version: VERSION, downloadUrl: PINNED });
    assert.equal(await state.checkAppUpdate(), null);
  });
});
