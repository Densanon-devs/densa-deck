/**
 * The phone's sync engine, under the conditions it will actually meet.
 *
 * A shop with no signal, a desktop that was asleep, a push whose reply was
 * lost, a desktop that got reinstalled. Each of these has a way of quietly
 * destroying an inventory, and each is a test here.
 */

import { strict as assert } from 'node:assert';
import { test, beforeEach, describe } from 'node:test';

import { DesktopClient } from '../src/lib/client.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from '../src/lib/store.ts';
import { SyncEngine } from '../src/lib/sync.ts';
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

async function makePhone(desktop, { device = 'phone-1' } = {}) {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  const client = new DesktopClient(
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    { fetchImpl: desktop.fetchImpl },
  );
  return { store, engine: new SyncEngine(store, client, device, testUuid) };
}

beforeEach(() => resetUuid());

describe('editing while offline', () => {
  test('an edit survives having nowhere to send it', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { store, engine } = await makePhone(desktop);

    await engine.editQuantity({ ...SOL, delta: 4 });

    assert.equal(await store.totalCards(), 4, 'the phone knows what it owns');
    assert.equal(await engine.pending(), 1, 'and remembers to say so');

    const outcome = await engine.sync();
    assert.equal(outcome.offline, true);
    assert.equal(await store.totalCards(), 4, 'nothing is lost by failing to sync');
  });

  test('being offline is not an error worth shouting about', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { engine } = await makePhone(desktop);
    const outcome = await engine.sync();
    assert.equal(outcome.offline, true);
    assert.equal(outcome.error, undefined);
  });

  test('a week of edits all reach the desktop at once', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { engine } = await makePhone(desktop);

    for (let i = 0; i < 12; i += 1) {
      await engine.editQuantity({ ...SOL, printing_id: `p-${i}`, delta: 1 });
    }
    assert.equal(await engine.pending(), 12);

    desktop.reachable = true;
    const outcome = await engine.sync();
    assert.equal(outcome.ok, true);
    assert.equal(desktop.totalCards(), 12);
    assert.equal(await engine.pending(), 0);
  });
});

describe('both sides edited apart', () => {
  test('neither side loses its cards', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);

    // The phone, at a shop.
    await engine.editQuantity({ ...SOL, delta: 2 });
    // The desktop, at home, while the phone was away.
    desktop.edit({ ...SOL, delta: 3 });

    await engine.sync();

    assert.equal(desktop.totalCards(), 5, 'desktop has both lots');
    assert.equal(await store.totalCards(), 5, 'and so does the phone');
  });

  test('a removal made on the phone reaches the desktop', async () => {
    const desktop = new FakeDesktop();
    const { engine } = await makePhone(desktop);

    await engine.editQuantity({ ...SOL, delta: 4 });
    await engine.sync();
    await engine.editQuantity({ ...SOL, delta: -1 }); // sold one
    await engine.sync();

    assert.equal(desktop.totalCards(), 3);
  });
});

describe('unreliable delivery', () => {
  test('a push whose reply was lost is sent again, not double-counted', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, delta: 3 });

    // The desktop applies the push, then the response never arrives.
    const realFetch = desktop.fetchImpl;
    let swallowed = false;
    const flaky = async (url, init) => {
      const response = await realFetch(url, init);
      if (String(url).includes('sync/push') && !swallowed) {
        swallowed = true;
        await response.json();          // the desktop HAS applied it
        throw new Error('connection dropped');
      }
      return response;
    };
    const client = new DesktopClient(
      { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
      { fetchImpl: flaky },
    );
    const engine2 = new SyncEngine(store, client, 'phone-1', testUuid);

    await engine2.sync();            // fails partway
    await engine2.sync();            // tries again

    assert.equal(desktop.totalCards(), 3, 'three cards, not six');
  });

  test('syncing repeatedly changes nothing', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, delta: 2 });

    for (let i = 0; i < 4; i += 1) await engine.sync();

    assert.equal(desktop.totalCards(), 2);
    assert.equal(await store.totalCards(), 2);
  });

  test('an acknowledged event is kept, not deleted', async () => {
    // It is the only record of what this device did, and a desktop that gets
    // restored from an older backup needs to be told again.
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    const event = await engine.editQuantity({ ...SOL, delta: 1 });
    await engine.sync();
    assert.equal(await store.knowsEvent(event.event_uid), true);
  });
});

describe('the desktop changed', () => {
  test('a different desktop resets the cursor rather than resuming', async () => {
    // Resuming from a watermark that points into somebody else's history
    // would silently skip everything the new desktop has.
    const desktop = new FakeDesktop({ device: 'desktop-1' });
    const { store, engine } = await makePhone(desktop);
    await engine.sync();
    await store.setMeta('sync.cursor', '99');

    desktop.device = 'desktop-2';
    desktop.edit({ ...SOL, delta: 7 });
    await engine.sync();

    assert.equal(await store.totalCards(), 7, 'it pulled from the start');
  });

  test('being unpaired is reported, not retried forever', async () => {
    const desktop = new FakeDesktop();
    const { engine } = await makePhone(desktop);
    desktop.paired = false;
    const outcome = await engine.sync();
    assert.equal(outcome.unpaired, true);
    assert.match(outcome.error, /QR/);
  });
});

describe('collections', () => {
  test('one made offline arrives with its cards', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { engine } = await makePhone(desktop);

    const uid = await engine.createCollection('Trade box');
    await engine.editQuantity({ ...SOL, collection_uid: uid, delta: 3 });

    desktop.reachable = true;
    await engine.sync();

    assert.equal(desktop.collections.get(uid), 'Trade box');
    assert.equal(desktop.totalCards(), 3);
  });

  test('deleting the grouping keeps the cards', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    const uid = await engine.createCollection('Trade box');
    await engine.editQuantity({ ...SOL, collection_uid: uid, delta: 3 });

    await engine.deleteCollection(uid, false);

    assert.equal(await store.totalCards(), 3, 'cards survive');
    assert.equal(await store.cardsIn(DEFAULT_COLLECTION_UID), 3, 'now unfiled');
  });

  test('discarding is possible but never the default', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    const uid = await engine.createCollection('Sold lot');
    await engine.editQuantity({ ...SOL, collection_uid: uid, delta: 3 });

    await engine.deleteCollection(uid, true);
    assert.equal(await store.totalCards(), 0);
  });

  test('both devices agree on what unfiled means', async () => {
    // A random uid per device gave each its own unfiled pile, so removals
    // made on one landed in a collection the other did not have.
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, delta: 1 });
    await engine.sync();

    const key = [...desktop.stacks.keys()][0];
    assert.ok(
      key.includes(DEFAULT_COLLECTION_UID),
      'the phone filed into the shared default, not one of its own',
    );
    assert.equal(await store.cardsIn(DEFAULT_COLLECTION_UID), 1);
  });
});

describe('push happens before pull', () => {
  test('local additions are sent before remote deletes are applied', async () => {
    // Pulling first could apply a delete for a collection the phone has just
    // filled, and only then send the additions — which would arrive addressed
    // to something neither side still has.
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    const uid = await engine.createCollection('Trade box');
    await engine.editQuantity({ ...SOL, collection_uid: uid, delta: 5 });

    desktop.edit({ collection_uid: uid, discard_cards: false },
                 'collection-delete');

    await engine.sync();

    // The desktop learned about the cards; the grouping went away on both
    // sides, and the cards became unfiled rather than vanishing.
    assert.equal(desktop.totalCards(), 5);
    assert.equal(await store.totalCards(), 5);
  });
});

describe('the mirror', () => {
  test('a stack that reaches zero is removed, not kept at zero', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, delta: 2 });
    await engine.editQuantity({ ...SOL, delta: -2 });
    const stacks = await store.listStacks();
    assert.equal(stacks.length, 0);
  });

  test('removing from a stack that is not there is a no-op', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, delta: -3 });
    assert.equal(await store.totalCards(), 0, 'not minus three');
  });

  test('finishes are different stacks', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, finish: 'foil', delta: 1 });
    await engine.editQuantity({ ...SOL, finish: 'nonfoil', delta: 1 });
    const stacks = await store.listStacks();
    assert.equal(stacks.length, 2);
    assert.equal(await store.totalCards(), 2);
  });

  test('searching by name', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop);
    await engine.editQuantity({ ...SOL, delta: 1 });
    await engine.editQuantity({
      ...SOL, printing_id: 'p-bolt', card_name: 'Lightning Bolt', delta: 1,
    });
    const hits = await store.listStacks(undefined, 'Bolt');
    assert.equal(hits.length, 1);
    assert.equal(hits[0].card_name, 'Lightning Bolt');
  });
});

describe('a mirror that has drifted, and cannot fix itself', () => {
  /**
   * A pulled event is remembered by uid so it is never applied twice. That is
   * right — until one is recorded and NOT applied, after which the phone
   * skips it on every future sync and the cards it described can never
   * arrive. Pulling to refresh forever cannot help.
   *
   * Which is not hypothetical: recording used to happen BEFORE applying, so
   * anything that interrupted the app in between — a force-quit, which is
   * what someone does to a sync that looks stuck — left exactly that state.
   */
  test('an event recorded but never applied leaves the card gone for good', async () => {
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();

    // The shape a crash leaves behind: known, never applied.
    await store.recordEvent({
      event_uid: 'baseline-1', device: 'pc', seq: 1, kind: 'stack-set',
      payload: {
        printing_id: 'p1', card_name: 'Sol Ring', oracle_id: '',
        finish: 'nonfoil', condition: 'NM', language: 'en', location: '',
        collection_uid: DEFAULT_COLLECTION_UID, quantity: 2,
      },
      created_at: 'now',
    });
    assert.equal(await store.knowsEvent('baseline-1'), true);
    assert.equal(await store.totalCards(), 0, 'recorded, never applied');
  });

  test('forgetting the desktop lets the whole lot arrive again', async () => {
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    await store.recordEvent({
      event_uid: 'baseline-1', device: 'pc', seq: 1, kind: 'stack-set',
      payload: { printing_id: 'p1', card_name: 'Sol Ring' },
      created_at: 'now',
    });
    await store.setMeta('sync.cursor', '42');

    await store.forgetDesktopState('phone-a');

    assert.equal(await store.knowsEvent('baseline-1'), false,
                 'the desktop event is forgettable again');
    assert.equal(await store.getMeta('sync.cursor'), undefined,
                 'and the cursor is back to nothing, which asks for a baseline');
  });

  test('it keeps this phone’s own edits, sent or not', async () => {
    // Throwing away unsent work to fix a display problem turns a confusing
    // screen into lost cards.
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    await store.recordEvent({
      event_uid: 'mine-1', device: 'phone-a', seq: 1, kind: 'stack-delta',
      payload: { printing_id: 'p1', card_name: 'Sol Ring', delta: -1 },
      created_at: 'now',
    });
    await store.recordEvent({
      event_uid: 'theirs-1', device: 'pc', seq: 1, kind: 'stack-set',
      payload: { printing_id: 'p2', card_name: 'Bolt' },
      created_at: 'now',
    });

    await store.forgetDesktopState('phone-a');

    assert.equal(await store.knowsEvent('mine-1'), true, 'my edit survives');
    assert.equal(await store.knowsEvent('theirs-1'), false, 'theirs does not');
    assert.equal((await store.unpushed(50)).length, 1,
                 'and it is still queued for the PC');
  });

  test('the local mirror is emptied so the baseline is not doubled', async () => {
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    await store.applyDelta({
      printing_id: 'p1', card_name: 'Sol Ring', oracle_id: '',
      finish: 'nonfoil', condition: 'NM', language: 'en', location: '',
      collection_uid: DEFAULT_COLLECTION_UID, delta: 3, reason: 'test',
    });
    assert.equal(await store.totalCards(), 3);

    await store.forgetDesktopState('phone-a');
    assert.equal(await store.totalCards(), 0);
  });
});
