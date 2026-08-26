/**
 * Card art on a phone that may have no signal.
 *
 * Two separate things get confused here, so they are stated plainly:
 *
 *   * **Hosting** card images is out. The URLs point at Scryfall's CDN and
 *     nothing is ever served from us or bundled into a build.
 *   * **Caching** them is in, and is what Scryfall's own guidelines ask of
 *     clients: keep what you already fetched rather than requesting it again.
 *     That cache is the phone's own and never leaves it.
 *
 * The URL half is checked against Scryfall's documented layout — verified
 * against the live CDN when it was written: 200, image/jpeg. The prefetch half
 * is checked for the things that make a background download rude or fragile.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  ART_HEADERS,
  USER_AGENT,
  artQueue,
  artSource,
  cardImageUrl,
  checkArtReachable,
  scryfallPageUrl,
} from '../src/lib/images.ts';

// Death Wind, DTK #95 — a real printing id.
const DEATH_WIND = '87ed0a14-1a98-4190-b195-f84fa42d4364';

describe('where the art comes from', () => {
  test('it matches the CDN layout', () => {
    // Two directory levels from the first two characters of the id. Getting
    // this wrong 404s every card at once, which at least fails loudly.
    assert.equal(
      cardImageUrl(DEATH_WIND),
      'https://cards.scryfall.io/normal/front/8/7/' +
        '87ed0a14-1a98-4190-b195-f84fa42d4364.jpg',
    );
  });

  test('the thumbnail and the full card are different sizes of the same thing', () => {
    assert.ok(cardImageUrl(DEATH_WIND, 'small').includes('/small/'));
    assert.ok(cardImageUrl(DEATH_WIND, 'png').endsWith('.png'));
  });

  test('an uppercase id still resolves', () => {
    // The CDN paths are lowercase; a capitalised id would 404 for that one
    // card and nobody would work out why.
    assert.equal(cardImageUrl(DEATH_WIND.toUpperCase()), cardImageUrl(DEATH_WIND));
  });

  test('nothing sensible gives no URL rather than a broken one', () => {
    // `.../n/o/none.jpg` renders as a broken image with no explanation. An
    // empty string lets the screen say "no art for this printing".
    for (const bad of ['', '   ', 'none', 'Death Wind', undefined, null, 42]) {
      assert.equal(cardImageUrl(bad), '', String(bad));
    }
  });

  test('it links out to Scryfall for rulings', () => {
    assert.equal(scryfallPageUrl(DEATH_WIND), `https://scryfall.com/card/${DEATH_WIND}`);
    assert.equal(scryfallPageUrl('nope'), '');
  });

  test('every URL is Scryfall and nothing else', () => {
    // The whole licence position in one assertion: if these ever start
    // pointing at a host of ours, this fails.
    for (const size of ['small', 'normal', 'large', 'png', 'art_crop']) {
      assert.ok(cardImageUrl(DEATH_WIND, size).startsWith('https://cards.scryfall.io/'));
    }
  });
});

describe('identifying ourselves to Scryfall', () => {
  test('every art request carries a User-Agent', () => {
    // Not politeness. Scryfall's CDN answers 400 to the `okhttp/x.y.z` that
    // React Native's image loader sends by default, so without this EVERY
    // card in the app fails while the same URL works in a browser.
    //   curl -A "okhttp/4.9.2"    -> 400
    //   curl -A "DensaDeck/0.2.2" -> 200
    assert.ok(ART_HEADERS['User-Agent']);
    assert.match(ART_HEADERS['User-Agent'], /DensaDeck/);
  });

  test('the User-Agent names the app and its version', () => {
    // Scryfall ask clients to identify themselves; a version makes a
    // misbehaving build traceable to a release rather than to "the app".
    assert.match(USER_AGENT, /^DensaDeck\/\d+\.\d+\.\d+/);
  });

  test('it does not pretend to be a browser', () => {
    // Spoofing a browser UA would work and is exactly the thing that gets a
    // client blocked later.
    assert.ok(!/Mozilla|Chrome|Safari|okhttp/i.test(USER_AGENT));
  });

  test('a source carries both the URL and the headers', () => {
    const source = artSource(DEATH_WIND, 'small');
    assert.match(source.uri, /cards\.scryfall\.io/);
    assert.equal(source.headers['User-Agent'], USER_AGENT);
  });

  test('a source for an unusable id has no URL but is still shaped right', () => {
    // The screen checks `uri`; a missing headers object would crash it.
    const source = artSource('');
    assert.equal(source.uri, '');
    assert.ok(source.headers);
  });
});

describe('the queue of art to fetch', () => {
  test('a printing listed twice is fetched once', () => {
    // A collection holds a foil and a nonfoil of the same printing, and
    // fetching that JPEG twice is what Scryfall ask clients not to do.
    assert.deepEqual(artQueue([DEATH_WIND, DEATH_WIND]), [DEATH_WIND]);
  });

  test('unusable ids are dropped rather than queued', () => {
    assert.deepEqual(artQueue(['', 'none', DEATH_WIND]), [DEATH_WIND]);
  });

  test('an empty collection gives an empty queue', () => {
    assert.deepEqual(artQueue([]), []);
  });

  test('order is preserved so progress reads sensibly', () => {
    const other = '3ad02b56-13ec-46ef-92bd-ae078b8bb517';
    assert.deepEqual(artQueue([DEATH_WIND, other]), [DEATH_WIND, other]);
  });
});

describe('is card art reachable at all', () => {
  test('a good answer reads as working', async () => {
    const result = await checkArtReachable(async () => ({ ok: true, status: 200 }));
    assert.equal(result.ok, true);
    assert.match(result.detail, /Scryfall/);
  });

  test('an error from Scryfall is named as theirs, not yours', async () => {
    // Sending someone to check their own Wi-Fi over a 503 wastes their
    // evening.
    const result = await checkArtReachable(async () => ({ ok: false, status: 503 }));
    assert.equal(result.ok, false);
    assert.match(result.detail, /503/);
    assert.match(result.detail, /their end/);
  });

  test('a 400 is named as the missing User-Agent that it is', async () => {
    // This exact failure shipped. "Scryfall answered 400, that is their end"
    // would have sent us looking in the wrong place for another evening.
    const result = await checkArtReachable(async () => ({ ok: false, status: 400 }));
    assert.match(result.detail, /User-Agent/);
  });

  test('the probe sends the same headers a real request does', async () => {
    // A probe without them would report a 400 caused by the probe, and the
    // app would be blamed for a fault in its own diagnostics.
    let sent;
    await checkArtReachable(async (_url, init) => {
      sent = init?.headers;
      return { ok: true, status: 200 };
    });
    assert.equal(sent?.['User-Agent'], USER_AGENT);
  });

  test('no connection says what art actually needs', async () => {
    // The tailnet is not the internet, and the app had been letting people
    // assume that being "Connected" covered both.
    const result = await checkArtReachable(async () => {
      throw new Error('Network request failed');
    });
    assert.equal(result.ok, false);
    assert.match(result.detail, /Network request failed/);
    assert.match(result.detail, /tailnet alone is not enough/);
  });

  test('it names the wrong-clock case, which looks identical otherwise', async () => {
    // A device days out of date fails every HTTPS connection while plain
    // HTTP to the tailnet keeps working — so the collection syncs and the
    // art does not, which reads as a bug in the app.
    const result = await checkArtReachable(async () => {
      throw new Error('certificate is not yet valid');
    });
    assert.match(result.detail, /date and time/);
  });

  test('it never throws, whatever comes back', async () => {
    for (const impl of [
      async () => { throw 'a string, not an Error'; },
      async () => { throw undefined; },
    ]) {
      const result = await checkArtReachable(impl);
      assert.equal(result.ok, false);
      assert.ok(result.detail.length > 0);
    }
  });
});
