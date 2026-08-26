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
  cardImageUrl,
  checkArtReachable,
  prefetchCollectionArt,
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

describe('warming the cache before you need it', () => {
  const ids = (n) =>
    Array.from({ length: n }, (_, i) =>
      `${(i % 10).toString(16)}7ed0a14-1a98-4190-b195-f84fa42d43${(i % 100)
        .toString()
        .padStart(2, '0')}`,
    );

  test('it fetches each card once', async () => {
    const seen = [];
    const result = await prefetchCollectionArt(ids(5), {
      prefetch: async (url) => seen.push(url),
    });
    assert.equal(result.total, 5);
    assert.equal(result.done, 5);
    assert.equal(new Set(seen).size, 5);
  });

  test('a card listed twice is fetched once', async () => {
    // A collection holds foils and nonfoils of the same printing. Fetching
    // the same JPEG twice is exactly what Scryfall asks clients not to do.
    let calls = 0;
    const result = await prefetchCollectionArt([DEATH_WIND, DEATH_WIND, DEATH_WIND], {
      prefetch: async () => {
        calls += 1;
      },
    });
    assert.equal(calls, 1);
    assert.equal(result.total, 1);
  });

  test('it does not open a thousand connections at once', async () => {
    // Rude to Scryfall, throttled anyway, and it would saturate the same
    // connection the app needs for syncing.
    let live = 0;
    let peak = 0;
    await prefetchCollectionArt(ids(40), {
      concurrency: 4,
      prefetch: async () => {
        live += 1;
        peak = Math.max(peak, live);
        await new Promise((resolve) => setTimeout(resolve, 1));
        live -= 1;
      },
    });
    assert.ok(peak <= 4, `ran ${peak} at once`);
  });

  test('one failure does not abandon the rest', async () => {
    // Half a collection cached is strictly better than none, and the card
    // that failed will load when it is next opened with signal.
    const result = await prefetchCollectionArt(ids(6), {
      prefetch: async (url) => {
        if (url.includes('/3/')) throw new Error('timed out');
      },
    });
    assert.equal(result.done, 6);
    assert.ok(result.failed >= 1);
  });

  test('unusable ids are skipped rather than fetched', async () => {
    const seen = [];
    const result = await prefetchCollectionArt(['', 'none', DEATH_WIND], {
      prefetch: async (url) => seen.push(url),
    });
    assert.equal(result.total, 1);
    assert.equal(seen.length, 1);
  });

  test('an empty collection reports done rather than hanging', async () => {
    const result = await prefetchCollectionArt([], { prefetch: async () => {} });
    assert.deepEqual(result, { done: 0, total: 0, failed: 0 });
  });

  test('it can be stopped part way', async () => {
    // Leaving a screen should not leave forty downloads running.
    let calls = 0;
    let stop = false;
    await prefetchCollectionArt(ids(40), {
      concurrency: 2,
      shouldStop: () => stop,
      prefetch: async () => {
        calls += 1;
        if (calls >= 4) stop = true;
      },
    });
    assert.ok(calls < 40, `kept going: ${calls}`);
  });

  test('progress is reported as it goes', async () => {
    // A silent thirty-second download looks like a button that did nothing.
    const seen = [];
    await prefetchCollectionArt(ids(3), {
      concurrency: 1,
      prefetch: async () => {},
      onProgress: (p) => seen.push(p.done),
    });
    assert.deepEqual(seen, [1, 2, 3]);
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
