/**
 * Decks, and the question you actually ask in a shop.
 *
 * "What do I still need for this deck" has to be answerable with no signal,
 * because that is exactly when it is asked. So the shortfall maths runs on the
 * phone against its own mirror, and only the analysis — which needs the
 * catalogue — goes to the PC.
 *
 * A deck slot may now name a printing. That is the change these tests exist
 * to hold still, because every way it can go wrong is SILENT: a deck that
 * merges two printings into one line, a printing-level slot quietly satisfied
 * by the wrong card, or an old deck on someone's phone opening empty. None of
 * those throw. All of them are wrong on a screen that looks right.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  DeckStore,
  addToDeck,
  carryPrintings,
  copiesOf,
  costToFinish,
  countByName,
  deckSize,
  deckValue,
  deckWarnings,
  entryKey,
  formatDecklist,
  mergeCounts,
  parseDecklist,
  pricesFromSlots,
  printingLabel,
  removeFromDeck,
  resolveSlots,
  shortfall,
  wishlistCost,
  wishlistFromDecks,
} from '../src/lib/decks.ts';
import { LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase } from './harness.mjs';

/** `{name: qty}` as entries, for the tests where the printing is beside the point. */
const list = (counts) =>
  Object.entries(counts).map(([name, qty]) => ({ name, qty }));

/** Entries back as `{name: qty}`, so an assertion can stay one line. */
const counts = (entries) => countByName(entries);

describe('reading a decklist', () => {
  test('counts and names', () => {
    const { cards } = parseDecklist('4 Lightning Bolt\n1 Sol Ring');
    assert.deepEqual(counts(cards), { 'Lightning Bolt': 4, 'Sol Ring': 1 });
  });

  test('a bare name means one copy', () => {
    const { cards } = parseDecklist('Sol Ring');
    assert.equal(cards[0].qty, 1);
  });

  test('the "4x" spelling', () => {
    const { cards } = parseDecklist('4x Lightning Bolt');
    assert.equal(counts(cards)['Lightning Bolt'], 4);
  });

  test('a set code is kept on the entry, not glued to the name', () => {
    // It used to be stripped and thrown away, which is what made a
    // printing-level list impossible to paste in OR out.
    const { cards } = parseDecklist('1 Sol Ring (CMM) 410\n2 Arcane Signet [ELD]');
    assert.equal(cards[0].name, 'Sol Ring');
    assert.equal(cards[0].set_code, 'CMM');
    assert.equal(cards[0].collector_number, '410');
    assert.equal(cards[1].name, 'Arcane Signet');
    assert.equal(cards[1].set_code, 'ELD');
    assert.equal(cards[1].qty, 2);
  });

  test('comments and deck headers are ignored', () => {
    const { cards } = parseDecklist('// notes\nDeck:\n4 Sol Ring\n# end');
    assert.deepEqual(counts(cards), { 'Sol Ring': 4 });
  });

  test('the commander header is a zone, not noise', () => {
    // It used to fold into the mainboard, which left the phone with no way to
    // know which card the other ninety-nine are legal against — so a
    // singleton format had no colour identity to check anything by.
    const { cards, commander } = parseDecklist(
      'Commander\n1 Atraxa\n\nDeck:\n4 Sol Ring',
    );
    assert.deepEqual(counts(commander), { Atraxa: 1 });
    assert.deepEqual(counts(cards), { 'Sol Ring': 4 });
  });

  test('a commander survives the round trip through the text box', () => {
    const text = 'Commander\n1 Atraxa\n\n4 Sol Ring';
    const parsed = parseDecklist(text);
    const written = formatDecklist(
      parsed.cards,
      parsed.sideboard,
      parsed.commander,
    );
    const again = parseDecklist(written);
    assert.deepEqual(counts(again.commander), { Atraxa: 1 });
    assert.deepEqual(counts(again.cards), { 'Sol Ring': 4 });
  });

  test('repeated lines add up rather than overwrite', () => {
    const { cards } = parseDecklist('2 Sol Ring\n2 Sol Ring');
    assert.equal(cards.length, 1);
    assert.equal(cards[0].qty, 4);
  });

  test('unreadable lines are reported, not silently dropped', () => {
    const { cards, skipped } = parseDecklist('4 Lightning Bolt\n0 Nothing');
    assert.equal(counts(cards)['Lightning Bolt'], 4);
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

describe('a deck slot that names a printing', () => {
  test('a name-only deck round-trips unchanged', () => {
    // The default has to stay exactly what it was. If a plain list came back
    // decorated with set codes, every deck in the app would silently become
    // printing-level the first time it was saved.
    const text = '1 Sol Ring\n4 Lightning Bolt';
    const { cards, sideboard } = parseDecklist(text);
    assert.equal(formatDecklist(cards, sideboard), '4 Lightning Bolt\n1 Sol Ring');
    assert.equal(cards.every((e) => !e.set_code && !e.printing_id), true);
  });

  test('a printing-level entry keeps its set and number through the trip', () => {
    const once = parseDecklist('1 Sol Ring (CMM) 410').cards;
    const text = formatDecklist(once);
    assert.equal(text, '1 Sol Ring (CMM) 410');
    const twice = parseDecklist(text).cards;
    assert.equal(twice[0].set_code, 'CMM');
    assert.equal(twice[0].collector_number, '410');
  });

  test('the same card at two printings is two entries, not one count', () => {
    // The whole point. "The full-art one that cost $50" and "the basic that
    // cost $16" are different cards to the person who owns both, and a model
    // that merges them cannot say which is in the deck.
    const { cards } = parseDecklist('1 Sol Ring (CMM) 410\n1 Sol Ring (LTC) 285');
    assert.equal(cards.length, 2);
    assert.equal(deckSize(cards), 2);
    assert.deepEqual(counts(cards), { 'Sol Ring': 2 }, 'still two Sol Rings');
  });

  test('a named printing and "any printing" are different slots', () => {
    const { cards } = parseDecklist('1 Sol Ring\n1 Sol Ring (CMM) 410');
    assert.equal(cards.length, 2);
    assert.equal(cards.filter((e) => e.set_code).length, 1);
  });

  test('adding by printing keeps the two apart', () => {
    let deck = addToDeck([], { name: 'Sol Ring', printing_id: 'aaa', set_code: 'CMM' });
    deck = addToDeck(deck, { name: 'Sol Ring', printing_id: 'bbb', set_code: 'LTC' });
    deck = addToDeck(deck, { name: 'Sol Ring', printing_id: 'aaa', set_code: 'CMM' });
    assert.equal(deck.length, 2);
    assert.equal(deck.find((e) => e.printing_id === 'aaa').qty, 2);
    assert.equal(copiesOf(deck, 'Sol Ring'), 3, 'three copies of the card');
  });

  test('removing a printing takes that one, not the other', () => {
    const deck = [
      { name: 'Sol Ring', qty: 1, printing_id: 'aaa' },
      { name: 'Sol Ring', qty: 1, printing_id: 'bbb' },
    ];
    const left = removeFromDeck(deck, { name: 'Sol Ring', printing_id: 'aaa' });
    assert.deepEqual(left.map((e) => e.printing_id), ['bbb']);
  });

  test('removing by bare name still reaches a printing slot', () => {
    // Otherwise Remove on a card added from a printing page would do nothing
    // at all, and the button would look broken.
    const deck = [{ name: 'Sol Ring', qty: 1, printing_id: 'aaa' }];
    assert.deepEqual(removeFromDeck(deck, 'Sol Ring'), []);
  });

  test('an id beats a set code when both name the same slot', () => {
    assert.notEqual(
      entryKey({ name: 'Sol Ring', printing_id: 'aaa' }),
      entryKey({ name: 'Sol Ring' }),
    );
    assert.equal(
      entryKey({ name: 'Sol Ring', printing_id: 'AAA' }),
      entryKey({ name: 'sol ring', printing_id: 'aaa' }),
    );
  });

  test('the printing reads as something a person can check on the card', () => {
    assert.equal(printingLabel({ set_code: 'cmm', collector_number: '410' }), 'CMM 410');
    assert.equal(printingLabel({ set_code: 'eld' }), 'ELD');
    assert.equal(printingLabel({}), '');
  });

  test('editing in the text box does not lose the printing ids', () => {
    // The text box can carry a set and a number; it cannot carry a UUID.
    // Without this, one hand-edit would downgrade every exact slot in the
    // deck to set-and-number only, the shortfall would change, and nothing
    // on screen would say why.
    const before = [
      { name: 'Sol Ring', qty: 1, printing_id: 'aaa', set_code: 'CMM',
        collector_number: '410' },
    ];
    const edited = parseDecklist(formatDecklist(before) + '\n1 Bolt').cards;
    assert.equal(edited[1].printing_id, undefined, 'the id did not survive the text');

    const healed = carryPrintings(edited, before);
    const ring = healed.find((e) => e.name === 'Sol Ring');
    assert.equal(ring.printing_id, 'aaa', 'and it was put back');
    assert.equal(healed.find((e) => e.name === 'Bolt').printing_id, undefined);
  });

  test('carrying printings never invents one for a name-only slot', () => {
    const healed = carryPrintings(
      [{ name: 'Sol Ring', qty: 1 }],
      [{ name: 'Sol Ring', qty: 1, printing_id: 'aaa', set_code: 'CMM' }],
    );
    assert.equal(healed[0].printing_id, undefined,
                 '"any printing" is a choice, not a gap to fill');
  });
});

describe('what you still need', () => {
  const deck = list({ 'Sol Ring': 1, 'Lightning Bolt': 4, 'Arcane Signet': 1 });

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
    const missing = shortfall(list({ 'Sol Ring': 3 }), [
      { card_name: 'Sol Ring', quantity: 1 },
      { card_name: 'Sol Ring', quantity: 1 },
    ]);
    assert.equal(missing[0].short, 1);
  });

  test('matching ignores case', () => {
    const missing = shortfall(list({ 'sol ring': 1 }), [
      { card_name: 'Sol Ring', quantity: 1 },
    ]);
    assert.equal(missing.length, 0);
  });

  test('a deck you can build reports nothing missing', () => {
    const missing = shortfall(list({ 'Sol Ring': 1 }), [
      { card_name: 'Sol Ring', quantity: 4 },
    ]);
    assert.deepEqual(missing, []);
  });

  test('the biggest gap comes first', () => {
    const missing = shortfall(list({ A: 4, B: 1 }), []);
    assert.equal(missing[0].name, 'A');
  });
});

describe('what you still need, when the slot names a printing', () => {
  test('a different printing does not fill an exact slot', () => {
    // The failure this exists to stop: you own the $16 basic, the deck asks
    // for the $50 full-art, and the app tells you the deck is finished.
    const missing = shortfall(
      [{ name: 'Sol Ring', qty: 1, printing_id: 'full-art' }],
      [{ card_name: 'Sol Ring', quantity: 1, printing_id: 'basic' }],
    );
    assert.equal(missing.length, 1);
    assert.equal(missing[0].short, 1);
    assert.equal(missing[0].printing_id, 'full-art', 'and it says which one');
  });

  test('the right printing does fill it', () => {
    const missing = shortfall(
      [{ name: 'Sol Ring', qty: 1, printing_id: 'full-art' }],
      [{ card_name: 'Sol Ring', quantity: 1, printing_id: 'full-art' }],
    );
    assert.deepEqual(missing, []);
  });

  test('a name-only slot is still filled by anything', () => {
    // The old default has to survive. An import from Moxfield is name-only
    // and must not suddenly report every card missing.
    const missing = shortfall(
      [{ name: 'Sol Ring', qty: 1 }],
      [{ card_name: 'Sol Ring', quantity: 1, printing_id: 'whatever' }],
    );
    assert.deepEqual(missing, []);
  });

  test('one physical card cannot fill two slots', () => {
    // The exact slot claims the copy first; the loose slot must then find
    // nothing left, or the deck reads as complete with a sleeve empty.
    const missing = shortfall(
      [
        { name: 'Sol Ring', qty: 1, printing_id: 'full-art' },
        { name: 'Sol Ring', qty: 1 },
      ],
      [{ card_name: 'Sol Ring', quantity: 1, printing_id: 'full-art' }],
    );
    assert.equal(missing.length, 1);
    assert.equal(missing[0].printing_id, undefined, 'the loose slot is the short one');
  });

  test('a set code with no id is matched by name, not guessed at', () => {
    // The phone's mirror holds printing ids and not set codes, so there is
    // genuinely nothing to compare. Admitting that beats inventing a match.
    const missing = shortfall(
      [{ name: 'Sol Ring', qty: 1, set_code: 'CMM', collector_number: '410' }],
      [{ card_name: 'Sol Ring', quantity: 1, printing_id: 'anything' }],
    );
    assert.deepEqual(missing, []);
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
      decklist: list({ 'Sol Ring': 1 }), notes: '',
      updated_at: '2026-08-23T10:00:00Z',
    });
    const read = await decks.get('d1');
    assert.equal(read.name, 'Shop brew');
    assert.deepEqual(counts(read.decklist), { 'Sol Ring': 1 });
  });

  test('a printing survives the store', async () => {
    const decks = await store();
    await decks.save({
      deck_id: 'd1', name: 'Pimped', format: 'commander',
      decklist: [{ name: 'Sol Ring', qty: 1, printing_id: 'aaa',
                   set_code: 'CMM', collector_number: '410' }],
      notes: '', updated_at: '2026-08-27T10:00:00Z',
    });
    const back = await decks.get('d1');
    assert.deepEqual(back.decklist, [{
      name: 'Sol Ring', qty: 1, printing_id: 'aaa',
      set_code: 'CMM', collector_number: '410',
    }]);
  });

  test('saving again replaces rather than duplicates', async () => {
    // A deck is a document: last write wins, because a half-merged decklist
    // is worse than a lost edit.
    const decks = await store();
    const deck = {
      deck_id: 'd1', name: 'First', format: '', decklist: [], notes: '',
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
      deck_id: 'd1', name: 'X', format: '', decklist: [], notes: '',
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
      decklist: list({ 'Sol Ring': 4 }), notes: '', updated_at: 'now',
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
    const deck = addToDeck([], 'Black Lotus');
    assert.equal(deck[0].name, 'Black Lotus');
    assert.equal(deck[0].qty, 1);
  });

  test('adding the same card again increases the count', () => {
    let deck = addToDeck([], 'Lightning Bolt', 2);
    deck = addToDeck(deck, 'Lightning Bolt', 2);
    assert.equal(deck.length, 1);
    assert.equal(deck[0].qty, 4);
  });

  test('the original list is not mutated', () => {
    const before = list({ 'Sol Ring': 1 });
    addToDeck(before, 'Black Lotus');
    assert.deepEqual(before, [{ name: 'Sol Ring', qty: 1 }]);
  });

  test('the entries in the original are not mutated either', () => {
    // A shallow copy of the array with the same objects in it would let an
    // edit reach back into React state, which is how a screen stops
    // re-rendering after a change it did make.
    const before = [{ name: 'Sol Ring', qty: 1 }];
    addToDeck(before, 'Sol Ring');
    assert.equal(before[0].qty, 1);
  });

  test('blank names and nonsense counts are ignored', () => {
    assert.deepEqual(addToDeck([], '   '), []);
    assert.deepEqual(addToDeck([], 'Sol Ring', 0), []);
  });

  test('removing takes copies off and then the line itself', () => {
    let deck = list({ 'Sol Ring': 2 });
    deck = removeFromDeck(deck, 'Sol Ring');
    assert.equal(deck[0].qty, 1);
    deck = removeFromDeck(deck, 'Sol Ring');
    assert.deepEqual(deck, [], 'a zero line is not kept');
  });

  test('removing a card that is not there changes nothing', () => {
    assert.deepEqual(removeFromDeck(list({ 'Sol Ring': 1 }), 'Black Lotus'),
                     [{ name: 'Sol Ring', qty: 1 }]);
  });

  test('a deck of entirely unowned cards reports all of them missing', () => {
    const deck = addToDeck(addToDeck([], 'Black Lotus'), 'Mox Jet');
    const missing = shortfall(deck, []);
    assert.equal(missing.length, 2);
  });
});

describe('what finishing a deck would cost', () => {
  test('only the copies you lack are counted', () => {
    const missing = shortfall(list({ 'Sol Ring': 4 }),
                              [{ card_name: 'Sol Ring', quantity: 1 }]);
    const cost = costToFinish(missing, { 'sol ring': 2 });
    assert.equal(cost.usd, 6, 'three missing at 2 each, not four');
  });

  test('an unknown price is admitted, not treated as free', () => {
    // A total that silently counts "unknown" as zero is worse than one that
    // says what it could not price.
    const missing = shortfall(list({ 'Black Lotus': 1, 'Sol Ring': 1 }), []);
    const cost = costToFinish(missing, { 'sol ring': 2 });
    assert.equal(cost.usd, 2);
    assert.equal(cost.unpriced, 1);
  });

  test('a deck you can already build costs nothing', () => {
    const cost = costToFinish([], {});
    assert.equal(cost.usd, 0);
    assert.equal(cost.unpriced, 0);
  });

  test('an exact slot is quoted at its own printing price', () => {
    const missing = shortfall(
      [{ name: 'Sol Ring', qty: 1, printing_id: 'full-art' }], [],
    );
    const cost = costToFinish(missing, { 'sol ring': 2, 'full-art': 50 });
    assert.equal(cost.usd, 50, 'not the cheap representative printing');
  });
});

describe('what the deck is actually worth', () => {
  test('two printings of one card are two different decks', () => {
    // Before slots could name a printing this was one number for both, and
    // that number was an estimate wearing a dollar sign.
    const prices = { 'sol ring': 16, 'full-art': 50, basic: 16 };
    const pimped = deckValue([{ name: 'Sol Ring', qty: 1, printing_id: 'full-art' }], prices);
    const plain = deckValue([{ name: 'Sol Ring', qty: 1, printing_id: 'basic' }], prices);
    assert.equal(pimped.usd, 50);
    assert.equal(plain.usd, 16);
  });

  test('a name-only slot falls back to the representative price', () => {
    const value = deckValue([{ name: 'Sol Ring', qty: 2 }], { 'sol ring': 1.5 });
    assert.equal(value.usd, 3);
  });

  test('what could not be priced is reported, not counted as free', () => {
    const value = deckValue(
      [{ name: 'Sol Ring', qty: 1 }, { name: 'Black Lotus', qty: 1 }],
      { 'sol ring': 2 },
    );
    assert.equal(value.usd, 2);
    assert.equal(value.unpriced, 1);
  });

  test('an empty deck is worth nothing and admits nothing', () => {
    assert.deepEqual(deckValue([], {}), { usd: 0, unpriced: 0 });
  });
});

describe('which picture a slot shows, and what it costs', () => {
  const owned = [
    { card_name: 'Sol Ring', printing_id: 'basic', price_usd: 16 },
    { card_name: 'Sol Ring', printing_id: 'full-art', price_usd: 50 },
  ];

  test('an exact slot shows its own printing', () => {
    const found = resolveSlots(
      [{ name: 'Sol Ring', qty: 1, printing_id: 'full-art' }], owned,
    );
    const facts = found[entryKey({ name: 'Sol Ring', printing_id: 'full-art' })];
    assert.equal(facts.printing_id, 'full-art');
    assert.equal(facts.price_usd, 50);
  });

  test('a name-only slot borrows a printing you own', () => {
    // With no signal this is the only picture available, and a card from your
    // own box beats a grey rectangle.
    const found = resolveSlots([{ name: 'Sol Ring', qty: 1 }], owned);
    assert.equal(found[entryKey({ name: 'Sol Ring' })].printing_id, 'basic');
  });

  test('a card you have never owned has nothing to show, offline', () => {
    const found = resolveSlots([{ name: 'Black Lotus', qty: 1 }], owned);
    assert.deepEqual(found, {});
  });

  test('the desktop fills in what the mirror could not', () => {
    const found = resolveSlots(
      [{ name: 'Black Lotus', qty: 1 }], owned,
      [{ printing_id: 'lea-232', set_code: 'LEA', collector_number: '232',
         price_usd: 45000, found: true }],
    );
    const facts = found[entryKey({ name: 'Black Lotus' })];
    assert.equal(facts.printing_id, 'lea-232');
    assert.equal(facts.set_code, 'LEA');
  });

  test('the desktop is matched by position, not by name', () => {
    // It answers with the catalogue's spelling, so matching on the name we
    // sent would silently miss every slot whose capitalisation differed.
    const found = resolveSlots(
      [{ name: 'sol ring', qty: 1 }], [],
      [{ printing_id: 'cmm-410', set_code: 'CMM', collector_number: '410',
         price_usd: 2, found: true }],
    );
    assert.equal(found[entryKey({ name: 'sol ring' })].printing_id, 'cmm-410');
  });

  test('a slot the catalogue could not place keeps what the mirror knew', () => {
    const found = resolveSlots(
      [{ name: 'Sol Ring', qty: 1 }], owned,
      [{ printing_id: '', set_code: '', collector_number: '', found: false }],
    );
    assert.equal(found[entryKey({ name: 'Sol Ring' })].printing_id, 'basic');
  });

  test('a price you paid survives a desktop that has none', () => {
    const found = resolveSlots(
      [{ name: 'Sol Ring', qty: 1, printing_id: 'full-art' }], owned,
      [{ printing_id: 'full-art', set_code: 'CMM', collector_number: '410',
         price_usd: null, found: true }],
    );
    assert.equal(found[entryKey({ name: 'Sol Ring', printing_id: 'full-art' })]
                   .price_usd, 50);
  });

  test('prices come out in the shape the value maths wants', () => {
    const entries = [
      { name: 'Sol Ring', qty: 1, printing_id: 'full-art' },
      { name: 'Sol Ring', qty: 1 },
    ];
    const prices = pricesFromSlots(entries, resolveSlots(entries, owned));
    assert.equal(prices['full-art'], 50, 'the exact slot keys on its printing');
    assert.equal(prices['sol ring'], 16, 'the loose slot keys on the name');
    assert.equal(deckValue(entries, prices).usd, 66);
  });
});

describe('the wishlist', () => {
  const deck = (id, name, decklist) => ({
    deck_id: id, name, format: '',
    decklist: Array.isArray(decklist) ? decklist : list(decklist),
    notes: '', updated_at: 'now',
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

  test('two decks wanting two printings are two purchases', () => {
    // Collapsing them would send you home with one card that finishes one
    // deck and leaves the other still short, with nothing to say so.
    const rows = wishlistFromDecks([
      deck('d1', 'Pimped', [{ name: 'Sol Ring', qty: 1, printing_id: 'full-art',
                              set_code: 'CMM', collector_number: '410' }]),
      deck('d2', 'Budget', [{ name: 'Sol Ring', qty: 1, printing_id: 'basic' }]),
    ], []);
    assert.equal(rows.length, 2);
    assert.deepEqual(rows.map((r) => r.printing_id).sort(), ['basic', 'full-art']);
    assert.equal(rows.find((r) => r.printing_id === 'full-art').set_code, 'CMM');
  });

  test('the board is wanted too', () => {
    // Those cards get bought and carried like any other. A wishlist from the
    // maindeck alone would tell you to buy none of them.
    const rows = wishlistFromDecks([{
      deck_id: 'd1', name: 'Burn', format: '',
      decklist: list({ Bolt: 4 }), sideboard: list({ Pyroblast: 2 }),
      notes: '', updated_at: 'now',
    }], []);
    assert.equal(rows.find((r) => r.card_name === 'Pyroblast').quantity, 2);
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
    assert.deepEqual(counts(cards), { 'Lightning Bolt': 4 });
    assert.deepEqual(counts(sideboard), { Pyroblast: 2 });
  });

  test('the abbreviations the exporters actually emit', async () => {
    for (const header of ['Sideboard', 'SIDEBOARD', 'sb', 'Side:']) {
      const { sideboard } = parseDecklist(`4 Bolt\n${header}\n1 Pyroblast`);
      assert.deepEqual(counts(sideboard), { Pyroblast: 1 }, header);
    }
  });

  test('a later Deck header goes back to the maindeck', async () => {
    const { cards, sideboard } = parseDecklist(
      'Sideboard\n1 Pyroblast\nDeck\n4 Bolt',
    );
    assert.deepEqual(counts(sideboard), { Pyroblast: 1 });
    assert.deepEqual(counts(cards), { Bolt: 4 });
  });

  test('a card can be in both, and the counts stay apart', async () => {
    // Three in the deck and one in the board is a real and common shape.
    const { cards, sideboard } = parseDecklist(
      '3 Bolt\nSideboard\n1 Bolt',
    );
    assert.equal(counts(cards).Bolt, 3);
    assert.equal(counts(sideboard).Bolt, 1);
  });

  test('a deck with no board is unchanged', async () => {
    const { cards, sideboard } = parseDecklist('4 Bolt');
    assert.deepEqual(counts(cards), { Bolt: 4 });
    assert.deepEqual(sideboard, []);
  });

  test('formatting writes the header back out', async () => {
    // Without it, one round trip through the text box moves the board into
    // the deck and nothing says so.
    const text = formatDecklist(list({ Bolt: 4 }), list({ Pyroblast: 2 }));
    assert.match(text, /Sideboard/);
    const again = parseDecklist(text);
    assert.deepEqual(counts(again.sideboard), { Pyroblast: 2 });
    assert.deepEqual(counts(again.cards), { Bolt: 4 });
  });

  test('formatting a deck with no board adds no header', async () => {
    assert.equal(formatDecklist(list({ Bolt: 4 })), '4 Bolt');
  });

  test('what you still need counts the board as well', async () => {
    // Those cards get bought and carried like any other. A shortfall from
    // the maindeck alone would tell you to buy none of them.
    assert.deepEqual(
      counts(mergeCounts(list({ Bolt: 3 }), list({ Bolt: 1, Pyroblast: 2 }))),
      { Bolt: 4, Pyroblast: 2 },
    );
  });

  test('merging keeps two printings apart', async () => {
    const merged = mergeCounts(
      [{ name: 'Bolt', qty: 3, printing_id: 'a' }],
      [{ name: 'Bolt', qty: 1, printing_id: 'b' }],
    );
    assert.equal(merged.length, 2);
    assert.equal(deckSize(merged), 4);
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
      decklist: list({ Bolt: 4 }),
      sideboard: list({ Pyroblast: 2 }),
      notes: '',
      updated_at: new Date(0).toISOString(),
    });
    const back = await store.get('d1');
    assert.deepEqual(counts(back.decklist), { Bolt: 4 });
    assert.deepEqual(counts(back.sideboard), { Pyroblast: 2 });
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
    assert.deepEqual(counts(back.decklist), { Bolt: 4 });
    assert.deepEqual(back.sideboard, []);
  });

  test('a deck saved before printings existed still reads, board and all', async () => {
    // This is the migration test, and its equivalent caught the last one
    // going wrong. There are real decks on the user's phone in this shape.
    const db = new MemoryDatabase();
    await db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      ['mid', 'Sideboarded', 'modern',
       '{"main":{"Bolt":4},"side":{"Pyroblast":2}}', '',
       new Date(0).toISOString()],
    );
    const back = await new DeckStore(db).get('mid');
    assert.deepEqual(counts(back.decklist), { Bolt: 4 });
    assert.deepEqual(counts(back.sideboard), { Pyroblast: 2 });
    assert.equal(back.decklist[0].printing_id, undefined,
                 'and an old deck means "any printing", not a missing one');
  });

  test('an old deck reopened and resaved keeps its cards', async () => {
    const db = new MemoryDatabase();
    await db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      ['old', 'Legacy deck', 'modern', '{"Bolt":4}', '', new Date(0).toISOString()],
    );
    const store = new DeckStore(db);
    const back = await store.get('old');
    await store.save({ ...back, updated_at: new Date(1).toISOString() });
    assert.deepEqual(counts((await store.get('old')).decklist), { Bolt: 4 });
  });

  test('unreadable json is an empty deck, not a crash', async () => {
    const db = new MemoryDatabase();
    await db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      ['bad', 'Broken', '', '{not json', '', new Date(0).toISOString()],
    );
    const back = await new DeckStore(db).get('bad');
    assert.deepEqual(back.decklist, []);
  });

  test('an entry with no name or a nonsense count is dropped, not kept', async () => {
    const db = new MemoryDatabase();
    await db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
      ['odd', 'Odd', '',
       '{"v":2,"main":[{"name":"Bolt","qty":4},{"name":"","qty":2},' +
       '{"name":"X","qty":0},null]}', '', new Date(0).toISOString()],
    );
    const back = await new DeckStore(db).get('odd');
    assert.deepEqual(counts(back.decklist), { Bolt: 4 });
  });
});

describe('over the line, but not stopped at it', () => {
  test('a fifth copy is allowed and flagged', async () => {
    // Half of deckbuilding is holding a pile that is not legal yet.
    const over = addToDeck(list({ Bolt: 4 }), 'Bolt');
    assert.equal(counts(over).Bolt, 5);
    const said = deckWarnings(over, [], 'modern');
    assert.ok(said.some((w) => w.kind === 'copies' && /Bolt/.test(w.text)));
  });

  test('four printings of one card is still four copies', async () => {
    // Counting slots rather than cards would call an illegal deck legal the
    // moment someone picked their favourite art for one of them.
    const five = ['a', 'b', 'c', 'd', 'e'].map((id) => ({
      name: 'Bolt', qty: 1, printing_id: id,
    }));
    assert.ok(deckWarnings(five, [], 'modern').some((w) => w.kind === 'copies'));
  });

  test('basic lands are never over', async () => {
    assert.deepEqual(
      deckWarnings(list({ Mountain: 24 }), [], 'modern')
        .filter((w) => w.kind === 'copies'),
      [],
    );
  });

  test('the cards that say so on themselves are not over either', async () => {
    assert.deepEqual(
      deckWarnings(list({ 'Relentless Rats': 30 }), [], 'modern')
        .filter((w) => w.kind === 'copies'),
      [],
    );
  });

  test('commander is singleton, so a second copy is flagged', async () => {
    assert.ok(
      deckWarnings(list({ 'Sol Ring': 2 }), [], 'commander')
        .some((w) => w.kind === 'copies'),
    );
  });

  test('two printings of one card break singleton too', async () => {
    assert.ok(
      deckWarnings(
        [{ name: 'Sol Ring', qty: 1, printing_id: 'a' },
         { name: 'Sol Ring', qty: 1, printing_id: 'b' }],
        [], 'commander',
      ).some((w) => w.kind === 'copies'),
    );
  });

  test('deck and board are counted together for the copy limit', async () => {
    // Three in the deck and two in the board is five copies you own.
    assert.ok(
      deckWarnings(list({ Bolt: 3 }), list({ Bolt: 2 }), 'modern')
        .some((w) => w.kind === 'copies'),
    );
  });

  test('an oversized sideboard is flagged', async () => {
    const board = list(Object.fromEntries(
      Array.from({ length: 16 }, (_, i) => [`Card ${i}`, 1]),
    ));
    assert.ok(deckWarnings([], board, 'modern').some((w) => w.kind === 'sideboard'));
  });

  test('an empty deck is not nagged about its size', async () => {
    // Warning that a deck you have not started is too small is noise.
    assert.deepEqual(deckWarnings([], [], 'modern'), []);
  });

  test('a legal deck says nothing at all', async () => {
    const legal = list(Object.fromEntries(
      Array.from({ length: 15 }, (_, i) => [`Card ${i}`, 4]),
    ));
    assert.deepEqual(deckWarnings(legal, [], 'modern'), []);
  });
});
