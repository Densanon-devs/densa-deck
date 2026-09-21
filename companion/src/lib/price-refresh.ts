/**
 * Keeping prices current without re-downloading the world.
 *
 * Prices arrive with the card index, because the bulk file carries
 * them and we already stream past every row. That is the right way to
 * get a first number for a hundred thousand printings and the wrong
 * way to keep them: the file is 78 MB and prices move daily, so a
 * phone that re-fetched it for prices alone would spend a gigabyte a
 * fortnight to track cards it does not own.
 *
 * The prices worth keeping current are the ones attached to something
 * — cards in the collection, cards in a deck. That is tens or
 * hundreds, not a hundred thousand, and Scryfall answers seventy-five
 * at a time from `/cards/collection`. A collection of 88 cards is two
 * requests.
 *
 * So: the index gives every card a price, and this keeps YOUR cards'
 * prices honest.
 */

/** Scryfall takes at most this many identifiers in one request. */
export const BATCH = 75;

/**
 * How old prices may get before they are worth refetching.
 *
 * A week. Magic prices drift rather than jump, and someone checking
 * what a deck is worth is asking a rough question -- but a month-old
 * number on a card that spiked is wrong in a way that matters, and
 * two requests is a cheap way to not be wrong.
 */
export const PRICE_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

/** Split printing ids into requests Scryfall will accept. */
export function priceBatches(ids: string[], size = BATCH): string[][] {
  const clean = [...new Set(
    (ids || []).map((id) => String(id || '').trim()).filter(Boolean),
  )];
  const out: string[][] = [];
  for (let i = 0; i < clean.length; i += size) {
    out.push(clean.slice(i, i + size));
  }
  return out;
}

/**
 * Whether prices are old enough to be worth fetching again.
 *
 * Never fetched at all counts as due: a phone whose index predates
 * prices has nothing, and waiting a week to get a first number would
 * be a strange way to treat it.
 */
export function pricesDue(
  lastPricedAt: number,
  now: number,
  maxAge = PRICE_MAX_AGE_MS,
): boolean {
  if (!lastPricedAt) return true;
  // A clock that has gone backwards -- a restored backup, a timezone
  // change -- must not lock refreshes out until the date catches up.
  if (lastPricedAt > now) return true;
  return now - lastPricedAt >= maxAge;
}

/** How old the prices are, for a line in settings rather than a log. */
export function pricedInWords(at: number, now: number): string {
  if (!at) return 'never';
  const hours = Math.floor((now - at) / (60 * 60 * 1000));
  if (hours < 1) return 'just now';
  if (hours < 24) return hours === 1 ? 'an hour ago' : `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  const months = Math.round(days / 30);
  return months <= 1 ? 'about a month ago' : `about ${months} months ago`;
}
