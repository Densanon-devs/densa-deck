/**
 * Decks and results reaching the phone, and leaving it.
 *
 * The plumbing existed on both sides and had never been connected. The
 * phone's `applyRemote` had no case for any deck kind, so a deck built on
 * the PC fell through to `default:` — stored, acknowledged, and never
 * written anywhere. Nothing on the phone emitted a deck event either, so a
 * deck edited here stayed here.
 *
 * Two things have to hold for this to be worth trusting at a table:
 *
 *   - a game logged with no desktop in range is still logged, and still
 *     arrives later;
 *   - applying what the PC sent must not look like an edit made here, or the
 *     two devices hand one deck back and forth forever.
 */

import assert from 'node:assert/strict';
import { beforeEach, describe, test } from 'node:test';

import { MemoryDatabase, FakeDesktop, testUuid, resetUuid } from './harness.mjs';
import { LocalStore } from '../src/lib/store.ts';
import { SyncEngine, entriesFromSync } from '../src/lib/sync.ts';
import { DeckStore, summariseRecord } from '../src/lib/decks.ts';
import { DesktopClient } from '../src/lib/client.ts';

async function makePhone(desktop, { device = 'phone-1' } = {}) {
  const db = new MemoryDatabase();
  const store = new LocalStore(db);
  await store.init();
  const decks = new DeckStore(db);
  const client = new DesktopClient(
    { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
    { fetchImpl: desktop.fetchImpl },
  );
  return {
    store,
    decks,
    engine: new SyncEngine(store, client, device, testUuid, decks),
  };
}

/** An event as the desktop would send it. */
function deckEvent(overrides = {}) {
  return {
    event_uid: 'e-deck-1',
    device: 'pc-1',
    seq: 1,
    kind: 'deck-upsert',
    created_at: '2026-01-01T00:00:00Z',
    payload: {
      deck_id: 'blue',
      name: 'Blue Deck',
      format: 'commander',
      notes: '',
      decklist: { 'Sol Ring': 1, Island: 30 },
      entries: [
        { name: 'Sol Ring', qty: 1, printing_id: 'p-sol' },
        { name: 'Island', qty: 30 },
      ],
      sideboard: [],
      updated_at: '2026-01-01T00:00:00Z',
    },
    ...overrides,
  };
}

function gameEvent(overrides = {}) {
  return {
    event_uid: 'e-game-1',
    device: 'pc-1',
    seq: 2,
    kind: 'deck-game',
    created_at: '2026-01-02T00:00:00Z',
    payload: {
      deck_id: 'blue',
      game_uid: 'g-1',
      result: 'win',
      version_number: 1,
      opponent: '',
      notes: '',
      played_at: '2026-01-02T00:00:00Z',
      removed: false,
    },
    ...overrides,
  };
}

beforeEach(() => resetUuid());

describe('a deck arriving from the desktop', () => {
  test('is written, not merely remembered', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent());

    await engine.sync();

    const deck = await decks.get('blue');
    assert.ok(deck, 'the deck fell through to default: and vanished');
    assert.equal(deck.name, 'Blue Deck');
  });

  test('keeps the printing each slot named', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent());

    await engine.sync();

    const deck = await decks.get('blue');
    const sol = deck.decklist.find((e) => e.name === 'Sol Ring');
    assert.equal(sol.printing_id, 'p-sol');
  });

  test('is readable from an older desktop that sends only the map', async () => {
    // A build that predates entries sends `decklist` alone. Refusing it
    // would make upgrading one device break sync with the other.
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    const event = deckEvent();
    delete event.payload.entries;
    desktop.events.push(event);

    await engine.sync();

    const deck = await decks.get('blue');
    assert.equal(deck.decklist.length, 2);
    assert.equal(
      deck.decklist.find((e) => e.name === 'Island').qty,
      30,
    );
  });

  test('a delete removes it here too', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent());
    await engine.sync();

    desktop.events.push({
      event_uid: 'e-del',
      device: 'pc-1',
      seq: 3,
      kind: 'deck-delete',
      created_at: '2026-01-03T00:00:00Z',
      payload: { deck_id: 'blue' },
    });
    await engine.sync();

    assert.equal(await decks.get('blue'), undefined);
  });
});

describe('the newer edit wins whichever way it travels', () => {
  test('an older event does not overwrite a newer local deck', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    await decks.save({
      deck_id: 'blue',
      name: 'Mine',
      format: 'commander',
      decklist: [{ name: 'Island', qty: 40 }],
      sideboard: [],
      notes: '',
      updated_at: '2026-06-01T00:00:00Z',
    });

    desktop.events.push(deckEvent());          // dated January
    await engine.sync();

    const deck = await decks.get('blue');
    assert.equal(deck.name, 'Mine', 'a stale copy overwrote a newer edit');
  });

  test('a newer event does overwrite', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    await decks.save({
      deck_id: 'blue',
      name: 'Mine',
      format: 'commander',
      decklist: [{ name: 'Island', qty: 40 }],
      sideboard: [],
      notes: '',
      updated_at: '2020-01-01T00:00:00Z',
    });

    desktop.events.push(deckEvent());
    await engine.sync();

    assert.equal((await decks.get('blue')).name, 'Blue Deck');
  });

  test('the deck edit time decides, not when the event was written', async () => {
    // A deck edited at noon and synced at six must not beat an edit made at
    // three. The payload says when the deck changed; `created_at` says when
    // it went on the wire.
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    await decks.save({
      deck_id: 'blue',
      name: 'Mine',
      format: 'commander',
      decklist: [{ name: 'Island', qty: 40 }],
      sideboard: [],
      notes: '',
      updated_at: '2026-03-01T00:00:00Z',
    });

    const event = deckEvent({ created_at: '2026-12-31T00:00:00Z' });
    event.payload.updated_at = '2026-01-01T00:00:00Z';   // edited long before
    desktop.events.push(event);
    await engine.sync();

    assert.equal((await decks.get('blue')).name, 'Mine');
  });
});

describe('results', () => {
  test('a game from the desktop counts here', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent(), gameEvent());

    await engine.sync();

    assert.equal((await decks.recordFor('blue')).record, '1-0');
  });

  test('the same game twice is one game', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent(), gameEvent());
    await engine.sync();
    // The desktop re-sends it — a baseline carries games as well as the log.
    desktop.events.push(gameEvent({ event_uid: 'e-game-1-again' }));
    await engine.sync();

    assert.equal(
      (await decks.recordFor('blue')).games,
      1,
      'the record doubled on a re-send',
    );
  });

  test('a game the desktop took back is taken back here', async () => {
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent(), gameEvent());
    await engine.sync();

    desktop.events.push(gameEvent({
      event_uid: 'e-game-gone',
      seq: 3,
      payload: { deck_id: 'blue', game_uid: 'g-1', removed: true },
    }));
    await engine.sync();

    assert.equal((await decks.recordFor('blue')).games, 0);
  });

  test('deleting a deck takes its games with it', async () => {
    // A deck id can be reused. A surviving game would credit the next deck
    // with the last one's record.
    const desktop = new FakeDesktop();
    const { engine, decks } = await makePhone(desktop);
    desktop.events.push(deckEvent(), gameEvent());
    await engine.sync();

    await decks.remove('blue');

    assert.equal((await decks.recordFor('blue')).games, 0);
  });
});

describe('logging a game here', () => {
  test('is written locally even with nothing reachable', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { engine, decks } = await makePhone(desktop);

    await decks.recordGame({
      game_uid: 'local-1',
      deck_id: 'blue',
      result: 'win',
      played_at: '2026-05-01T00:00:00Z',
    });
    await engine.recordDeckGame({
      deck_id: 'blue', game_uid: 'local-1', result: 'win',
      played_at: '2026-05-01T00:00:00Z',
    });

    assert.equal((await decks.recordFor('blue')).record, '1-0');
    assert.equal(await engine.pending(), 1, 'nothing waiting to be sent');
  });

  test('and reaches the desktop once something is', async () => {
    const desktop = new FakeDesktop();
    desktop.reachable = false;
    const { engine } = await makePhone(desktop);
    await engine.recordDeckGame({
      deck_id: 'blue', game_uid: 'local-1', result: 'win',
      played_at: '2026-05-01T00:00:00Z',
    });

    desktop.reachable = true;
    await engine.sync();

    // Pushed events land in the fake's own log, tagged with this phone.
    const fromPhone = desktop.events.filter((e) => e.device === 'phone-1');
    const kinds = fromPhone.map((e) => e.kind);
    assert.ok(kinds.includes('deck-game'), kinds.join(','));
  });

  test('a deck edited here is offered to the desktop with its printings', async () => {
    const desktop = new FakeDesktop();
    const { engine } = await makePhone(desktop);
    await engine.recordDeckUpsert({
      deck_id: 'mine',
      name: 'Built on the phone',
      format: 'commander',
      decklist: [{ name: 'Sol Ring', qty: 1, printing_id: 'p-sol' }],
      sideboard: [],
      notes: '',
      updated_at: '2026-05-01T00:00:00Z',
    });
    await engine.sync();

    const sent = desktop.events.find(
      (e) => e.device === 'phone-1' && e.kind === 'deck-upsert');
    assert.ok(sent, 'nothing was sent');
    assert.equal(sent.payload.entries[0].printing_id, 'p-sol');
    // And the map too, for a desktop that predates entries.
    assert.equal(sent.payload.decklist['Sol Ring'], 1);
  });
});

describe('applying is not editing', () => {
  test('a deck from the desktop is not queued back at it', async () => {
    const desktop = new FakeDesktop();
    const { engine } = await makePhone(desktop);
    desktop.events.push(deckEvent(), gameEvent());

    await engine.sync();

    assert.equal(
      await engine.pending(),
      0,
      'the phone queued the PC\'s own deck straight back at it',
    );
  });
});

describe('the record arithmetic matches the desktop', () => {
  test('draws are games played and not games lost', () => {
    const r = summariseRecord({ win: 1, draw: 1 });
    assert.equal(r.record, '1-0-1');
    assert.equal(r.win_rate, 0.5);
  });

  test('never played is null rather than nought per cent', () => {
    assert.equal(summariseRecord({}).win_rate, null);
  });
});

describe('entries off the wire', () => {
  test('a list keeps its printings', () => {
    const out = entriesFromSync([{ name: 'Sol Ring', qty: 1, set_code: 'cmm' }]);
    assert.equal(out[0].set_code, 'cmm');
  });

  test('a map still becomes entries', () => {
    const out = entriesFromSync({ Island: 30 });
    assert.deepEqual(out, [{ name: 'Island', qty: 30 }]);
  });

  test('rubbish is dropped rather than stored as a card', () => {
    const out = entriesFromSync([
      { name: '', qty: 4 },
      { name: 'Real', qty: 0 },
      { name: 'Also real', qty: 2 },
    ]);
    assert.deepEqual(out, [{ name: 'Also real', qty: 2,
      printing_id: undefined, set_code: undefined,
      collector_number: undefined }]);
  });
});

describe('an edit made here reaches the desktop', () => {
  /**
   * The gap this closes: `deckChanged` and `deckDeleted` were written and
   * nothing ever called them. Every screen went straight to
   * `DeckStore.save`, so a deck edited on the phone was stored and never
   * broadcast — and the sync tests drove the engine directly, so they passed
   * the whole time the real path was disconnected.
   *
   * These go through `AppState`, which is the door the screens use.
   */
  async function makeState(desktop) {
    const { buildAppState } = await import('../src/lib/app-state.ts');
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
    return { state, decks };
  }

  const DECK = {
    deck_id: 'mine',
    name: 'Built here',
    format: 'commander',
    decklist: [{ name: 'Sol Ring', qty: 1, printing_id: 'p-sol' }],
    sideboard: [],
    commander: [{ name: 'Yuriko', qty: 1 }],
    notes: '',
    updated_at: '2026-05-01T00:00:00Z',
  };

  test('saving a deck queues it for the desktop', async () => {
    const desktop = new FakeDesktop();
    const { state } = await makeState(desktop);

    await state.saveDeck(DECK);

    assert.equal(await state.engine.pending(), 1,
      'the deck was stored and never broadcast');
  });

  test('and it is stored locally too', async () => {
    const desktop = new FakeDesktop();
    const { state, decks } = await makeState(desktop);
    await state.saveDeck(DECK);
    assert.equal((await decks.get('mine'))?.name, 'Built here');
  });

  test('the commander goes with it, both ways of saying so', async () => {
    const desktop = new FakeDesktop();
    const { state } = await makeState(desktop);
    await state.saveDeck(DECK);
    await state.engine.sync();

    const sent = desktop.events.find(
      (e) => e.device === 'phone-1' && e.kind === 'deck-upsert');
    assert.ok(sent, 'nothing was sent');
    assert.equal(sent.payload.commander[0].name, 'Yuriko');
    assert.deepEqual(sent.payload.zones.commander, ['Yuriko'],
      'the desktop keys zones by name and would file it in the ninety-nine');
  });

  test('the map the desktop reads counts the commander as a card', async () => {
    const desktop = new FakeDesktop();
    const { state } = await makeState(desktop);
    await state.saveDeck(DECK);
    await state.engine.sync();
    const sent = desktop.events.find(
      (e) => e.device === 'phone-1' && e.kind === 'deck-upsert');
    assert.equal(sent.payload.decklist.Yuriko, 1,
      'a desktop reading only the map would receive a 99-card deck');
  });

  test('deleting a deck here queues that too', async () => {
    const desktop = new FakeDesktop();
    const { state, decks } = await makeState(desktop);
    await state.saveDeck(DECK);
    await state.engine.sync();

    await state.removeDeck('mine');
    await state.engine.sync();

    const kinds = desktop.events
      .filter((e) => e.device === 'phone-1').map((e) => e.kind);
    assert.ok(kinds.includes('deck-delete'), kinds.join(','));
    assert.equal(await decks.get('mine'), undefined);
  });

  test('applying a deck still does NOT queue anything', async () => {
    // The other half of the same rule: one door broadcasts, the other does
    // not, and folding them together would make the two devices hand a deck
    // to each other forever.
    const desktop = new FakeDesktop();
    const { state } = await makeState(desktop);
    desktop.events.push(deckEvent());

    await state.engine.sync();

    assert.equal(await state.engine.pending(), 0);
  });
});
