/**
 * Which picture a wishlist row shows.
 *
 * The wishlist used to be names only -- standing in a shop you could not
 * see the card you were looking for. A want that names a printing shows
 * THAT printing: it is the one to buy. A want by name alone shows the most
 * recent printing, which is the one a shop is most likely to have, with the
 * rest behind it to swipe through.
 *
 * Answered from the phone's own card index, so it works with no signal.
 */

export interface PrintingLike {
  printing_id: string;
  set_code: string;
  collector_number: string;
  released_year?: number | null;
}

export function orderForWish<T extends PrintingLike>(
  printings: T[],
  want: { set_code?: string; collector_number?: string },
): T[] {
  const set = (want.set_code ?? '').trim().toLowerCase();
  const num = (want.collector_number ?? '').trim().toLowerCase();
  const rank = (p: T) => {
    const pSet = (p.set_code ?? '').toLowerCase();
    const pNum = String(p.collector_number ?? '').toLowerCase();
    if (set && num && pSet === set && pNum === num) return 0;   // the one asked for
    if (set && pSet === set) return 1;                          // same set, any number
    return 2;
  };
  return printings
    .filter((p) => p.printing_id)
    .map((p, i) => ({ p, i }))
    .sort((a, b) =>
      rank(a.p) - rank(b.p)
      // Newest first within a rank; an unknown year sorts last, not first.
      || (b.p.released_year ?? -1) - (a.p.released_year ?? -1)
      || a.i - b.i)
    .map(({ p }) => p);
}
