/**
 * The scan queue: what gets sent, what waits for a person, and what may be
 * queued at all.
 *
 * From an audit of dead ends:
 *   - a photo the PC could not decide was re-uploaded on every drain, for
 *     ever, and nothing on screen could answer it (reviewNextScan and
 *     friends existed with no caller);
 *   - a photo taken in TAG mode, or while scanning into a deck, was queued
 *     and later filed as a NEW copy into the collection -- the one thing tag
 *     mode promises not to do, and the deck never got the card.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import {
  LocalStore, DEFAULT_COLLECTION_UID, SCAN_UNDECIDED, SCAN_UNREADABLE,
  scanNeedsYou, scanToSend,
} from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';
import { canQueue, notQueuedLine, planFor } from '../src/lib/scan-target.ts';

/** A PC whose `capture` answer the test controls, counting every call. */
function pc({ candidates = 2, certain = false } = {}) {
  const desktop = new FakeDesktop();
  const inner = desktop.handle.bind(desktop);
  desktop.captures = 0;
  desktop.answer = { candidates, certain };
  desktop.handle = (route, payload) => {
    if (route === 'capture') {
      desktop.captures += 1;
      const n = desktop.answer.candidates;
      const found = [
        { printing_id: 'p-sol', name: 'Sol Ring', finishes: ['nonfoil'] },
        { printing_id: 'p-sol2', name: 'Sol Ring', finishes: ['nonfoil'] },
      ].slice(0, n);
      return {
        auto_addable: desktop.answer.certain && n === 1,
        candidates: found,
        capture: { text: 'Sol Ring', card_detected: true },
      };
    }
    return inner(route, payload);
  };
  return desktop;
}

async function makePhone(desktop) {
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const state = buildAppState(
    store, { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1', testUuid, desktop.fetchImpl, new DeckStore(db));
  return { store, state };
}

beforeEach(() => resetUuid());

describe('what the drain sends', () => {
  test('a photo the PC could not decide is not re-sent on the next drain', async () => {
    const desktop = pc({ candidates: 2 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);

    assert.equal((await state.drainScans()).undecided, 1);
    assert.equal(desktop.captures, 1);

    const again = await state.drainScans();
    assert.equal(desktop.captures, 1, 'the stuck photo was uploaded again');
    assert.deepEqual(again, { filed: 0, undecided: 0, failed: 0, repeats: 0 });
  });

  test('nor is one it could not read', async () => {
    const desktop = pc({ candidates: 0 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);
    await state.drainScans();
    await state.drainScans();
    assert.equal(desktop.captures, 1);
  });

  test('new photos behind a stuck one still go', async () => {
    const desktop = pc({ candidates: 2 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);
    await state.drainScans();

    desktop.answer = { candidates: 1, certain: true };
    await state.queueScan('data:image/jpeg;base64,BBBB', DEFAULT_COLLECTION_UID);
    const out = await state.drainScans();
    assert.equal(out.filed, 1);
    assert.equal(desktop.captures, 2);
  });
});

describe('counting the queue in its two halves', () => {
  test('waiting for the PC and waiting for you are told apart', async () => {
    const desktop = pc({ candidates: 2 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);
    assert.deepEqual(await state.scanQueueCounts(), { waiting: 1, stuck: 0 });

    await state.drainScans();
    assert.deepEqual(await state.scanQueueCounts(), { waiting: 0, stuck: 1 });
  });

  test('the rule itself', () => {
    assert.equal(scanNeedsYou({ note: '' }), false);
    assert.equal(scanNeedsYou({ note: SCAN_UNDECIDED }), true);
    assert.equal(scanNeedsYou({ note: SCAN_UNREADABLE }), true);
    assert.equal(scanToSend({ note: '' }), true);
    assert.equal(scanToSend({}), true);
  });
});

describe('deciding a stuck photo by hand', () => {
  test('the stuck one comes up first, with its picture and why', async () => {
    const desktop = pc({ candidates: 2 });
    const { state, store } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,STUCK', DEFAULT_COLLECTION_UID);
    await state.drainScans();
    // A photo the PC has not seen yet, queued after it -- but captured_at
    // order would put an OLDER untried photo first, so stamp this one
    // earlier to prove the stuck one wins on need, not on age.
    await state.queueScan('data:image/jpeg;base64,FRESH', DEFAULT_COLLECTION_UID);
    const rows = await store.pendingScans();
    const fresh = rows.find((r) => r.image.includes('FRESH'));
    await store.db.run('UPDATE pending_scans SET captured_at = ? WHERE scan_uid = ?',
      ['1999-01-01T00:00:00.000Z', fresh.scan_uid]);

    const next = await state.reviewNextScan();
    assert.match(next.image, /STUCK/);
    assert.equal(next.note, SCAN_UNDECIDED);
    assert.equal(next.reply.candidates.length, 2);

    await state.fileQueuedScan(next.scanUid, next.reply.candidates[1], 'nonfoil');
    // The stuck one is settled; the fresh one is still waiting for the PC.
    assert.deepEqual(await state.scanQueueCounts(), { waiting: 1, stuck: 0 });
    assert.equal((await state.cards())[0].printing_id, 'p-sol2');
  });

  test('an unreadable one can be looked at and discarded with no PC', async () => {
    const desktop = pc({ candidates: 0 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,BLUR', DEFAULT_COLLECTION_UID);
    await state.drainScans();

    desktop.reachable = false;
    const next = await state.reviewNextScan();
    assert.equal(next.reply, null, 'it asked the PC again for a known answer');
    assert.equal(next.note, SCAN_UNREADABLE);

    await state.discardQueuedScan(next.scanUid);
    assert.equal(await state.queuedScans(), 0);
    assert.deepEqual(await state.cards(), []);
  });
});

describe('what may be queued at all', () => {
  test('a plain add may: the queue adds a copy, which is what was asked', () => {
    assert.equal(canQueue(planFor('add', false)), true);
  });

  test('tag mode may not: draining would file a NEW copy of an owned card', () => {
    assert.equal(canQueue(planFor('tag', false)), false);
    assert.match(notQueuedLine(planFor('tag', false)), /nothing was changed/);
  });

  test('scanning into a deck may not: the deck would never get the card', () => {
    assert.equal(canQueue(planFor('add', true)), false);
    assert.equal(canQueue(planFor('tag', true)), false);
    assert.match(notQueuedLine(planFor('add', true)), /wasn't added to the deck/);
  });
});
