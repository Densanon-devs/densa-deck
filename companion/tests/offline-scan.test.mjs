/**
 * Scanning a box with no PC in reach.
 *
 * Identification used to live entirely on the desktop, so out of range the
 * scanner threw and the photo was discarded — every card scanned in a garage
 * was lost work, which makes the feature useless exactly where it is needed.
 *
 * Two things fix it, and both are exercised here. The phone pulls the index
 * of every printing off the PC (four fields, a few megabytes — never bundled
 * into the build, because it changes with every set) and matches against it
 * on the device. Whatever it cannot place EXACTLY is kept as a photo and
 * handed to the PC later.
 *
 * The contract: nothing is lost, nothing is filed that was not certain, and
 * cards land in the lists they were scanned into rather than whichever list
 * happens to be open when the queue drains.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore, DEFAULT_COLLECTION_UID } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';

const SET = 'aaaaaaaa-0000-4000-8000-000000000001';
const DECK = 'bbbbbbbb-0000-4000-8000-000000000002';

/** The index page the PC serves, as [id, name, set, number]. */
const INDEX = [
  ['p-sol', 'Sol Ring', 'cmm', '410'],
  ['p-bolt', 'Lightning Bolt', 'lea', '161'],
];

/** A desktop that serves the index and identifies photos. */
function serving(desktop, { certain = true, candidates = 1, index = INDEX } = {}) {
  const inner = desktop.handle.bind(desktop);
  desktop.handle = (route, payload) => {
    if (route === 'catalogue/page') {
      const after = payload.after ?? '';
      const rows = index.filter((r) => r[0] > after);
      return { rows, next: '', total: index.length };
    }
    if (route === 'capture') {
      const found = [
        { printing_id: 'p-sol', name: 'Sol Ring', finishes: ['nonfoil'] },
        { printing_id: 'p-sol2', name: 'Sol Ring', finishes: ['nonfoil'] },
      ].slice(0, candidates);
      return {
        auto_addable: certain && candidates === 1,
        candidates: found,
        capture: { text: 'Sol Ring', card_detected: true },
      };
    }
    return inner(route, payload);
  };
  return desktop;
}

/** A reader that returns whatever text the test wants off the "card". */
const readerOf = (text) => ({ async read() { return text; } });

async function makePhone(desktop, reader = readerOf('')) {
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const state = buildAppState(
    store,
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1',
    testUuid,
    desktop.fetchImpl,
    new DeckStore(db),
    reader,
  );
  return { store, state };
}

beforeEach(() => resetUuid());

describe('getting the index off the PC', () => {
  test('a pull fills the phone index', async () => {
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);

    await state.syncCatalogue();
    assert.equal(await store.catalogueSize(), 2);
    assert.deepEqual(await state.catalogueReady(), { rows: 2, ready: true });
  });

  test('a phone that has never pulled is not ready', async () => {
    const { state } = await makePhone(serving(new FakeDesktop()));
    assert.deepEqual(await state.catalogueReady(), { rows: 0, ready: false });
  });

  test('pulling twice does not double the index', async () => {
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await state.syncCatalogue();
    await state.syncCatalogue();
    assert.equal(await store.catalogueSize(), 2);
  });

  test('the exact key is what it is indexed for', async () => {
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await state.syncCatalogue();

    const hit = await store.printingByKey('cmm', '410');
    assert.equal(hit.name, 'Sol Ring');
    assert.equal(await store.printingByKey('cmm', '999'), null);
  });
});

describe('identifying with no PC', () => {
  const FOOTER = 'Sol Ring\n0410/0500 U\nCMM • EN';

  test('a card the index holds is identified on the phone', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop, readerOf(FOOTER));
    await state.syncCatalogue();
    desktop.reachable = false;

    const out = await state.identifyOffline('file:///card.jpg');
    assert.equal(out.printing.printing_id, 'p-sol');
  });

  test('and it can be filed, offline, into the lists it was scanned into',
    async () => {
      const desktop = serving(new FakeDesktop());
      const { state } = await makePhone(desktop, readerOf(FOOTER));
      await state.syncCatalogue();
      desktop.reachable = false;

      const out = await state.identifyOffline('file:///card.jpg');
      await state.addCard({
        printing_id: out.printing.printing_id,
        card_name: out.printing.name,
        collection_uid: DEFAULT_COLLECTION_UID,
        also_collection_uids: [SET, DECK],
      });

      const [stack] = await state.cards();
      assert.equal(stack.card_name, 'Sol Ring');
      const lists = await state.listsFor(stack.stack_key);
      assert.ok(lists.includes(SET) && lists.includes(DECK), lists);
    });

  test('without the index it refuses rather than guessing', async () => {
    // The index is the whole basis for an offline answer. Without it the
    // only honest reply is "ask the PC".
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop, readerOf(FOOTER));
    desktop.reachable = false;
    assert.equal(await state.identifyOffline('file:///card.jpg'), null);
  });

  test('a half-pulled index is treated as no index', async () => {
    // The missing rows are exactly the cards it would silently fail on, and
    // "scanned it, nothing found" reads as a bad photo rather than a
    // half-finished download.
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop, readerOf(FOOTER));
    await store.putCatalogue([INDEX[0]]);
    await store.setMeta('catalogue.cursor', 'p-sol');
    assert.equal((await state.catalogueReady()).ready, false);
    assert.equal(await state.identifyOffline('file:///card.jpg'), null);
  });

  test('a footer with no name above it is not filed unasked', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop, readerOf('0410/0500 U\nCMM • EN'));
    await state.syncCatalogue();
    assert.equal(await state.identifyOffline('file:///card.jpg'), null);
  });

  test('an unreadable picture is not filed', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop, readerOf(''));
    await state.syncCatalogue();
    assert.equal(await state.identifyOffline('file:///card.jpg'), null);
  });

  test('a card the index does not hold is not filed', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(
      desktop, readerOf('Black Lotus\n0233/0295 R\nLEB • EN'));
    await state.syncCatalogue();
    assert.equal(await state.identifyOffline('file:///card.jpg'), null);
  });
});

describe('a photo it could not place', () => {
  test('is kept rather than thrown away', async () => {
    const desktop = new FakeDesktop();
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);
    assert.equal(await state.queuedScans(), 1);
    assert.deepEqual(await state.cards(), [], 'filed something unread');
  });

  test('and several queue in the order they were scanned', async () => {
    const desktop = new FakeDesktop();
    const { store, state } = await makePhone(desktop);
    for (const n of ['a', 'b', 'c']) {
      await state.queueScan(`data:image/jpeg;base64,${n}`, DEFAULT_COLLECTION_UID);
    }
    const queued = await store.pendingScans();
    assert.deepEqual(queued.map((q) => q.image.slice(-1)), ['a', 'b', 'c']);
  });
});

describe('draining the queue when the PC is back', () => {
  test('a card the PC is certain of is filed', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);

    const out = await state.drainScans();
    assert.equal(out.filed, 1);
    assert.equal(await state.queuedScans(), 0);
    assert.equal((await state.cards())[0].card_name, 'Sol Ring');
  });

  test('it lands in the lists it was SCANNED into, not the current ones',
    async () => {
      // By the time this drains you have moved on to another box. Reading
      // the selection at drain time would file a shoebox of commons into
      // whatever binder happens to be open.
      const desktop = serving(new FakeDesktop());
      const { state } = await makePhone(desktop);
      await state.queueScan('data:image/jpeg;base64,AAAA',
        DEFAULT_COLLECTION_UID, [SET, DECK]);

      await state.drainScans();
      const [stack] = await state.cards();
      const lists = await state.listsFor(stack.stack_key);
      assert.ok(lists.includes(SET) && lists.includes(DECK), lists);
    });

  test('an uncertain one is NOT filed, and is kept for a human', async () => {
    const desktop = serving(new FakeDesktop(), { candidates: 2 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);

    const out = await state.drainScans();
    assert.equal(out.filed, 0);
    assert.equal(out.undecided, 1);
    assert.deepEqual(await state.cards(), []);
    assert.equal(await state.queuedScans(), 1, 'it was dropped unfiled');
  });

  test('an unreadable one is kept too, and counted separately', async () => {
    const desktop = serving(new FakeDesktop(), { candidates: 0 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);

    const out = await state.drainScans();
    assert.equal(out.failed, 1);
    assert.equal(await state.queuedScans(), 1);
  });

  test('the PC going away mid-drain leaves the rest safe', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    for (const n of ['a', 'b', 'c']) {
      await state.queueScan(`data:image/jpeg;base64,${n}`, DEFAULT_COLLECTION_UID);
    }
    let seen = 0;
    const inner = desktop.handle.bind(desktop);
    desktop.handle = (route, payload) => {
      if (route === 'capture' && seen++ >= 1) throw new Error('gone');
      return inner(route, payload);
    };

    const out = await state.drainScans();
    assert.equal(out.filed, 1);
    assert.equal(await state.queuedScans(), 2, 'the rest were lost');
    // And it STOPS, rather than throwing the rest of the box at a machine
    // that is not there. On a queue of three that is a detail; on a queue of
    // three hundred it is three hundred timeouts in a row.
    assert.equal(seen, 2, `kept trying: ${seen} attempts after it went away`);
  });

  test('draining an empty queue does nothing and says so', async () => {
    const { state } = await makePhone(serving(new FakeDesktop()));
    assert.deepEqual(await state.drainScans(),
      { filed: 0, undecided: 0, failed: 0, repeats: 0 });
  });
});

describe('deciding on one by hand', () => {
  test('picking a candidate files it and clears the photo', async () => {
    const desktop = serving(new FakeDesktop(), { candidates: 2 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA',
      DEFAULT_COLLECTION_UID, [SET]);

    const next = await state.reviewNextScan();
    assert.equal(next.reply.candidates.length, 2);
    await state.fileQueuedScan(next.scanUid, next.reply.candidates[1], 'nonfoil');

    assert.equal(await state.queuedScans(), 0);
    const [stack] = await state.cards();
    assert.equal(stack.printing_id, 'p-sol2');
    assert.ok((await state.listsFor(stack.stack_key)).includes(SET));
  });

  test('discarding one files nothing', async () => {
    const desktop = serving(new FakeDesktop(), { candidates: 2 });
    const { state } = await makePhone(desktop);
    await state.queueScan('data:image/jpeg;base64,AAAA', DEFAULT_COLLECTION_UID);

    const next = await state.reviewNextScan();
    await state.discardQueuedScan(next.scanUid);
    assert.equal(await state.queuedScans(), 0);
    assert.deepEqual(await state.cards(), []);
  });

  test('there is nothing to review when the queue is empty', async () => {
    const { state } = await makePhone(serving(new FakeDesktop()));
    assert.equal(await state.reviewNextScan(), null);
  });
});

describe('one card scanned five times is one card', () => {
  /**
   * The failure this exists to stop, in the user's words: a card queued
   * offline shows nothing obvious happening, so you photograph it again —
   * and again — and on reconnect all five file as five separate copies.
   *
   * Live, `RepeatGuard` already answers this: the same name inside four
   * seconds is the same card still in frame. The queue has to answer it the
   * same way, using the capture times it stored, or the two halves of the
   * app disagree about what a duplicate is.
   */
  async function queueAt(state, times) {
    let n = 0;
    for (const at of times) {
      await state.queueScan(`data:image/jpeg;base64,${n++}`,
        DEFAULT_COLLECTION_UID);
    }
    // Restamp with the capture times the test is about.
    const rows = await state.store?.pendingScans?.();
    return rows;
  }

  /** Queue photos with explicit capture times, oldest first. */
  async function queuedWithTimes(store, times) {
    let n = 0;
    for (const captured_at of times) {
      await store.queueScan({
        scan_uid: `s-${n}`,
        image: `data:image/jpeg;base64,${n}`,
        captured_at,
        collection_uid: DEFAULT_COLLECTION_UID,
        also_uids: [],
      });
      n += 1;
    }
  }

  test('five frantic attempts at one card file ONE copy', async () => {
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await queuedWithTimes(store, [
      '2026-08-30T10:00:00.000Z',
      '2026-08-30T10:00:00.800Z',
      '2026-08-30T10:00:01.600Z',
      '2026-08-30T10:00:02.400Z',
      '2026-08-30T10:00:03.200Z',
    ]);

    const out = await state.drainScans();
    assert.equal(out.filed, 1, `filed ${out.filed}`);
    assert.equal(out.repeats, 4);
    const [stack] = await state.cards();
    assert.equal(stack.quantity, 1, `owned ${stack.quantity} of one card`);
  });

  test('and the extra photos are cleared, not left to file next time',
    async () => {
      const desktop = serving(new FakeDesktop());
      const { store, state } = await makePhone(desktop);
      await queuedWithTimes(store, [
        '2026-08-30T10:00:00.000Z', '2026-08-30T10:00:01.000Z',
      ]);
      await state.drainScans();
      assert.equal(await state.queuedScans(), 0);
      // Draining again must not resurrect them.
      await state.drainScans();
      assert.equal((await state.cards())[0].quantity, 1);
    });

  test('but four copies scanned deliberately are still four cards', async () => {
    // The whole risk of a dedup: somebody filing a playset must get four.
    // Spaced past the hold, exactly as the live scanner requires.
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await queuedWithTimes(store, [
      '2026-08-30T10:00:00.000Z',
      '2026-08-30T10:00:05.000Z',
      '2026-08-30T10:00:10.000Z',
      '2026-08-30T10:00:15.000Z',
    ]);

    const out = await state.drainScans();
    assert.equal(out.filed, 4, `filed ${out.filed}`);
    assert.equal(out.repeats, 0);
    assert.equal((await state.cards())[0].quantity, 4);
  });

  test('the guard reads CAPTURE times, not drain times', async () => {
    // Drained back to back every photo is milliseconds from the last, so a
    // guard fed the clock would collapse a whole box into one card.
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await queuedWithTimes(store, [
      '2026-08-30T10:00:00.000Z',
      '2026-08-30T10:01:00.000Z',
      '2026-08-30T10:02:00.000Z',
    ]);
    const out = await state.drainScans();
    assert.equal(out.filed, 3, 'a minute apart is three separate cards');
  });

  test('a photo with an unreadable timestamp is still filed', async () => {
    // Refusing it would lose a real card over a bad clock.
    //
    // The guard tolerates the NaN by construction — an unparseable time
    // fails every comparison, so the photo files and the next real
    // timestamp restores the window. Passing 0 instead is belt and braces,
    // not load-bearing, and no test here pretends otherwise.
    const desktop = serving(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await store.queueScan({
      scan_uid: 's-x', image: 'data:image/jpeg;base64,x',
      captured_at: 'not a date', collection_uid: DEFAULT_COLLECTION_UID,
      also_uids: [],
    });
    assert.equal((await state.drainScans()).filed, 1);
  });

});
