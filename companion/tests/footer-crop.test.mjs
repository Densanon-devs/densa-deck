/**
 * The second look at the bottom of the card.
 *
 * Five real scans in a row failed the same way: the name read
 * perfectly, the artist and copyright line read perfectly, and the set
 * code — which sits between them, in the same six-point type — did not.
 * The app could see there was a card and could not say which one.
 *
 * These pin when the extra pass happens and what it looks at. The cost
 * of getting "when" wrong is a second OCR on every idle frame of auto
 * scan; the cost of getting "what" wrong is cropping away the only line
 * it exists to read.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import {
  FOOTER_FRACTION,
  enlargedWidth,
  footerRegion,
  needsCloserLook,
} from '../src/lib/footer-crop.ts';

describe('which part of the picture to look at again', () => {
  test('the bottom strip, full width', () => {
    const r = footerRegion(1000, 2000);
    assert.equal(r.originX, 0);
    assert.equal(r.width, 1000, 'full width');
    assert.equal(r.height, Math.round(2000 * FOOTER_FRACTION));
    assert.equal(r.originY, 2000 - r.height, 'anchored to the bottom');
  });

  test('full width rather than the corner the set code sits in', () => {
    // A card photographed at an angle puts its bottom-left a long way
    // from the picture's bottom-left, and the collector number over on
    // the right has to come too: a set code with no number is not a key.
    const r = footerRegion(1200, 1600);
    assert.equal(r.width, 1200);
  });

  test('the strip stays inside the picture', () => {
    const r = footerRegion(100, 100);
    assert.ok(r.originY >= 0);
    assert.ok(r.originY + r.height <= 100);
  });

  test('a picture with no size is not cropped', () => {
    // The manipulator reports dimensions and can fail to; guessing a
    // region from zero would crop to nothing and read nothing.
    assert.equal(footerRegion(0, 1000), null);
    assert.equal(footerRegion(1000, 0), null);
  });
});

describe('how much to enlarge it', () => {
  test('doubled, because six-point type needs pixels to have a shape', () => {
    assert.equal(enlargedWidth(800), 1600);
  });

  test('but capped, so a big photo does not grow without limit', () => {
    assert.equal(enlargedWidth(4000), 2600);
  });

  test('and nothing to enlarge stays nothing', () => {
    assert.equal(enlargedWidth(0), 0);
  });
});

describe('when a second look is worth taking', () => {
  test('not when the first read already found a key', () => {
    // The common path must pay nothing for this.
    assert.equal(needsCloserLook('MKM 200', [['mkm', '200']]), false);
  });

  test('yes when text came back but no key in it', () => {
    // The exact failure: artist and copyright read, set code missed.
    assert.equal(
      needsCloserLook('Illus. Mark Zug 2023 Wizards of the Coast 310', []),
      true);
  });

  test('not when nothing was read at all', () => {
    // An empty frame during auto scan, which is most frames. Enlarging
    // a blank strip finds nothing and costs a pass every second.
    assert.equal(needsCloserLook('', []), false);
    assert.equal(needsCloserLook('   \n  ', []), false);
  });
});
