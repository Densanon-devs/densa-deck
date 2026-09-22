/**
 * Whether the card in your hand is the shiny one.
 *
 * Reported as "there are several cards I have that don't show a foil
 * option but the card is in fact foil". There was no foil option
 * anywhere: the scanner decided it from a star in the collector line,
 * the star is the one glyph OCR reliably loses — the matcher's own
 * comment says "measured against Windows OCR the star NEVER comes
 * back" — and no screen let anyone disagree.
 *
 * A foil filed as an ordinary copy is not cosmetic. On a card whose
 * foil is worth ten times its nonfoil, it is most of what the
 * collection is worth.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  finishToFile,
  finishesOf,
  foilExists,
  noFoilBecause,
  nonfoilExists,
} from '../src/lib/finishes.ts';

describe('reading what the index stored', () => {
  test('the ordinary pair', () => {
    assert.deepEqual(finishesOf('nonfoil,foil'), ['nonfoil', 'foil']);
  });

  test('a card with no foil', () => {
    assert.deepEqual(finishesOf('nonfoil'), ['nonfoil']);
  });

  test('etched, which is a third thing', () => {
    assert.deepEqual(finishesOf('nonfoil,foil,etched'),
      ['nonfoil', 'foil', 'etched']);
  });

  test('nothing recorded is nothing, not a blank entry', () => {
    assert.deepEqual(finishesOf(''), []);
    assert.deepEqual(finishesOf(null), []);
    assert.deepEqual(finishesOf(undefined), []);
  });
});

describe('whether a foil exists', () => {
  test('when the index says so', () => {
    assert.equal(foilExists('nonfoil,foil'), true);
  });

  test('and when it says the opposite', () => {
    // An Alpha Island. Recording a foil would invent a card.
    assert.equal(foilExists('nonfoil'), false);
    assert.equal(nonfoilExists('nonfoil'), true);
  });

  test('etched alone counts as a foil', () => {
    assert.equal(foilExists('etched'), true);
  });

  test('a foil-only printing has no nonfoil', () => {
    assert.equal(nonfoilExists('foil'), false);
  });

  test('NOT KNOWING is not the same as no', () => {
    /*
      The whole point. An index downloaded before this column existed
      has nothing here, and reading that as "there is no foil" would
      hide the switch on every card until somebody re-downloaded
      78 MB — the same silence in a new coat, and the assumption that
      caused this in the first place.
    */
    assert.equal(foilExists(''), true);
    assert.equal(foilExists(null), true);
    assert.equal(nonfoilExists(''), true);
  });
});

describe('what actually gets filed', () => {
  test('what you asked for, when the printing allows it', () => {
    assert.equal(finishToFile('nonfoil,foil', true), 'foil');
    assert.equal(finishToFile('nonfoil,foil', false), 'nonfoil');
  });

  test('your choice wins over an unknown printing', () => {
    // You are holding the card and the index is not.
    assert.equal(finishToFile('', true), 'foil');
    assert.equal(finishToFile(null, true), 'foil');
  });

  test('but not over a printing that positively has no foil', () => {
    // The one case where the app knows better: recording a foil
    // Alpha Island would invent a card that does not exist.
    assert.equal(finishToFile('nonfoil', true), 'nonfoil');
  });

  test('etched is what you get when it is the only shiny one', () => {
    assert.equal(finishToFile('nonfoil,etched', true), 'etched');
  });

  test('a foil-only printing files as foil even unasked', () => {
    assert.equal(finishToFile('foil', false), 'foil');
  });
});

describe('saying why, rather than greying out in silence', () => {
  test('a printing with no foil explains itself', () => {
    assert.match(noFoilBecause('nonfoil'), /never made in foil/);
  });

  test('and one with a foil says nothing', () => {
    assert.equal(noFoilBecause('nonfoil,foil'), '');
  });

  test('nor does one the index knows nothing about', () => {
    assert.equal(noFoilBecause(''), '');
  });
});
