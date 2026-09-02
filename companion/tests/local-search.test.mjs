/**
 * Finding a card you do not own, with no PC.
 *
 * Reported from a standalone phone with the index downloaded: it would not
 * turn up cards for a deck. The name search worked; every OTHER filter the
 * browser offers — colours, types, rarities, sets — returned nothing at
 * all, because the local fallback only understood a name. A filter with
 * nothing behind it looks exactly like an app refusing to find cards.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { coloursMatch, searchLocally, typesMatch } from '../src/lib/local-search.ts';

const ORACLE = [
  { oracle_id: 'o-sol', name: 'Sol Ring', type_line: 'Artifact',
    oracle_text: 'Add two.', mana_cost: '{1}', cmc: 1, color_identity: '' },
  { oracle_id: 'o-bolt', name: 'Lightning Bolt', type_line: 'Instant',
    oracle_text: 'Deal 3.', mana_cost: '{R}', cmc: 1, color_identity: 'R' },
  { oracle_id: 'o-nissa', name: 'Nissa, Worldsoul Speaker',
    type_line: 'Legendary Creature — Elf Druid', oracle_text: 'Landfall.',
    mana_cost: '{3}{G}', cmc: 4, color_identity: 'G' },
  { oracle_id: 'o-mix', name: 'Golden Mixer',
    type_line: 'Creature — Human', oracle_text: '', mana_cost: '{R}{G}',
    cmc: 2, color_identity: 'RG' },
];

const PRINTINGS = [
  { printing_id: 'p-sol', name: 'Sol Ring', set_code: 'cmm',
    collector_number: '410', cmc: 1, rarity: 'uncommon' },
  { printing_id: 'p-bolt', name: 'Lightning Bolt', set_code: 'lea',
    collector_number: '161', cmc: 1, rarity: 'common' },
  { printing_id: 'p-bolt2', name: 'Lightning Bolt', set_code: 'm10',
    collector_number: '146', cmc: 1, rarity: 'rare' },
  { printing_id: 'p-nissa', name: 'Nissa, Worldsoul Speaker', set_code: 'drc',
    collector_number: '13', cmc: 4, rarity: 'mythic' },
  { printing_id: 'p-mix', name: 'Golden Mixer', set_code: 'cmm',
    collector_number: '7', cmc: 2, rarity: 'common' },
];

const inputs = (owned = []) => ({
  oracle: ORACLE,
  printings: PRINTINGS,
  owned: new Set(owned.map((n) => n.toLowerCase())),
});

const names = (rows) => rows.map((r) => r.name);

describe('searching by name still works', () => {
  test('a partial name finds the card', () => {
    assert.deepEqual(names(searchLocally({ name: 'Sol' }, inputs())),
      ['Sol Ring']);
  });

  test('and a card that STARTS with it comes first', () => {
    // "Bolt" is in the middle of Lightning Bolt; a card actually called
    // Bolt-something would be what you meant.
    const rows = searchLocally({ name: 'light' }, inputs());
    assert.equal(rows[0].name, 'Lightning Bolt');
  });
});

describe('the filters that used to find nothing', () => {
  test('by type', () => {
    assert.deepEqual(names(searchLocally({ types: ['Instant'] }, inputs())),
      ['Lightning Bolt']);
  });

  test('by a type buried in a longer line', () => {
    // "Legendary Creature — Elf Druid" is prose, not a field.
    assert.deepEqual(
      names(searchLocally({ types: ['Elf'] }, inputs())),
      ['Nissa, Worldsoul Speaker']);
  });

  test('by rarity', () => {
    assert.deepEqual(names(searchLocally({ rarities: ['mythic'] }, inputs())),
      ['Nissa, Worldsoul Speaker']);
  });

  test('by set', () => {
    assert.deepEqual(
      names(searchLocally({ set_codes: ['drc'] }, inputs())),
      ['Nissa, Worldsoul Speaker']);
  });

  test('by mana value', () => {
    assert.deepEqual(names(searchLocally({ cmc_min: 4 }, inputs())),
      ['Nissa, Worldsoul Speaker']);
  });

  test('and several at once', () => {
    assert.deepEqual(
      names(searchLocally({ types: ['Creature'], colors: ['G'] }, inputs())),
      ['Nissa, Worldsoul Speaker']);
  });
});

describe('colours, all three ways of asking', () => {
  test('identity keeps what a deck could play', () => {
    // Colourless fits every deck, which is why Sol Ring is here.
    assert.deepEqual(
      names(searchLocally({ colors: ['G'] }, inputs())),
      ['Nissa, Worldsoul Speaker', 'Sol Ring']);
  });

  test('exact wants that combination and no other', () => {
    assert.deepEqual(
      names(searchLocally(
        { colors: ['R', 'G'], color_match: 'exact' }, inputs())),
      ['Golden Mixer']);
  });

  test('any touches at least one of them', () => {
    assert.deepEqual(
      names(searchLocally({ colors: ['R'], color_match: 'any' }, inputs())),
      ['Golden Mixer', 'Lightning Bolt']);
  });

  test('a colourless card is in every identity', () => {
    assert.equal(coloursMatch(ORACLE[0], ['W', 'U'], 'identity'), true);
  });

  test('but not in "any", which asks for a colour', () => {
    assert.equal(coloursMatch(ORACLE[0], ['W'], 'any'), false);
  });

  test('no colours chosen is not a filter', () => {
    assert.equal(coloursMatch(ORACLE[1], []), true);
  });
});

describe('the point of the whole thing', () => {
  test('cards you do NOT own are found', () => {
    // The complaint, exactly: building a deck means finding cards you have
    // not got, and a search that only knew what you owned would be a
    // collection browser wearing a catalogue's clothes.
    const rows = searchLocally({ types: ['Creature'] }, inputs(['Sol Ring']));
    assert.ok(names(rows).includes('Nissa, Worldsoul Speaker'));
  });

  test('and can still be narrowed to what you own when asked', () => {
    const rows = searchLocally({ owned: true }, inputs(['Sol Ring']));
    assert.deepEqual(names(rows), ['Sol Ring']);
  });
});

describe('what a result carries', () => {
  test('enough to put in a deck and show a picture', () => {
    const [card] = searchLocally({ name: 'Nissa' }, inputs());
    assert.equal(card.type_line, 'Legendary Creature — Elf Druid');
    assert.equal(card.cmc, 4);
    assert.deepEqual(card.color_identity, ['G']);
    assert.ok(card.printing_id, 'no printing to fetch art by');
  });

  test('the printing shown is one that MATCHED the filter', () => {
    // Lightning Bolt is common in LEA and rare in M10. Asked for rares,
    // showing the LEA art would be showing a card that fails the filter.
    const [card] = searchLocally({ rarities: ['rare'] }, inputs());
    assert.equal(card.set_code, 'm10');
    assert.equal(card.rarity, 'rare');
  });

  test('a limit is honoured', () => {
    assert.equal(searchLocally({ types: ['a'] }, inputs(), 2).length, 2);
  });

  test('and asking for nothing returns nothing, not everything', () => {
    // A blank query answered with the whole catalogue is thirty-four
    // thousand rows to render.
    assert.deepEqual(searchLocally({}, inputs()), []);
  });
});

describe('the two index sources spell colours differently', () => {
  /**
   * Both are already in the wild: the desktop sends the JSON it holds,
   * `["U", "W"]`, and the Scryfall path joins them into `UW`. Read as one
   * colour called "UW", every colour filter came back empty.
   */
  const desktopStyle = {
    oracle_id: 'o-j', name: 'Json Card', type_line: 'Creature',
    oracle_text: '', mana_cost: '{U}{W}', cmc: 2,
    color_identity: '["U", "W"]',
  };
  const scryfallStyle = { ...desktopStyle, oracle_id: 'o-s',
    name: 'Joined Card', color_identity: 'UW' };

  test('the desktop spelling is understood', () => {
    assert.equal(coloursMatch(desktopStyle, ['U', 'W'], 'exact'), true);
  });

  test('and so is the Scryfall one', () => {
    assert.equal(coloursMatch(scryfallStyle, ['U', 'W'], 'exact'), true);
  });

  test('they agree with each other', () => {
    for (const mode of ['identity', 'exact', 'any']) {
      assert.equal(
        coloursMatch(desktopStyle, ['U', 'W'], mode),
        coloursMatch(scryfallStyle, ['U', 'W'], mode),
        mode);
    }
  });

  test('and neither is mistaken for a colour it is not', () => {
    assert.equal(coloursMatch(desktopStyle, ['G'], 'identity'), false);
    assert.equal(coloursMatch(scryfallStyle, ['G'], 'identity'), false);
  });
});

describe('type matching', () => {
  test('is case-insensitive, because a type line is prose', () => {
    assert.equal(typesMatch(ORACLE[2], ['creature']), true);
  });

  test('and no types chosen is not a filter', () => {
    assert.equal(typesMatch(ORACLE[0], []), true);
  });
});

describe('the fields the browser actually sends', () => {
  /**
   * Reported with a screenshot: typing "Vol" with the Creature filter on
   * returned sixty cards whose names start with underscores — Un-set
   * cards, which sort first alphabetically. That is not a search for
   * "Vol"; it is every creature in the catalogue.
   *
   * The browser sends what you typed as `anywhere`, ownership as the word
   * `owned`, and fetches art by `scryfall_id`. The local search read
   * `name`, `owned` and set `scryfall_id` to empty — so the term was
   * ignored, the ownership filter was ignored, and every result was a
   * grey rectangle.
   */
  test('the typed term arrives as `anywhere`, and is used', () => {
    assert.deepEqual(names(searchLocally({ anywhere: 'Sol' }, inputs())),
      ['Sol Ring']);
  });

  test('a term plus a type does not return the whole type', () => {
    // The exact shape of the screenshot.
    const rows = searchLocally(
      { anywhere: 'Nissa', types: ['Creature'] }, inputs());
    assert.deepEqual(names(rows), ['Nissa, Worldsoul Speaker']);
  });

  test('`anywhere` also reaches the type line and rules text', () => {
    // That is what the word means, and it is why the desktop is still
    // asked first — it searches more than this.
    assert.ok(names(searchLocally({ anywhere: 'landfall' }, inputs()))
      .includes('Nissa, Worldsoul Speaker'));
  });

  test('but a name match still comes first', () => {
    // Searching "artifact" should offer a card CALLED that before every
    // card that merely is one.
    const rows = searchLocally({ anywhere: 'sol' }, {
      ...inputs(),
      oracle: [
        { oracle_id: 'o-x', name: 'Not Named', type_line: 'Artifact',
          oracle_text: 'Solve the puzzle.', mana_cost: '', cmc: 0,
          color_identity: '' },
        ...ORACLE,
      ],
    });
    assert.equal(rows[0].name, 'Sol Ring');
  });

  test('`name` stays name-only, and does not match rules text', () => {
    assert.deepEqual(names(searchLocally({ name: 'landfall' }, inputs())), []);
  });

  test('ownership arrives as a word, not a flag', () => {
    assert.deepEqual(
      names(searchLocally({ ownership: 'owned' }, inputs(['Sol Ring']))),
      ['Sol Ring']);
  });

  test('every result carries an id the art can be fetched by', () => {
    // The browser loads art from `scryfall_id`, and for a printing that
    // id IS the printing id. Blank means a grey rectangle.
    for (const card of searchLocally({ anywhere: 'o' }, inputs())) {
      assert.ok(card.scryfall_id, `${card.name} has no art id`);
      assert.equal(card.scryfall_id, card.printing_id);
    }
  });
});
