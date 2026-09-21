/**
 * Set symbols you can actually see.
 *
 * Scryfall draws them for paper and for white web pages, so they are
 * black. On the pick list that is a black shape on a black background:
 * present, occupying space, unreadable.
 *
 * The shapes below are copied from real Scryfall files, not invented.
 * All three occur, and a single find-and-replace handles exactly one
 * of them.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { isColoured, rarityColour, recolourSvg }
  from '../src/lib/set-symbol.ts';

const WHITE = '#e4e6eb';

// Guilds of Ravnica, and most other sets.
const BLACK_FILL =
  '<svg viewBox="0 0 238 156" xmlns="http://www.w3.org/2000/svg">'
  + '<path d="M118 0l6 12z" fill="#000"/></svg>';

// Zendikar Rising Commander, Archenemy: Nicol Bolas, Murders at Karlov
// Manor. No fill anywhere: every path takes SVG's default black.
const NO_FILL =
  '<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">'
  + '<path d="M1 1h30v30z"/></svg>';

// The List. The multi-colour Magic logo.
const COLOURED =
  '<svg viewBox="0 0 64 64"><path fill="none" d="M0 0h64v64z"/>'
  + '<path fill="#0055DC" d="M1 1z"/><path fill="white" d="M2 2z"/></svg>';

describe('recolouring a set symbol', () => {
  test('a black fill becomes the colour asked for', () => {
    const out = recolourSvg(BLACK_FILL, WHITE);
    assert.match(out, new RegExp(`fill="${WHITE}"`));
    assert.doesNotMatch(out, /fill="#000"/);
  });

  test('a symbol with NO fill gets one on the root to inherit', () => {
    // The case a find-and-replace cannot touch, and a third of the
    // ones checked were like this. There is no black to swap: the
    // black is SVG's default.
    const out = recolourSvg(NO_FILL, WHITE);
    assert.match(out, new RegExp(`<svg fill="${WHITE}"`));
    assert.match(out, /viewBox/, 'the rest of the root survives');
  });

  test('a coloured logo is left exactly as it is', () => {
    // The List's symbol. Painting it white erases it.
    assert.equal(recolourSvg(COLOURED, WHITE), COLOURED);
  });

  test('and "none" stays none, because it is a hole not a colour', () => {
    // Filling it in turns the shape into a blob.
    const holed = '<svg><path fill="none" d="z"/><path fill="#000" d="z"/></svg>';
    const out = recolourSvg(holed, WHITE);
    assert.match(out, /fill="none"/);
    assert.match(out, new RegExp(`fill="${WHITE}"`));
  });

  test('nothing recognisable comes back untouched', () => {
    // A symbol in the wrong colour beats no symbol, and both beat a
    // crash in a list somebody is reading.
    assert.equal(recolourSvg('', WHITE), '');
    assert.equal(recolourSvg('not markup', WHITE), 'not markup');
  });

  test('black in any of its spellings is recognised', () => {
    for (const black of ['#000', '#000000', 'black', '#010101']) {
      const out = recolourSvg(`<svg><path fill="${black}"/></svg>`, WHITE);
      assert.match(out, new RegExp(`fill="${WHITE}"`), black);
    }
  });
});

describe('telling a coloured mark from a black one', () => {
  test('black is not coloured', () => {
    assert.equal(isColoured(BLACK_FILL), false);
  });

  test('no fill at all is not coloured', () => {
    assert.equal(isColoured(NO_FILL), false);
  });

  test('a palette is', () => {
    assert.equal(isColoured(COLOURED), true);
  });
});

describe('the colour a set symbol is printed in', () => {
  /**
   * Scryfall's symbol file is a single-colour silhouette and carries
   * no rarity, so whitening them lost nothing -- but it also threw
   * away something the phone already knows. The index stores a rarity
   * per printing, and on a real card the symbol's colour IS its
   * rarity. The pick list can show what the card shows.
   */
  test('the four rarities are four different colours', () => {
    const seen = ['mythic', 'rare', 'uncommon', 'common'].map(rarityColour);
    assert.equal(new Set(seen).size, 4, 'each has to be distinguishable');
  });

  test('special frames get their own colour too', () => {
    // 392 printings in the real catalogue, so not hypothetical.
    assert.notEqual(rarityColour('special'), rarityColour('rare'));
    assert.notEqual(rarityColour('special'), rarityColour('common'));
  });

  test('common is light, never black', () => {
    // It is printed black, and black on this screen is the unreadable
    // symbol the whole module exists to fix.
    const out = rarityColour('common').toLowerCase();
    assert.notEqual(out, '#000');
    assert.notEqual(out, '#000000');
    // Bright enough to read on a near-black background.
    const n = parseInt(out.slice(1), 16);
    const lum = ((n >> 16) & 255) * 0.299 + ((n >> 8) & 255) * 0.587
      + (n & 255) * 0.114;
    assert.ok(lum > 140, `too dark to read: ${out} (luminance ${lum})`);
  });

  test('an unknown or missing rarity falls back rather than vanishing', () => {
    // A phone whose index predates the rarity column has '' here, and
    // an invisible symbol would be worse than an uncoloured one.
    assert.equal(rarityColour(''), rarityColour('common'));
    assert.equal(rarityColour('nonsense'), rarityColour('common'));
  });

  test('case and padding do not decide it', () => {
    assert.equal(rarityColour('  Mythic '), rarityColour('mythic'));
  });
});
