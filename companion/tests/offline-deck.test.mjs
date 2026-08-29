/**
 * A deck you have already looked at, with the desktop gone.
 *
 * The phone's mirror answers for cards you OWN, so a deck of your own cards
 * survived going offline. Everything else came from the desktop and vanished
 * with it: the price of a card you have never owned, the art standing in for
 * a name-only slot, the colour identity a legality check needs. Opening a
 * deck out of range showed grey rectangles and no total — the opposite of
 * what a companion is for.
 *
 * So what the desktop says is kept, and the cache warms simply by using the
 * app while it is in range.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore } from '../src/lib/store.ts';
import { DesktopClient } from '../src/lib/client.ts';
import { buildAppState } from '../src/lib/app-state.ts';
import { DeckStore, deckColorIdentity, withinIdentity } from '../src/lib/decks.ts';

/** A desktop that answers `decks/resolve` with facts the mirror cannot know. */
function withResolve(desktop) {
  const inner = desktop.handle.bind(desktop);
  desktop.handle = (route, payload) => {
    if (route !== 'decks/resolve') return inner(route, payload);
    return {
      catalogue_ready: true,
      slots: (payload.slots || []).map((slot) => ({
        name: slot.name,
        printing_id: `p-${slot.name.toLowerCase().replace(/\s+/g, '-')}`,
        set_code: 'tst',
        collector_number: '1',
        price_usd: slot.name === 'Sol Ring' ? 16.5 : 0.25,
        color_identity: slot.name === 'Yuriko' ? ['B', 'U']
          : slot.name === 'Lightning Bolt' ? ['R'] : [],
        type_line: 'Artifact',
        found: true,
      })),
    };
  };
  return desktop;
}

async function makePhone(desktop) {
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const decks = new DeckStore(db);
  const state = buildAppState(
    store,
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    'phone-1',
    testUuid,
    desktop.fetchImpl,
    decks,
  );
  return { store, decks, state };
}

const ENTRIES = [
  { name: 'Sol Ring', qty: 1 },
  { name: 'Lightning Bolt', qty: 4 },
];

beforeEach(() => resetUuid());

describe('what the desktop said is kept', () => {
  test('a resolve while in range fills the cache', async () => {
    const desktop = withResolve(new FakeDesktop());
    const { store, state } = await makePhone(desktop);

    await state.deckSlots(ENTRIES);

    const cached = await store.cachedSlotFacts();
    assert.equal(cached.size, 2);
    assert.equal(cached.get('sol ring').price_usd, 16.5);
  });

  test('and the same deck still has its prices with the desktop gone', async () => {
    const desktop = withResolve(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.deckSlots(ENTRIES);

    desktop.reachable = false;
    const slots = await state.deckSlots(ENTRIES);

    assert.equal(slots['sol ring'].price_usd, 16.5,
      'the price vanished with the desktop');
    assert.equal(slots['lightning bolt'].price_usd, 0.25);
  });

  test('and still has a picture for every slot', async () => {
    const desktop = withResolve(new FakeDesktop());
    const { state } = await makePhone(desktop);
    await state.deckSlots(ENTRIES);

    desktop.reachable = false;
    const slots = await state.deckSlots(ENTRIES);

    assert.ok(slots['sol ring'].printing_id, 'a grey rectangle');
    assert.ok(slots['lightning bolt'].printing_id);
  });

  test('a deck never seen in range still comes back empty rather than wrong',
    async () => {
      const desktop = withResolve(new FakeDesktop());
      const { state } = await makePhone(desktop);
      desktop.reachable = false;

      const slots = await state.deckSlots([{ name: 'Never Seen', qty: 1 }]);
      assert.deepEqual(slots, {},
        'invented facts for a card it has never been told about');
    });

  test('a later resolve replaces what was remembered', async () => {
    const desktop = withResolve(new FakeDesktop());
    const { store, state } = await makePhone(desktop);
    await state.deckSlots([{ name: 'Sol Ring', qty: 1 }]);

    // The price moved on the desktop.
    const inner = desktop.handle.bind(desktop);
    desktop.handle = (route, payload) => {
      const reply = inner(route, payload);
      if (route === 'decks/resolve') {
        reply.slots.forEach((s) => { s.price_usd = 22; });
      }
      return reply;
    };
    await state.deckSlots([{ name: 'Sol Ring', qty: 1 }]);

    const cached = await store.cachedSlotFacts();
    assert.equal(cached.get('sol ring').price_usd, 22, 'the cache went stale');
  });
});

describe('colour identity survives the desktop going away', () => {
  test('it is cached with the rest of the slot', async () => {
    const desktop = withResolve(new FakeDesktop());
    const { state } = await makePhone(desktop);
    const commander = [{ name: 'Yuriko', qty: 1 }];
    await state.deckSlots(commander);

    desktop.reachable = false;
    const slots = await state.deckSlots(commander);

    assert.deepEqual(
      deckColorIdentity(commander, slots, 'commander'),
      new Set(['B', 'U']),
    );
  });
});

describe('which colours a deck may play', () => {
  const slots = { yuriko: { printing_id: 'p', color_identity: ['B', 'U'] } };
  const commander = [{ name: 'Yuriko', qty: 1 }];

  test('a commander decides it', () => {
    assert.deepEqual(deckColorIdentity(commander, slots, 'commander'),
      new Set(['B', 'U']));
  });

  test('a format without commanders has no constraint at all', () => {
    // null is not the empty set. An empty set would lock out every coloured
    // card in a Modern deck.
    assert.equal(deckColorIdentity(commander, slots, 'modern'), null);
  });

  test('an unknown commander is not guessed at', () => {
    // A check that cannot be made must not be made up — locking cards out
    // because a lookup had not finished is worse than not locking.
    assert.equal(deckColorIdentity(commander, {}, 'commander'), null);
  });

  test('no commander yet means no constraint yet', () => {
    assert.equal(deckColorIdentity([], slots, 'commander'), null);
  });

  test('a colourless commander is a real answer, not a missing one', () => {
    const kozilek = { kozilek: { printing_id: 'p', color_identity: [] } };
    const identity = deckColorIdentity([{ name: 'Kozilek', qty: 1 }],
      kozilek, 'commander');
    assert.deepEqual(identity, new Set());
  });
});

describe('what that identity allows', () => {
  const dimir = new Set(['B', 'U']);

  test('a card inside the colours is fine', () => {
    assert.equal(withinIdentity(['U'], dimir), true);
  });

  test('a card outside them is not', () => {
    assert.equal(withinIdentity(['R'], dimir), false);
  });

  test('partly outside is still outside', () => {
    assert.equal(withinIdentity(['U', 'R'], dimir), false);
  });

  test('colourless fits anywhere', () => {
    assert.equal(withinIdentity([], dimir), true);
  });

  test('nothing is refused when there is no constraint', () => {
    assert.equal(withinIdentity(['R'], null), true);
  });

  test('a card whose colours are unknown is not judged', () => {
    assert.equal(withinIdentity(undefined, dimir), true);
  });

  test('a colourless commander still takes colourless cards', () => {
    assert.equal(withinIdentity([], new Set()), true);
    assert.equal(withinIdentity(['U'], new Set()), false);
  });
});

describe('price history reaches the phone and stays there', () => {
  /**
   * The phone cannot record this itself — no catalogue to price against, no
   * daily trigger — so the desktop keeps the series and the phone keeps what
   * it is handed. Which matters most in a shop with no signal, which is
   * exactly where somebody wants to know whether a card has been climbing.
   */
  function servingHistory(desktop, points, scope = 'printing') {
    const inner = desktop.handle.bind(desktop);
    desktop.handle = (route, payload) =>
      route === 'prices/history'
        ? { points, scope, count: points.length }
        : inner(route, payload);
    return desktop;
  }

  const JAN = [
    { captured_on: '2026-01-01', price_usd: 1.5 },
    { captured_on: '2026-01-02', price_usd: 1.75 },
  ];

  test('a pull returns the desktop series', async () => {
    const desktop = servingHistory(new FakeDesktop(), JAN);
    const { state } = await makePhone(desktop);
    const out = await state.priceHistory('p-bolt', 'Lightning Bolt');
    assert.equal(out.points.length, 2);
    assert.equal(out.cached, false);
  });

  test('and the same series answers with the desktop gone', async () => {
    const desktop = servingHistory(new FakeDesktop(), JAN);
    const { state } = await makePhone(desktop);
    await state.priceHistory('p-bolt', 'Lightning Bolt');

    desktop.reachable = false;
    const out = await state.priceHistory('p-bolt', 'Lightning Bolt');
    assert.equal(out.points.length, 2, 'the history vanished with the desktop');
    assert.equal(out.cached, true, 'and it should say where it came from');
  });

  test('a later pull ADDS days rather than replacing them', async () => {
    // The desktop returns a window. A phone that replaced its copy would
    // forget every day that fell out of it.
    const desktop = servingHistory(new FakeDesktop(), JAN);
    const { state } = await makePhone(desktop);
    await state.priceHistory('p-bolt', 'Lightning Bolt');

    servingHistory(desktop, [{ captured_on: '2026-01-03', price_usd: 2.0 }]);
    const out = await state.priceHistory('p-bolt', 'Lightning Bolt');

    assert.deepEqual(out.points.map((p) => p.captured_on),
      ['2026-01-01', '2026-01-02', '2026-01-03']);
  });

  test('pulling the same day twice keeps one row', async () => {
    const desktop = servingHistory(new FakeDesktop(), JAN);
    const { state } = await makePhone(desktop);
    await state.priceHistory('p-bolt', 'Lightning Bolt');
    const out = await state.priceHistory('p-bolt', 'Lightning Bolt');
    assert.equal(out.points.length, 2);
  });

  test('a revised price for a day replaces that day', async () => {
    const desktop = servingHistory(new FakeDesktop(), JAN);
    const { state } = await makePhone(desktop);
    await state.priceHistory('p-bolt', 'Lightning Bolt');

    servingHistory(desktop, [{ captured_on: '2026-01-02', price_usd: 9.99 }]);
    const out = await state.priceHistory('p-bolt', 'Lightning Bolt');
    const second = out.points.find((p) => p.captured_on === '2026-01-02');
    assert.equal(second.price_usd, 9.99);
  });

  test('a card never asked about has nothing, rather than something', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { state } = await makePhone(desktop);
    const out = await state.priceHistory('p-unknown', 'Never Seen');
    assert.deepEqual(out.points, []);
  });

  test('the card-level scope is carried through', async () => {
    // A wishlist card is tracked at whichever copy was cheapest, so the
    // phone has to know it is looking at a card rather than a printing.
    const desktop = servingHistory(new FakeDesktop(), JAN, 'card');
    const { state } = await makePhone(desktop);
    assert.equal((await state.priceHistory('', 'Lightning Bolt')).scope, 'card');
  });
});

describe('naming a printing on the wishlist', () => {
  /**
   * "A Lightning Bolt" and "the Alpha Lightning Bolt" are different wants,
   * and the desktop tracks them differently: a name-only wish is priced at
   * whichever copy was cheapest that day. So the printing has to survive the
   * trip, or holding down a result in a shop quietly watches the cheap one.
   */
  function recording(desktop) {
    const sent = [];
    const inner = desktop.handle.bind(desktop);
    desktop.handle = (route, payload) => {
      sent.push({ route, payload });
      return route === 'wishlist/add' ? { ok: true } : inner(route, payload);
    };
    return sent;
  }

  test('a picked printing is sent with the card', async () => {
    const desktop = new FakeDesktop();
    const sent = recording(desktop);
    const { state } = await makePhone(desktop);

    await state.wishlistAdd('Lightning Bolt', 1,
      { set_code: 'lea', collector_number: '161' });

    const add = sent.find((c) => c.route === 'wishlist/add');
    assert.equal(add.payload.set_code, 'lea');
    assert.equal(add.payload.collector_number, '161');
  });

  test('and adding without one still means any copy', async () => {
    const desktop = new FakeDesktop();
    const sent = recording(desktop);
    const { state } = await makePhone(desktop);

    await state.wishlistAdd('Lightning Bolt', 1);

    const add = sent.find((c) => c.route === 'wishlist/add');
    assert.equal(add.payload.set_code, '',
      'a wish for the card got pinned to a printing');
  });
});
