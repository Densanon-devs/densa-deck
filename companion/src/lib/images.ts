/**
 * Card art: where it comes from, and how it survives having no signal.
 *
 * **Nothing here rehosts anything.** The URLs point at Scryfall's CDN, which
 * is the project's licence position — art is hotlinked, never served from us
 * and never bundled into a build.
 *
 * Caching is a different question from hosting, and the two get confused. What
 * is forbidden is redistributing card images. What Scryfall's own guidelines
 * ASK for is that clients keep what they have already fetched instead of
 * requesting it again — re-downloading the same JPEG every time a card is
 * opened is the behaviour they object to. So the cache here is the ordinary
 * on-device HTTP cache, holding images this phone fetched for its own display,
 * and it never leaves the phone.
 *
 * On Android that cache is Fresco's, and React Native's `Image` uses it
 * automatically: art seen once is on the phone afterwards, with no code. What
 * needs code is the card you have NOT opened yet — because the moment you want
 * to look through your collection is usually the moment you have no signal, in
 * a shop, holding a card. `prefetchCollectionArt` warms it before then.
 *
 * This file imports nothing from react-native, deliberately. The caller passes
 * `Image.prefetch` in. An import here would be native code at module scope,
 * which is unresolvable under Node — and the whole point of keeping logic out
 * of the screens is that it can be tested there.
 */

export type ImageSize =
  | 'small'
  | 'normal'
  | 'large'
  | 'png'
  | 'art_crop'
  | 'border_crop';

const CDN = 'https://cards.scryfall.io';

/**
 * A Scryfall id, loosely.
 *
 * Loose on purpose: this stops a blank or obviously wrong value producing
 * `.../n/o/none.jpg`, which renders as a broken image with no explanation. It
 * is not an attempt to validate Scryfall's id format for them.
 */
function looksLikeId(printingId: string): boolean {
  const cleaned = String(printingId ?? '').trim().toLowerCase();
  return (
    cleaned.length >= 8 &&
    /^[0-9a-f][0-9a-f-]*$/.test(cleaned)
  );
}

export function cardImageUrl(
  printingId: string,
  size: ImageSize = 'normal',
): string {
  const cleaned = String(printingId ?? '').trim().toLowerCase();
  if (!looksLikeId(cleaned)) return '';
  const extension = size === 'png' ? 'png' : 'jpg';
  // Two directory levels from the first two characters of the id — Scryfall's
  // documented layout, and what a card's own `image_uris` resolve to.
  return `${CDN}/${size}/front/${cleaned[0]}/${cleaned[1]}/${cleaned}.${extension}`;
}

export function scryfallPageUrl(printingId: string): string {
  const cleaned = String(printingId ?? '').trim().toLowerCase();
  return looksLikeId(cleaned) ? `https://scryfall.com/card/${cleaned}` : '';
}

export interface PrefetchProgress {
  done: number;
  total: number;
  failed: number;
}

/**
 * Pull the collection's art onto the phone before it is needed.
 *
 * Runs a few at a time rather than all at once: a thousand simultaneous image
 * requests is rude to Scryfall and will be throttled anyway, and it would
 * saturate the connection the app also needs for syncing.
 *
 * Failures are counted, not thrown. Half a collection cached is strictly
 * better than none, and the one card that failed will simply load when it is
 * next opened with signal.
 */
export async function prefetchCollectionArt(
  printingIds: string[],
  options: {
    size?: ImageSize;
    concurrency?: number;
    onProgress?: (progress: PrefetchProgress) => void;
    shouldStop?: () => boolean;
  } & { prefetch: (url: string) => Promise<unknown> },
): Promise<PrefetchProgress> {
  const { size = 'normal', concurrency = 4, onProgress, shouldStop, prefetch } =
    options;

  const urls = [...new Set(printingIds)]
    .map((id) => cardImageUrl(id, size))
    .filter(Boolean);

  const progress: PrefetchProgress = { done: 0, total: urls.length, failed: 0 };
  if (!urls.length) {
    onProgress?.(progress);
    return progress;
  }

  let next = 0;
  const worker = async () => {
    for (;;) {
      if (shouldStop?.()) return;
      const index = next;
      next += 1;
      const url = urls[index];
      if (!url) return;
      try {
        await prefetch(url);
      } catch {
        progress.failed += 1;
      }
      progress.done += 1;
      onProgress?.({ ...progress });
    }
  };

  await Promise.all(
    Array.from({ length: Math.max(1, concurrency) }, () => worker()),
  );
  return progress;
}
