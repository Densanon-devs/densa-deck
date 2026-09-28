/**
 * Two seams where the phone and the desktop disagreed about what an event
 * meant (audit, 2026-09-28).
 *
 * 1. A membership from the desktop carries the LIST's uid as
 *    `collection_uid`. The phone fed that to stackKey(), which uses it as
 *    the collection the stack is FILED in -- a key no stack had, so every
 *    group the desktop set up was missing on the phone. The old test called
 *    store.addMembership() directly and never went through the applier;
 *    these go through a real sync, against a real SQLite.
 * 2. Decks lost zones and printings both ways (see sync/apply.py).
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { FakeDesktop, testUuid, resetUuid, MemoryDatabase } from './harness.mjs';
import { RealDatabase } from './real-sqlite.mjs';
import { LocalStore } from '../src/lib/store.ts';
import { SyncEngine, arraysFromDesktop, hasPhoneArrays } from '../src/lib/sync.ts';
import { DeckStore } from '../src/lib/decks.ts';
import { DesktopClient } from '../src/lib/client.ts';
import { stackKey } from '../src/lib/protocol.ts';

async function makePhone(desktop, db) {
  const store = new LocalStore(db);
  await store.init();
  const decks = new DeckStore(db);
  const client = new DesktopClient(
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    { fetchImpl: desktop.fetchImpl },
  );
  return { store, decks, engine: new SyncEngine(store, client, 'phone-1', testUuid, decks) };
}

let seq = 0;
function event(kind, payload) {
  seq += 1;
  return {
    event_uid: `pc-${seq}`, device: 'pc-1', seq, kind,
    created_at: `2026-01-01T00:00:${String(seq).padStart(2, '0')}Z`, payload,
  };
}

beforeEach(() => { resetUuid(); seq = 0; });

describe('a group the desktop set up reaches the phone', () => {
  test('a card filed in Main and tagged into a list shows in that list', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop, new RealDatabase());
    desktop.events.push(
      event('collection-upsert', { collection_uid: 'main', name: 'Main Collection' }),
      event('collection-upsert', { collection_uid: 'edh', name: 'EDH staples', kind: 'group' }),
      event('stack-delta', {
        printing_id: 'p-sol', card_name: 'Sol Ring', collection_uid: 'main',
        finish: 'nonfoil', condition: 'NM', language: 'en', location: '', delta: 2,
      }),
      // Exactly the shape sync/apply.py membership_event writes.
      event('membership', {
        printing_id: 'p-sol', card_name: 'Sol Ring', collection_uid: 'edh',
        finish: 'nonfoil', condition: 'NM', language: 'en', location: '', member: true,
      }),
    );

    await engine.sync();

    const inList = await store.listStacks('edh');
    assert.deepEqual(inList.map((s) => s.card_name), ['Sol Ring'],
      'the membership landed under a key no stack has');
    // Recomputed, not read back: node:sqlite returns TEXT only up to the
    // first NUL, and stack keys are NUL-joined (see the note in sync.ts).
    const [stack] = await store.listStacks('main');
    const key = stackKey({ ...stack, collection_uid: stack.collection_uid });
    assert.ok((await store.membershipsFor(key)).includes('edh'));
  });

  test('and leaving the list takes it back out', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop, new RealDatabase());
    const base = { printing_id: 'p-sol', card_name: 'Sol Ring', finish: 'nonfoil',
                   condition: 'NM', language: 'en', location: '' };
    desktop.events.push(
      event('stack-delta', { ...base, collection_uid: 'main', delta: 1 }),
      event('membership', { ...base, collection_uid: 'edh', member: true }),
      event('membership', { ...base, collection_uid: 'edh', member: false }),
    );
    await engine.sync();
    assert.deepEqual(await store.listStacks('edh'), []);
  });

  test('a different finish is a different stack and is not tagged', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop, new RealDatabase());
    desktop.events.push(
      event('stack-delta', { printing_id: 'p-sol', card_name: 'Sol Ring',
        collection_uid: 'main', finish: 'foil', condition: 'NM', language: 'en',
        location: '', delta: 1 }),
      event('membership', { printing_id: 'p-sol', card_name: 'Sol Ring',
        collection_uid: 'edh', finish: 'nonfoil', condition: 'NM', language: 'en',
        location: '', member: true }),
    );
    await engine.sync();
    assert.deepEqual(await store.listStacks('edh'), []);
  });
});

describe('a desktop deck keeps its zones and printings on the phone', () => {
  const OLD_DESKTOP_DECK = {
    deck_id: 'ur', name: 'Izzet', format: 'modern', notes: '',
    decklist: { 'Lightning Bolt': 4, Island: 10, 'Mystical Dispute': 3, Ponder: 1 },
    zones: { mainboard: ['Lightning Bolt', 'Island'],
             sideboard: ['Mystical Dispute'], maybeboard: ['Ponder'] },
    printings: [{ card_name: 'Lightning Bolt', set_code: 'lea',
                  collector_number: '161', quantity: 1, zone: 'mainboard' }],
    updated_at: '2026-01-01T00:00:00Z',
  };

  test('an older desktop (map + zones only) is split into zones, not all main deck', async () => {
    const desktop = new FakeDesktop();
    const { decks, engine } = await makePhone(desktop, new MemoryDatabase());
    desktop.events.push(event('deck-upsert', OLD_DESKTOP_DECK));
    await engine.sync();

    const deck = await decks.get('ur');
    const qty = (list, name) => list.filter((e) => e.name === name)
      .reduce((s, e) => s + e.qty, 0);
    assert.equal(qty(deck.decklist, 'Lightning Bolt'), 4);
    assert.equal(qty(deck.decklist, 'Mystical Dispute'), 0, 'sideboard landed in the main deck');
    assert.equal(qty(deck.sideboard, 'Mystical Dispute'), 3);
    assert.equal(qty(deck.sideboard, 'Ponder'), 1, 'maybeboard is not the main deck');
    const alpha = deck.decklist.find((e) => e.set_code === 'lea');
    assert.ok(alpha, 'the chosen printing was dropped');
    assert.equal(alpha.qty, 1);
  });

  test('the split matches the desktop rule exactly', () => {
    const out = arraysFromDesktop(OLD_DESKTOP_DECK.decklist, OLD_DESKTOP_DECK.zones,
                                  OLD_DESKTOP_DECK.printings);
    const total = (l) => l.reduce((s, e) => s + e.qty, 0);
    assert.equal(total(out.entries) + total(out.sideboard) + total(out.commander), 18);
    assert.equal(hasPhoneArrays({ entries: [], sideboard: [] }), false);
    assert.equal(hasPhoneArrays({ entries: [{ name: 'x', qty: 1 }] }), true);
  });
});

describe('a phone deck keeps its sideboard and printings on the way out', () => {
  test('the event carries the sideboard in the map and the printings', async () => {
    const desktop = new FakeDesktop();
    const { store, engine } = await makePhone(desktop, new MemoryDatabase());
    await engine.recordDeckUpsert({
      deck_id: 'ur', name: 'Izzet', format: 'modern', notes: '',
      decklist: [{ name: 'Lightning Bolt', qty: 4, set_code: 'lea', collector_number: '161' }],
      sideboard: [{ name: 'Mystical Dispute', qty: 3 }],
      commander: [],
      updated_at: '2026-01-02T00:00:00Z',
    });
    const [sent] = await store.unpushed();
    assert.equal(sent.payload.decklist['Mystical Dispute'], 3,
      'the sideboard was left out of the map the desktop reads');
    assert.deepEqual(sent.payload.printings, [{
      card_name: 'Lightning Bolt', quantity: 4, zone: 'mainboard',
      set_code: 'lea', collector_number: '161',
    }]);
  });
});
