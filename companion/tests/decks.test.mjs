/**
 * Decks, and the question you actually ask in a shop.
 *
 * "What do I still need for this deck" has to be answerable with no signal,
 * because that is exactly when it is asked. So the shortfall maths runs on the
 * phone against its own mirror, and only the analysis — which needs the
 * catalogue — goes to the PC.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  DeckStore,
  deckSize,
  formatDecklist,
  parseDecklist,
  shortfall,
} from '../src/lib/decks.ts';
import { LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase } from './harness.mjs';

describe('reading a decklist', () => {
  test('counts and names', () => {
    const { cards } = parseDecklist('4 Lightning Bolt\n1 Sol Ring');
    assert.deepEqual(cards, { 'Lightning Bolt': 4, 'Sol Ring': 1 });
  });

  test('a bare name means one copy', () => {
    const { cards } = parseDecklist('Sol Ring');
    assert.equal(cards['Sol Ring'], 1);
  });

  test('the "4x" spelling', () => {
    const { cards } = parseDecklist('4x Lightning Bolt');
    assert.equal(cards['Lightning Bolt'], 4);
  });

  test('set codes from exported lists are stripped', () => {
    // People paste from everywhere; refusing a list because of a bracket
    // helps nobody.
    const { cards } = parseDecklist('1 Sol Ring (CMM) 410\n2 Arcane Signet [ELD]');
    assert.equal(cards['Sol Ring'], 1);
    assert.equal(cards['Arcane Signet'], 2);
  });

  test('section headers and comments are ignored', () => {
    const { cards } = parseDecklist(
      'Commander\n1 Atraxa\n\n// notes\nDeck:\n4 Sol Ring\n# end',
    );
    assert.deepEqual(cards, { Atraxa: 1, 'Sol Ring': 4 });
  });

  test('repeated lines add up rather than overwrite', () => {
    const { cards } = parseDecklist('2 Sol Ring\n2 Sol Ring');
    assert.equal(cards['Sol Ring'], 4);
  });

  test('unreadable lines are reported, not silently dropped', () => {
    const { cards, skipped } = parseDecklist('4 Lightning Bolt\n0 Nothing');
    assert.equal(cards['Lightning Bolt'], 4);
    assert.deepEqual(skipped, ['0 Nothing']);
  });

  test('round trips through formatting', () => {
    const text = '4 Lightning Bolt\n1 Sol Ring';
    assert.equal(formatDecklist(parseDecklist(text).cards), text);
  });

  test('counting a deck', () => {
    assert.equal(deckSize(parseDecklist('4 Bolt\n2 Sol Ring').cards), 6);
  });
});

describe('what you still need', () => {
  const deck = { 'Sol Ring': 1, 'Lightning Bolt': 4, 'Arcane Signet': 1 };

  test('counts what is missing', () => {
    const missing = shortfall(deck, [
      { card_name: 'Sol Ring', quantity: 1 },
      { card_name: 'Lightning Bolt', quantity: 2 },
    ]);
    assert.deepEqual(
      missing.map((m) => [m.name, m.short]),
      [['Lightning Bolt', 2], ['Arcane Signet', 1]],
    );
  });

  test('copies in different collections all count', () => {
    // The deck asks whether you own the card, not where you filed it.
    const missing = shortfall({ 'Sol Ring': 3 }, [
      { card_name: 'Sol Ring', quantity: 1 },
      { card_name: 'Sol Ring', quantity: 1 },
    ]);
    assert.equal(missing[0].short, 1);
  });

  test('matching ignores case', () => {
    const missing = shortfall({ 'sol ring': 1 }, [
      { card_name: 'Sol Ring', quantity: 1 },
    ]);
    assert.equal(missing.length, 0);
  });

  test('a deck you can build reports nothing missing', () => {
    const missing = shortfall({ 'Sol Ring': 1 }, [
      { card_name: 'Sol Ring', quantity: 4 },
    ]);
    assert.deepEqual(missing, []);
  });

  test('the biggest gap comes first', () => {
    const missing = shortfall({ A: 4, B: 1 }, []);
    assert.equal(missing[0].name, 'A');
  });
});

describe('storing decks', () => {
  async function store() {
    const db = new MemoryDatabase();
    await new LocalStore(db).init();
    return new DeckStore(db);
  }

  test('saved and read back intact', async () => {
    const decks = await store();
    await decks.save({
      deck_id: 'd1', name: 'Shop brew', format: 'commander',
      decklist: { 'Sol Ring': 1 }, notes: '', updated_at: '2026-08-23T10:00:00Z',
    });
    const read = await decks.get('d1');
    assert.equal(read.name, 'Shop brew');
    assert.deepEqual(read.decklist, { 'Sol Ring': 1 });
  });

  test('saving again replaces rather than duplicates', async () => {
    // A deck is a document: last write wins, because a half-merged decklist
    // is worse than a lost edit.
    const decks = await store();
    const deck = {
      deck_id: 'd1', name: 'First', format: '', decklist: {}, notes: '',
      updated_at: '2026-08-23T10:00:00Z',
    };
    await decks.save(deck);
    await decks.save({ ...deck, name: 'Second',
                       updated_at: '2026-08-23T11:00:00Z' });
    const all = await decks.list();
    assert.equal(all.length, 1);
    assert.equal(all[0].name, 'Second');
  });

  test('deleting', async () => {
    const decks = await store();
    await decks.save({
      deck_id: 'd1', name: 'X', format: '', decklist: {}, notes: '',
      updated_at: '2026-08-23T10:00:00Z',
    });
    await decks.remove('d1');
    assert.equal((await decks.list()).length, 0);
  });

  test('editing a deck does not touch what you own', async () => {
    // Deck contents and ownership are separate questions; conflating them
    // would mean editing a list could change an inventory.
    const db = new MemoryDatabase();
    const local = new LocalStore(db);
    await local.init();
    await local.applyDelta({
      printing_id: 'p1', card_name: 'Sol Ring', oracle_id: '', finish: 'nonfoil',
      condition: 'NM', language: 'en', location: '',
      collection_uid: '00000000-0000-4000-8000-00000000d0cc', delta: 2,
      reason: 'test',
    });

    const decks = new DeckStore(db);
    await decks.save({
      deck_id: 'd1', name: 'X', format: '',
      decklist: { 'Sol Ring': 4 }, notes: '', updated_at: 'now',
    });
    await decks.remove('d1');

    assert.equal(await local.totalCards(), 2, 'the cards are untouched');
  });
});
