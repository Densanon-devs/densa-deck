/**
 * What the screens are told, and when.
 *
 * The UI is thin on purpose — React Native cannot be exercised here — so the
 * decisions it renders are made in `app-state.ts` and tested here instead.
 */

import { strict as assert } from 'node:assert';
import { beforeEach, describe, test } from 'node:test';

import { buildAppState } from '../src/lib/app-state.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from '../src/lib/store.ts';
import { FakeDesktop, MemoryDatabase, resetUuid, testUuid } from './harness.mjs';

const SOL = {
  printing_id: 'p-sol',
  card_name: 'Sol Ring',
  oracle_id: 'o-sol',
  finish: 'nonfoil',
  condition: 'NM',
  language: 'en',
  location: '',
  collection_uid: DEFAULT_COLLECTION_UID,
  reason: 'phone',
};

async function build(desktop) {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  const state = buildAppState(
    store,
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1',
    testUuid,
    desktop.fetchImpl,
  );
  return { store, state };
}

beforeEach(() => resetUuid());

describe('what the screens are told', () => {
  test('offline is a state, not an error', async () => {
    // A companion that shouts every time it cannot reach home would spend
    // most of its life shouting, and the edits are safe regardless.
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { state } = await build(desktop);

    const snapshot = await state.sync();
    assert.equal(snapshot.connection, 'offline');
    assert.equal(snapshot.lastError, undefined);
  });

  test('being unpaired is an error, because it needs the user', async () => {
    const desktop = new FakeDesktop();
    desktop.paired = false;
    const { state } = await build(desktop);

    const snapshot = await state.sync();
    assert.equal(snapshot.connection, 'unpaired');
    assert.match(snapshot.lastError, /QR/);
  });

  test('a good sync records when it happened', async () => {
    const desktop = new FakeDesktop();
    const { state } = await build(desktop);
    const snapshot = await state.sync();
    assert.equal(snapshot.connection, 'connected');
    assert.ok(snapshot.lastSyncAt);
  });

  test('unsent edits are counted so the user can see them', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { store, state } = await build(desktop);

    const engineState = buildAppState(
      store, { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
      'phone-1', testUuid, desktop.fetchImpl,
    );
    await engineState.sync();       // fails, offline
    await state.refreshPending();
    assert.equal((await state.sync()).pendingEdits, 0);
  });

  test('subscribers are told immediately, not only on the next change', async () => {
    const desktop = new FakeDesktop();
    const { state } = await build(desktop);
    let seen = null;
    state.subscribe((s) => { seen = s; });
    assert.ok(seen, 'a screen that subscribes must render something at once');
  });

  test('a backlog is drained rather than left half-applied', async () => {
    const desktop = new FakeDesktop();
    const { state } = await build(desktop);

    // The desktop says there is more waiting than one round carried.
    let rounds = 0;
    const realHandle = desktop.handle.bind(desktop);
    desktop.handle = (route, payload) => {
      const reply = realHandle(route, payload);
      if (route === 'sync/pull') {
        rounds += 1;
        return { ...reply, more: rounds < 3 };
      }
      return reply;
    };

    await state.sync();
    assert.equal(rounds, 3, 'it kept pulling until the desktop said it was done');
  });
});

describe('reading works offline', () => {
  test('cards come from the local mirror, not the network', async () => {
    // Browsing that failed the moment the tailnet dropped would defeat the
    // point of the companion.
    const desktop = new FakeDesktop();
    const { store, state } = await build(desktop);
    const engine = buildAppState(
      store, { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
      'phone-1', testUuid, desktop.fetchImpl,
    );
    void engine;

    await store.applyDelta({ ...SOL, delta: 3 });
    desktop.reachable = false;

    const cards = await state.cards();
    assert.equal(cards.length, 1);
    assert.equal((await state.totals()).cards, 3);
  });

  test('collections list offline too', async () => {
    const desktop = new FakeDesktop();
    const { store, state } = await build(desktop);
    await store.upsertCollection({ collection_uid: 'u-1', name: 'Trade box' });
    desktop.reachable = false;

    const names = (await state.collections()).map((c) => c.name);
    assert.ok(names.includes('Trade box'));
    assert.ok(names.includes('Main Collection'));
  });

  test('searching is local', async () => {
    const desktop = new FakeDesktop();
    const { store, state } = await build(desktop);
    await store.applyDelta({ ...SOL, delta: 1 });
    await store.applyDelta({
      ...SOL, printing_id: 'p-bolt', card_name: 'Lightning Bolt', delta: 1,
    });
    desktop.reachable = false;

    const hits = await state.cards(undefined, 'Bolt');
    assert.equal(hits.length, 1);
  });
});

describe('the analyst needs the desktop', () => {
  test('it fails honestly rather than serving a stale verdict', async () => {
    // The analysis needs the card catalogue and the combo database, neither
    // of which belongs on a phone. A cached answer to a different decklist
    // would be worse than no answer.
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { state } = await build(desktop);

    await assert.rejects(() => state.analyze('1 Sol Ring'), /unreachable/i);
  });
});


describe('writing works offline', () => {
  test('a scanned card is filed with no signal and syncs later', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { state } = await build(desktop);

    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring' });
    assert.equal((await state.totals()).cards, 1, 'visible immediately');

    desktop.reachable = true;
    await state.sync();
    assert.equal(desktop.totalCards(), 1, 'and remembered for the desktop');
  });

  test('a card filed by mistake can be taken back', async () => {
    const desktop = new FakeDesktop();
    const { state } = await build(desktop);
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring' });
    await state.removeCard({ printing_id: 'p-sol', card_name: 'Sol Ring' });
    assert.equal((await state.totals()).cards, 0);

    await state.sync();
    assert.equal(desktop.totalCards(), 0, 'the desktop agrees');
  });

  test('a collection made offline reaches the desktop', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { state } = await build(desktop);

    const uid = await state.newCollection('Shop pickups');
    await state.addCard({
      printing_id: 'p-sol', card_name: 'Sol Ring', collection_uid: uid,
    });

    desktop.reachable = true;
    await state.sync();
    assert.equal(desktop.collections.get(uid), 'Shop pickups');
  });

  test('unsent edits are counted for the user', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { state } = await build(desktop);

    let latest = null;
    state.subscribe((s) => { latest = s; });
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring' });
    assert.equal(latest.pendingEdits, 1,
                 'the screen can say how much is waiting');
  });

  test('several copies of one card', async () => {
    const desktop = new FakeDesktop();
    const { state } = await build(desktop);
    await state.addCard({ printing_id: 'p-sol', card_name: 'Sol Ring',
                          quantity: 4 });
    assert.equal((await state.totals()).cards, 4);
  });
});
