/**
 * Dead ends found in the 2026-09-28 audit, pinned.
 *
 * Each of these was a feature that existed underneath with no working way
 * to reach it, or a flow that quietly lost part of what the user asked for.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { addToDeck, deckZonesFromText } from '../src/lib/decks.ts';
import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';

describe('a deck copied from text keeps its commander', () => {
  // How the desktop writes a built or saved commander deck: its own header.
  const TEXT = 'Commander:\n1 Etrata, the Silencer\n\nMainboard:\n1 Sol Ring\n35 Island\n';

  test('the commander zone comes across', () => {
    const zones = deckZonesFromText(TEXT);
    assert.deepEqual(zones.commander?.map((e) => e.name), ['Etrata, the Silencer']);
  });

  test('and is not also left in the main deck', () => {
    const zones = deckZonesFromText(TEXT);
    assert.ok(!zones.decklist.some((e) => e.name === 'Etrata, the Silencer'));
    assert.equal(zones.decklist.reduce((n, e) => n + e.qty, 0), 36);
  });

  test('a deck with no commander has no commander zone, as before', () => {
    const zones = deckZonesFromText('4 Lightning Bolt\n20 Mountain\n');
    assert.equal('commander' in zones, false);
  });
});

describe('scanning several copies into a deck', () => {
  test('the deck gains as many as were filed, not one', () => {
    const next = addToDeck([], { name: 'Island', qty: 10 }, 10);
    assert.equal(next[0].qty, 10);
  });

  test('and adds them to a slot that is already there', () => {
    const next = addToDeck([{ name: 'Island', qty: 5 }], 'Island', 3);
    assert.equal(next.find((e) => e.name === 'Island').qty, 8);
  });
});

async function makePhone() {
  const desktop = new FakeDesktop();
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const decks = new DeckStore(db);
  const state = buildAppState(
    store, { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1', testUuid, desktop.fetchImpl, decks);
  desktop.reachable = false;
  return { store, state, decks };
}

beforeEach(() => resetUuid());

describe('the Sets filter needs no PC', () => {
  test('it lists the sets in the phone’s own index, newest first', async () => {
    const { store, state } = await makePhone();
    store.catalogueSets = async () => new Set(['lea', 'mh3', 'dsk']);
    store.setDirectory = async () => ({
      lea: { name: 'Alpha', year: 1993, iconUri: '' },
      mh3: { name: 'Modern Horizons 3', year: 2024, iconUri: '' },
      dsk: { name: 'Duskmourn', year: 2024, iconUri: '' },
    });
    const sets = await state.localSets();
    assert.deepEqual(sets.map((s) => s.set_code), ['dsk', 'mh3', 'lea']);
  });

  test('an empty index is an empty list, not an error', async () => {
    const { store, state } = await makePhone();
    store.catalogueSets = async () => new Set();
    store.setDirectory = async () => ({});
    assert.deepEqual(await state.localSets(), []);
  });
});

describe('a mis-tapped game result can be taken back', () => {
  test('forgetting the game just logged undoes it, offline', async () => {
    const { state, decks } = await makePhone();
    const uid = await state.logGame('deck-1', 'win');
    assert.equal((await decks.recordFor('deck-1')).games, 1);

    await state.forgetGame('deck-1', uid);
    assert.equal((await decks.recordFor('deck-1')).games, 0);
  });
});
