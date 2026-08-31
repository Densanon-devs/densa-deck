/**
 * The phone with no PC — the whole of it, not the easy parts.
 *
 * The rule: mobile is collection-first and must work completely standalone.
 * These are the operations that quietly did not, and each failed in the one
 * place it is most needed — a wishlist you cannot add to from a shop,
 * groups you cannot rename over the box you are sorting, an overlaps screen
 * that is a pure read of local data and asked the network anyway.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore, DEFAULT_COLLECTION_UID } from '../src/lib/store.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore } from '../src/lib/decks.ts';

const SET = 'aaaaaaaa-0000-4000-8000-000000000001';
const DECK = 'bbbbbbbb-0000-4000-8000-000000000002';

const ORACLE = [
  ['o-sol', 'Sol Ring', 'Artifact', 'Tap: Add two colourless.', '{1}', 1, 'C'],
  ['o-bolt', 'Lightning Bolt', 'Instant', 'Deal 3 damage.', '{R}', 1, 'R'],
];
const INDEX = [
  ['p-sol', 'Sol Ring', 'cmm', '410', 1],
  ['p-bolt', 'Lightning Bolt', 'lea', '161', 1],
];

function serving(desktop) {
  const inner = desktop.handle.bind(desktop);
  desktop.handle = (route, payload) => {
    if (route === 'catalogue/page') {
      return {
        rows: INDEX.filter((r) => r[0] > (payload.after ?? '')),
        next: '', total: INDEX.length,
      };
    }
    if (route === 'oracle/page') {
      return {
        rows: ORACLE.filter((r) => r[0] > (payload.after ?? '')),
        next: '', total: ORACLE.length,
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
    store,
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1',
    testUuid,
    desktop.fetchImpl,
    new DeckStore(db),
  );
  return { store, state };
}

beforeEach(() => resetUuid());

describe('wanting something, from a shop', () => {
  test('a card can be added to the wishlist with no PC', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Black Lotus', 1);
    const wishes = await state.handWishes();
    assert.equal(wishes.length, 1);
    assert.equal(wishes[0].card_name, 'Black Lotus');
  });

  test('naming a printing survives too', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Lightning Bolt', 1,
      { set_code: 'lea', collector_number: '161' });
    const [wish] = await state.handWishes();
    assert.equal(wish.set_code, 'lea');
  });

  test('and it is queued for the PC rather than only kept here', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    const before = await state.pendingCount();
    await state.wishlistAdd('Black Lotus', 1);
    assert.ok(await state.pendingCount() > before,
      'the want never leaves this phone');
  });

  test('taking one off works offline as well', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Black Lotus', 1);
    await state.removeFromWishlist('Black Lotus');
    assert.deepEqual(await state.handWishes(), []);
  });

  test('buying one files it AND clears the want, offline', async () => {
    // The two halves belong together: filing it without clearing the want
    // leaves you shopping for a card already in your bag.
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Sol Ring', 1);
    await state.acquireFromWishlist('p-sol', 'Sol Ring', 1);

    assert.deepEqual(await state.handWishes(), []);
    assert.equal((await state.cards())[0].card_name, 'Sol Ring');
  });

  test('a want for a card you now own drops off by itself', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    desktop.reachable = false;

    await state.wishlistAdd('Sol Ring', 1);
    await state.addCard({
      printing_id: 'p-sol', card_name: 'Sol Ring',
      collection_uid: DEFAULT_COLLECTION_UID,
    });
    assert.deepEqual(await state.handWishes(), [],
      'it still wanted a card in the bag');
  });
});

describe('overlaps are a local read', () => {
  test('cards in two lists are found with no PC', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.addCard({
      printing_id: 'p-sol', card_name: 'Sol Ring',
      collection_uid: DEFAULT_COLLECTION_UID,
      also_collection_uids: [SET, DECK],
    });
    desktop.reachable = false;

    const out = await state.overlaps();
    assert.equal(out.cards.length, 1);
    assert.equal(out.cards[0].collection_count, 3);
  });

  test('and one in a single list is not an overlap', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.addCard({
      printing_id: 'p-sol', card_name: 'Sol Ring',
      collection_uid: DEFAULT_COLLECTION_UID,
    });
    desktop.reachable = false;
    assert.equal((await state.overlaps()).cards.length, 0);
  });

  test('more lists than copies is flagged, which is the point', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.addCard({
      printing_id: 'p-sol', card_name: 'Sol Ring',
      collection_uid: DEFAULT_COLLECTION_UID,
      also_collection_uids: [SET, DECK],
    });
    desktop.reachable = false;

    const out = await state.overlaps();
    assert.equal(out.overcommitted, 1, 'one copy cannot be in three lists');
  });
});

describe('browsing every card, with no PC', () => {
  test('a card can be searched for by name', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.syncOracle();
    await state.syncCatalogue();
    desktop.reachable = false;

    const out = await state.searchCards({ name: 'Sol' });
    assert.equal(out.cards.length, 1);
    assert.equal(out.cards[0].name, 'Sol Ring');
  });

  test('and carries what a deck builder needs', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.syncOracle();
    await state.syncCatalogue();
    desktop.reachable = false;

    const [card] = (await state.searchCards({ name: 'Bolt' })).cards;
    assert.equal(card.type_line, 'Instant');
    assert.equal(card.cmc, 1);
    assert.deepEqual(card.color_identity, ['R']);
    // The art is a Scryfall URL loaded from a printing id, so a card found
    // offline still has a picture the moment there is any network.
    assert.ok(card.printing_id, 'no printing to fetch art by');
  });

  test('its rules text can be read', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.syncOracle();
    desktop.reachable = false;

    const detail = await state.cardDetail('p-sol', 'Sol Ring');
    assert.match(detail.oracle_text, /Add two colourless/);
  });

  test('a card the index has never held says so rather than inventing one',
    async () => {
      const desktop = serving(new FakeDesktop());
      const { state } = await makePhone(desktop);
      await state.syncOracle();
      desktop.reachable = false;
      await assert.rejects(() => state.cardDetail('p-x', 'Nonexistent Card'));
    });

  test('an empty search asks for nothing rather than everything', async () => {
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.syncOracle();
    desktop.reachable = false;
    assert.deepEqual((await state.searchCards({ name: '  ' })).cards, []);
  });

  test('the PC is preferred when it is there', async () => {
    // It searches on rules text, colours, types, legality and price. This
    // is the case where the PC is genuinely better, so the phone is the
    // fallback rather than the fast path.
    const desktop = serving(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.syncOracle();
    let asked = 0;
    const inner = desktop.handle.bind(desktop);
    desktop.handle = (route, payload) => {
      if (route === 'cards/search') {
        asked += 1;
        return { cards: [], total: 0, offset: 0, limit: 0 };
      }
      return inner(route, payload);
    };
    await state.searchCards({ name: 'Sol' });
    assert.equal(asked, 1);
  });
});

describe('a standalone phone is on the free tier', () => {
  /**
   * Nobody bought Pro for a phone that has never met a desktop, there is
   * no licence to honour, and no desktop will ever answer. The unreachable
   * fallback used to say Pro — which is right for a PAIRED phone whose
   * wifi dropped, and wrong here: it handed a standalone install unlimited
   * everything.
   */
  async function alone() {
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    return buildAppState(
      store,
      // An empty address is how "no desktop" is spelled.
      { baseUrl: '', token: '' },
      'phone-1',
      testUuid,
      async () => { throw new Error('there is no desktop'); },
      new DeckStore(db),
    );
  }

  test('it reports free rather than assuming Pro', async () => {
    const snap = await (await alone()).tier();
    assert.equal(snap.tier, 'free');
    assert.equal(snap.is_pro, false);
  });

  test('and knows the free allowances without asking anyone', async () => {
    const snap = await (await alone()).tier();
    assert.equal(snap.allowances.collections, 3);
    assert.equal(snap.allowances.saved_decks, 3);
  });

  test('so the group limit actually bites with no PC', async () => {
    // The failure this prevents: a standalone phone quietly handing out
    // what the desktop sells.
    const state = await alone();
    await state.tier();
    for (let n = 0; n < 3; n += 1) await state.newCollection(`Box ${n}`);
    await assert.rejects(() => state.newCollection('Box 4'),
      /Pro keeps as many/);
  });

  test('a PAIRED phone out of range is still treated as Pro', async () => {
    // Different question, and the answer stays generous: a wifi drop must
    // not lock a paying user out of features they own.
    const db = new MemoryDatabase();
    const store = new LocalStore(db);
    await store.init();
    const state = buildAppState(
      store,
      { baseUrl: 'https://100.64.0.1:8791', token: 't' },
      'phone-1',
      testUuid,
      async () => { throw new Error('out of range'); },
      new DeckStore(db),
    );
    assert.equal((await state.tier()).is_pro, true);
  });
});
