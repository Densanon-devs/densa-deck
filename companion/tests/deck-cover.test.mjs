/**
 * The card whose art stands for a deck.
 *
 * Asked for: "we can set a card art per deck even if it isn't
 * commander". A deck is often about a card that is not the one
 * leading it — the combo piece, the one it was built around, the
 * joke — and a list of decks is read by picture long before it is
 * read by name.
 *
 * Stored in the same JSON blob as the decklist, added to the shape
 * rather than bumping its version, which is exactly how `cmd`
 * arrived: a reader that does not know about it takes the fields it
 * does and ignores this one.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { DeckStore } from '../src/lib/decks.ts';
import { RealDatabase } from './real-sqlite.mjs';
import { LocalStore } from '../src/lib/store.ts';

async function decks() {
  const db = new RealDatabase();
  await new LocalStore(db).init();
  return new DeckStore(db);
}

const DECK = {
  deck_id: 'd1',
  name: 'Etrata - Assassins',
  format: 'commander',
  decklist: [{ name: 'Island', qty: 9, printing_id: 'p-isl' }],
  sideboard: [],
  commander: [{ name: 'Etrata', qty: 1, printing_id: 'p-etrata' }],
  notes: '',
  updated_at: '2026-09-22',
};

describe('choosing a picture for a deck', () => {
  test('it survives being saved and read back', async () => {
    const store = await decks();
    await store.save({ ...DECK, cover_printing_id: 'p-joke' });
    const [back] = await store.list();
    assert.equal(back.cover_printing_id, 'p-joke');
  });

  test('a deck that has never chosen one has none', async () => {
    // Absent rather than empty, so the screen falls through to the
    // commander's art instead of asking for the artwork of a
    // printing called ''.
    const store = await decks();
    await store.save(DECK);
    const [back] = await store.list();
    assert.equal(back.cover_printing_id, undefined);
  });

  test('clearing it goes back to having none', async () => {
    const store = await decks();
    await store.save({ ...DECK, cover_printing_id: 'p-joke' });
    await store.save({ ...DECK, cover_printing_id: undefined });
    const [back] = await store.list();
    assert.equal(back.cover_printing_id, undefined);
  });

  test('the rest of the deck is untouched by it', async () => {
    // It rides in the same blob as the decklist, so a bug here
    // would not lose a picture, it would lose the deck.
    const store = await decks();
    await store.save({ ...DECK, cover_printing_id: 'p-joke' });
    const [back] = await store.list();
    assert.equal(back.decklist.length, 1);
    assert.equal(back.decklist[0].qty, 9);
    assert.equal(back.commander[0].name, 'Etrata');
  });

  test('a deck saved before covers existed still reads', async () => {
    /*
      The compatibility that matters: every deck on every phone was
      written without this field. It has to read back as "no cover"
      rather than as a parse failure, which would read as an empty
      deck.
    */
    const db = new RealDatabase();
    await new LocalStore(db).init();
    await db.run(
      `INSERT INTO decks
         (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES ('old', 'Before', 'commander', ?, '', '2026-01-01')`,
      [JSON.stringify({
        v: 2,
        main: [{ name: 'Island', qty: 9 }],
        side: [],
        cmd: [{ name: 'Etrata', qty: 1 }],
      })]);

    const [back] = await new DeckStore(db).list();
    assert.equal(back.cover_printing_id, undefined);
    assert.equal(back.decklist.length, 1);
    assert.equal(back.commander.length, 1);
  });

  test('and one from before commanders existed still reads too', async () => {
    const db = new RealDatabase();
    await new LocalStore(db).init();
    await db.run(
      `INSERT INTO decks
         (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES ('older', 'Ancient', '', ?, '', '2025-01-01')`,
      [JSON.stringify({ 'Sol Ring': 1, Island: 9 })]);

    const [back] = await new DeckStore(db).list();
    assert.equal(back.cover_printing_id, undefined);
    assert.equal(back.decklist.length, 2);
  });
});
