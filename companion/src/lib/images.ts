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

import { VERSION } from './version.ts';

export type ImageSize =
  | 'small'
  | 'normal'
  | 'large'
  | 'png'
  | 'art_crop'
  | 'border_crop';

const CDN = 'https://cards.scryfall.io';

/**
 * Who we say we are when asking for a picture.
 *
 * Not optional politeness — Scryfall's CDN answers **HTTP 400** to the
 * `okhttp/x.y.z` User-Agent that React Native's image loader sends by
 * default. Reproduced against the live service:
 *
 *     curl -A "okhttp/4.9.2"    -> 400
 *     curl -A "DensaDeck/0.2.2" -> 200
 *     curl  (no UA at all)      -> 200
 *
 * So every card in the app failed to load while the same URL worked from a
 * browser and from the desktop, which is exactly how it looked. Scryfall ask
 * clients to identify themselves; this does, and it is what makes the request
 * succeed at all.
 */
export const USER_AGENT = `DensaDeck/${VERSION} (companion; Android)`;

/** Headers every art request must carry. */
export const ART_HEADERS: Record<string, string> = {
  'User-Agent': USER_AGENT,
  Accept: 'image/jpeg,image/png,image/*;q=0.8',
};

/** What to hand an <Image source>. Empty uri means "no art for this". */
export function artSource(
  printingId: string,
  size: ImageSize = 'normal',
): { uri: string; headers: Record<string, string> } {
  return { uri: cardImageUrl(printingId, size), headers: ART_HEADERS };
}

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
 * The list of art to fetch, deduplicated.
 *
 * A collection holds a foil and a nonfoil of the same printing; fetching that
 * JPEG twice is exactly what Scryfall ask clients not to do.
 *
 * There is deliberately no `Image.prefetch` here. On Android it takes a URL
 * and nothing else — no headers — and an art request without a User-Agent is
 * answered 400 by Scryfall's CDN. So warming the cache has to go through a
 * real `<Image>` with real headers, which is what `ArtWarmer` does. A prefetch
 * helper that quietly 400'd everything would look like it was working.
 */
export function artQueue(
  printingIds: string[],
  size: ImageSize = 'normal',
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const id of printingIds) {
    const url = cardImageUrl(id, size);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    out.push(id);
  }
  return out;
}

/** A card that will exist for as long as Magic does, for testing the path. */
export const PROBE_URL = cardImageUrl('87ed0a14-1a98-4190-b195-f84fa42d4364');

export interface ArtReach {
  ok: boolean;
  detail: string;
}

/**
 * Can this phone actually fetch card art?
 *
 * Worth asking separately from "can it reach the PC". They are different
 * networks that fail independently: art comes from Scryfall over the public
 * internet, the collection from a machine on the tailnet. Being connected to
 * one says nothing about the other.
 */
export async function checkArtReachable(
  fetchImpl: typeof fetch = fetch,
  url: string = PROBE_URL,
): Promise<ArtReach> {
  try {
    const response = await fetchImpl(url, {
      method: 'GET',
      // The same headers a real art request sends. A probe without them would
      // report a 400 that only existed because the probe was anonymous.
      headers: ART_HEADERS,
    });
    if (response.ok) {
      return { ok: true, detail: 'Card art loads. Scryfall is reachable.' };
    }
    return {
      ok: false,
      detail:
        response.status === 400
          ? 'Scryfall answered 400. That is what they return to a request ' +
            'that does not identify itself — the app should be sending a ' +
            'User-Agent and evidently is not.'
          : `Scryfall answered ${response.status}. That is their end, not yours.`,
    };
  } catch (err) {
    const message = (err as Error)?.message || String(err);
    return {
      ok: false,
      // The two that actually happen and look identical otherwise: no
      // internet at all, and a device clock wrong enough to fail TLS.
      detail:
        `Could not reach Scryfall: ${message}. Card art needs ordinary ` +
        `internet — the tailnet alone is not enough. If this phone has Wi-Fi ` +
        `and it still fails, check the date and time: a clock that is days ` +
        `out makes every HTTPS connection fail while the tailnet keeps working.`,
    };
  }
}
