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
  addToDeck,
  costToFinish,
  deckSize,
  deckWarnings,
  formatDecklist,
  mergeCounts,
  parseDecklist,
  removeFromDeck,
  shortfall,
  wishlistCost,
  wishlistFromDecks,
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

describe('building a deck from cards you do not own', () => {
  test('an unowned card goes in like any other', () => {
    // A deck says what it WANTS. Refusing to list a card you do not own
    // would make the builder useless for working out what to buy, which is
    // most of what it is for.
    const deck = addToDeck({}, 'Black Lotus');
    assert.equal(deck['Black Lotus'], 1);
  });

  test('adding the same card again increases the count', () => {
    let deck = addToDeck({}, 'Lightning Bolt', 2);
    deck = addToDeck(deck, 'Lightning Bolt', 2);
    assert.equal(deck['Lightning Bolt'], 4);
  });

  test('the original list is not mutated', () => {
    const before = { 'Sol Ring': 1 };
    addToDeck(before, 'Black Lotus');
    assert.deepEqual(before, { 'Sol Ring': 1 });
  });

  test('blank names and nonsense counts are ignored', () => {
    assert.deepEqual(addToDeck({}, '   '), {});
    assert.deepEqual(addToDeck({}, 'Sol Ring', 0), {});
  });

  test('removing takes copies off and then the line itself', () => {
    let deck = { 'Sol Ring': 2 };
    deck = removeFromDeck(deck, 'Sol Ring');
    assert.equal(deck['Sol Ring'], 1);
    deck = removeFromDeck(deck, 'Sol Ring');
    assert.equal('Sol Ring' in deck, false, 'a zero line is not kept');
  });

  test('removing a card that is not there changes nothing', () => {
    assert.deepEqual(removeFromDeck({ 'Sol Ring': 1 }, 'Black Lotus'),
                     { 'Sol Ring': 1 });
  });

  test('a deck of entirely unowned cards reports all of them missing', () => {
    const deck = addToDeck(addToDeck({}, 'Black Lotus'), 'Mox Jet');
    const missing = shortfall(deck, []);
    assert.equal(missing.length, 2);
  });
});

describe('what finishing a deck would cost', () => {
  test('only the copies you lack are counted', () => {
    const missing = shortfall({ 'Sol Ring': 4 },
                              [{ card_name: 'Sol Ring', quantity: 1 }]);
    const cost = costToFinish(missing, { 'sol ring': 2 });
    assert.equal(cost.usd, 6, 'three missing at 2 each, not four');
  });

  test('an unknown price is admitted, not treated as free', () => {
    // A total that silently counts "unknown" as zero is worse than one that
    // says what it could not price.
    const missing = shortfall({ 'Black Lotus': 1, 'Sol Ring': 1 }, []);
    const cost = costToFinish(missing, { 'sol ring': 2 });
    assert.equal(cost.usd, 2);
    assert.equal(cost.unpriced, 1);
  });

  test('a deck you can already build costs nothing', () => {
    const cost = costToFinish([], {});
    assert.equal(cost.usd, 0);
    assert.equal(cost.unpriced, 0);
  });
});

describe('the wishlist', () => {
  const deck = (id, name, decklist) => ({
    deck_id: id, name, format: '', decklist, notes: '', updated_at: 'now',
  });

  test('wanting a card is not owning it', () => {
    // The failure this guards: a wished card looking owned would silently
    // vanish from the list of things to buy.
    const rows = wishlistFromDecks([deck('d1', 'Brew', { 'Black Lotus': 1 })], []);
    assert.equal(rows[0].card_name, 'Black Lotus');
    assert.equal(rows[0].quantity, 1);
  });

  test('cards you own do not appear', () => {
    const rows = wishlistFromDecks(
      [deck('d1', 'Brew', { 'Sol Ring': 1 })],
      [{ card_name: 'Sol Ring', quantity: 1 }],
    );
    assert.deepEqual(rows, []);
  });

  test('only the copies you lack are wanted', () => {
    const rows = wishlistFromDecks(
      [deck('d1', 'Brew', { 'Sol Ring': 4 })],
      [{ card_name: 'Sol Ring', quantity: 1 }],
    );
    assert.equal(rows[0].quantity, 3);
  });

  test('the headline is what ONE deck needs at once', () => {
    // Two decks each wanting one copy need one copy between them unless both
    // are built at the same time.
    const rows = wishlistFromDecks([
      deck('d1', 'Brew', { 'Black Lotus': 1 }),
      deck('d2', 'Other', { 'Black Lotus': 1 }),
    ], []);
    assert.equal(rows[0].quantity, 1);
    assert.equal(rows[0].quantityAcrossDecks, 2);
  });

  test('it records which decks want a card', () => {
    const rows = wishlistFromDecks([
      deck('d1', 'Brew', { 'Black Lotus': 1 }),
      deck('d2', 'Other', { 'Black Lotus': 2 }),
    ], []);
    assert.deepEqual(rows[0].wantedBy.map((w) => w.deck_name).sort(),
                     ['Brew', 'Other']);
    assert.equal(rows[0].quantity, 2, 'the deck that needs most sets it');
  });

  test('the biggest gap comes first', () => {
    const rows = wishlistFromDecks(
      [deck('d1', 'Brew', { A: 1, B: 4 })], [],
    );
    assert.equal(rows[0].card_name, 'B');
  });

  test('no decks means nothing wanted', () => {
    assert.deepEqual(wishlistFromDecks([], []), []);
  });

  test('a deck you can already build wants nothing', () => {
    const rows = wishlistFromDecks(
      [deck('d1', 'Brew', { 'Sol Ring': 1 })],
      [{ card_name: 'Sol Ring', quantity: 4 }],
    );
    assert.deepEqual(rows, []);
  });

  test('copies across collections all count as owned', () => {
    const rows = wishlistFromDecks(
      [deck('d1', 'Brew', { 'Sol Ring': 2 })],
      [{ card_name: 'Sol Ring', quantity: 1 },
       { card_name: 'Sol Ring', quantity: 1 }],
    );
    assert.deepEqual(rows, []);
  });

  test('costing the list, with unknowns admitted', () => {
    const rows = wishlistFromDecks(
      [deck('d1', 'Brew', { 'Black Lotus': 1, 'Sol Ring': 2 })], [],
    );
    const cost = wishlistCost(rows, { 'sol ring': 2 });
    assert.equal(cost.usd, 4, 'two Sol Rings at 2');
    assert.equal(cost.unpriced, 1, 'the Lotus is not counted as free');
  });
});

describe('the fifteen you bring but do not start with', () => {
  test('a Sideboard header separates the two', async () => {
    // It used to be SKIPPED and everything under it folded into the
    // maindeck, so a fifteen-card board silently became fifteen extra
    // maindeck cards and the deck read as 75.
    const { cards, sideboard } = parseDecklist(
      '4 Lightning Bolt\n\nSideboard\n2 Pyroblast',
    );
    assert.deepEqual(cards, { 'Lightning Bolt': 4 });
    assert.deepEqual(sideboard, { Pyroblast: 2 });
  });

  test('the abbreviations the exporters actually emit', async () => {
    for (const header of ['Sideboard', 'SIDEBOARD', 'sb', 'Side:']) {
      const { sideboard } = parseDecklist(`4 Bolt\n${header}\n1 Pyroblast`);
      assert.deepEqual(sideboard, { Pyroblast: 1 }, header);
    }
  });

  test('a later Deck header goes back to the maindeck', async () => {
    const { cards, sideboard } = parseDecklist(
      'Sideboard\n1 Pyroblast\nDeck\n4 Bolt',
    );
    assert.deepEqual(sideboard, { Pyroblast: 1 });
    assert.deepEqual(cards, { Bolt: 4 });
  });

  test('a card can be in both, and the counts stay apart', async () => {
    // Three in the deck and one in the board is a real and common shape.
    const { cards, sideboard } = parseDecklist(
      '3 Bolt\nSideboard\n1 Bolt',
    );
    assert.equal(cards.Bolt, 3);
    assert.equal(sideboard.Bolt, 1);
  });

  test('a deck with no board is unchanged', async () => {
    const { cards, sideboard } = parseDecklist('4 Bolt');
    assert.deepEqual(cards, { Bolt: 4 });
    assert.deepEqual(sideboard, {});
  });

  test('formatting writes the header back out', async () => {
    // Without it, one round trip through the text box moves the board into
    // the deck and nothing says so.
    const text = formatDecklist({ Bolt: 4 }, { Pyroblast: 2 });
    assert.match(text, /Sideboard/);
    const again = parseDecklist(text);
    assert.deepEqual(again.sideboard, { Pyroblast: 2 });
    assert.deepEqual(again.cards, { Bolt: 4 });
  });

  test('formatting a deck with no board adds no header', async () => {
    assert.equal(formatDecklist({ Bolt: 4 }), '4 Bolt');
  });

  test('what you still need counts the board as well', async () => {
    // Those cards get bought and carried like any other. A shortfall from
    // the maindeck alone would tell you to buy none of them.
    assert.deepEqual(mergeCounts({ Bolt: 3 }, { Bolt: 1, Pyroblast: 2 }), {
      Bolt: 4,
      Pyroblast: 2,
    });
  });
});

describe('the sideboard survives being saved', () => {
  test('a deck round-trips through the store with its board', async () => {
    // It did not. `save` wrote only `decklist_json` and the board was gone
    // the next time the deck was opened — the writer knew about it and the
    // schema did not.
    const store = new DeckStore(new MemoryDatabase());
    await store.save({
      deck_id: 'd1',
      name: 'Burn',
      format: 'modern',
      decklist: { Bolt: 4 },
      sideboard: { Pyroblast: 2 },
      notes: '',
      updated_at: new Date(0).toISOString(),
    });
    const back = await store.get('d1');
    assert.deepEqual(back.decklist, { Bolt: 4 });
    assert.deepEqual(back.sideboard, { Pyroblast: 2 });
  });

  test('a deck saved before sideboards existed still reads', async () => {
    // Rows on people's phones hold a bare map. Reading it as `{main, side}`
    // would return an empty deck and look like the deck had been wiped.
    const db = new MemoryDatabase();
    await db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      ['old', 'Legacy deck', 'modern', '{"Bolt":4}', '', new Date(0).toISOString()],
    );
    const back = await new DeckStore(db).get('old');
    assert.deepEqual(back.decklist, { Bolt: 4 });
    assert.deepEqual(back.sideboard, {});
  });

  test('unreadable json is an empty deck, not a crash', async () => {
    const db = new MemoryDatabase();
    await db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      ['bad', 'Broken', '', '{not json', '', new Date(0).toISOString()],
    );
    const back = await new DeckStore(db).get('bad');
    assert.deepEqual(back.decklist, {});
  });
});

describe('over the line, but not stopped at it', () => {
  test('a fifth copy is allowed and flagged', async () => {
    // Half of deckbuilding is holding a pile that is not legal yet.
    const over = addToDeck({ Bolt: 4 }, 'Bolt');
    assert.equal(over.Bolt, 5);
    const said = deckWarnings(over, {}, 'modern');
    assert.ok(said.some((w) => w.kind === 'copies' && /Bolt/.test(w.text)));
  });

  test('basic lands are never over', async () => {
    assert.deepEqual(
      deckWarnings({ Mountain: 24 }, {}, 'modern').filter((w) => w.kind === 'copies'),
      [],
    );
  });

  test('the cards that say so on themselves are not over either', async () => {
    assert.deepEqual(
      deckWarnings({ 'Relentless Rats': 30 }, {}, 'modern')
        .filter((w) => w.kind === 'copies'),
      [],
    );
  });

  test('commander is singleton, so a second copy is flagged', async () => {
    assert.ok(
      deckWarnings({ 'Sol Ring': 2 }, {}, 'commander')
        .some((w) => w.kind === 'copies'),
    );
  });

  test('deck and board are counted together for the copy limit', async () => {
    // Three in the deck and two in the board is five copies you own.
    assert.ok(
      deckWarnings({ Bolt: 3 }, { Bolt: 2 }, 'modern')
        .some((w) => w.kind === 'copies'),
    );
  });

  test('an oversized sideboard is flagged', async () => {
    const board = Object.fromEntries(
      Array.from({ length: 16 }, (_, i) => [`Card ${i}`, 1]),
    );
    assert.ok(deckWarnings({}, board, 'modern').some((w) => w.kind === 'sideboard'));
  });

  test('an empty deck is not nagged about its size', async () => {
    // Warning that a deck you have not started is too small is noise.
    assert.deepEqual(deckWarnings({}, {}, 'modern'), []);
  });

  test('a legal deck says nothing at all', async () => {
    const legal = Object.fromEntries(
      Array.from({ length: 15 }, (_, i) => [`Card ${i}`, 4]),
    );
    assert.deepEqual(deckWarnings(legal, {}, 'modern'), []);
  });
});
