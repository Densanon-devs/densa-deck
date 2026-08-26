/**
 * Naming a collection, and where the scanner files into.
 *
 * Both are small, and both are ways to quietly lose cards. Two collections
 * that look identical in a list cannot be told apart afterwards; a scan target
 * that resets between visits scatters half a box across the wrong shelves
 * without ever reporting a problem.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  MAX_COLLECTION_NAME,
  checkCollectionName,
} from '../src/lib/collections.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { MemoryDatabase, FakeDesktop } from './harness.mjs';

describe('naming one', () => {
  test('a name survives', () => {
    assert.deepEqual(checkCollectionName('Trade binder'), {
      ok: true,
      name: 'Trade binder',
    });
  });

  test('nothing is not a name', () => {
    assert.equal(checkCollectionName('   ').ok, false);
    assert.equal(checkCollectionName('').ok, false);
  });

  test('inner whitespace is collapsed, not just trimmed', () => {
    // "Deck  box" and "Deck box" are the same name to a person, and two
    // collections that look identical in a list are worse than a rejection.
    assert.equal(checkCollectionName('  Deck   box  ').name, 'Deck box');
  });

  test('an absurd name is refused rather than truncated', () => {
    // Truncating silently produces a name the user did not choose and cannot
    // predict, and two long names could truncate to the same thing.
    const verdict = checkCollectionName('x'.repeat(MAX_COLLECTION_NAME + 1));
    assert.equal(verdict.ok, false);
    assert.match(verdict.reason ?? '', /under/);
  });

  test('a duplicate is refused whatever its capitalisation', () => {
    const verdict = checkCollectionName('trade binder', [
      { name: 'Trade Binder' },
    ]);
    assert.equal(verdict.ok, false);
    assert.ok(verdict.reason?.includes('Trade Binder'));
  });

  test('a duplicate that differs only in spacing is refused too', () => {
    assert.equal(
      checkCollectionName('Trade  binder', [{ name: 'Trade binder' }]).ok,
      false,
    );
  });

  test('a name unlike the others is fine', () => {
    assert.equal(
      checkCollectionName('Cube', [{ name: 'Trade binder' }]).ok,
      true,
    );
  });
});

describe('where the scanner files into', () => {
  const build = async () => {
    const database = new MemoryDatabase();
    const store = new LocalStore(database);
    await store.init();
    const state = buildAppState(
      store,
      { baseUrl: 'http://100.64.0.1:8792', token: 't' },
      'phone-test',
      () => 'id-' + Math.floor(Math.random() * 1e9),
    );
    return { store, state };
  };

  test('a fresh phone scans into the default collection', async () => {
    const { state } = await build();
    assert.equal(await state.scanTarget(), DEFAULT_COLLECTION_UID);
  });

  test('a chosen collection is remembered', async () => {
    const { state } = await build();
    const uid = await state.newCollection('Trade binder');
    await state.rememberScanTarget(uid);
    assert.equal(await state.scanTarget(), uid);
  });

  test('a collection that no longer exists does not strand the scanner', async () => {
    // Deleting it on the desktop must not leave the phone pointing at
    // something gone — every scan after that would file into a collection
    // nothing can show.
    const { state } = await build();
    await state.rememberScanTarget('a-collection-that-was-deleted');
    assert.equal(await state.scanTarget(), DEFAULT_COLLECTION_UID);
  });
});
