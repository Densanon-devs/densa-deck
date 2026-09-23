/**
 * Putting a long list of printings in front of somebody.
 *
 * Scanning a card usually offers a handful. Typing a name offers
 * everything: 671 Islands, 41 Sol Rings, 29 Lightning Bolts. Sorting
 * those newest-first makes the top of the list reasonable and leaves
 * the rest unreachable, because a picker can only show forty rows
 * before it stops being a list and starts being a scroll.
 *
 * So there is a set box in front of the sort. Kept here rather than
 * in the screen because it is the part that can be wrong -- a filter
 * that matches a set code by accident, an ordering that puts 1993
 * first -- and a screen cannot be tested in Node.
 */

/**
 * As much of a printing as the picker cares about.
 *
 * No index signature: the callers pass their own row types through
 * and get the same type back, and an index signature here would make
 * every one of those an error at the door.
 */
export interface PickableRow {
  set_code: string;
}

/** What the phone knows about a set: its name, and when it landed. */
export type SetLookup = Record<string, { name?: string; year?: number }>;

/**
 * Whether one printing answers to what was typed in the set box.
 *
 * Code or name, either way. People know a set by whichever of the
 * two they happen to have seen most, and the code is what is printed
 * on the card -- so `blb` and `bloom` both have to find Bloomburrow.
 */
export function matchesSet(
  row: PickableRow,
  want: string,
  sets: SetLookup = {},
): boolean {
  const term = String(want ?? '').trim().toLowerCase();
  if (!term) return true;
  const code = String(row?.set_code ?? '').toLowerCase();
  const name = String(sets[code]?.name ?? '').toLowerCase();
  return code.includes(term) || name.includes(term);
}

/**
 * The printings to show, filtered and newest first.
 *
 * A set with no date sinks rather than floating to the top: an
 * unknown year is not 1993 and must not be treated as the oldest
 * thing in the list either, but of the two wrong answers, showing it
 * last is the one that does not push real recent printings off the
 * visible page.
 */
export function orderPrintings<T extends PickableRow>(
  rows: T[],
  want = '',
  sets: SetLookup = {},
): T[] {
  const kept = (rows ?? []).filter((row) => matchesSet(row, want, sets));
  const year = (row: T) => sets[String(row?.set_code ?? '').toLowerCase()]
    ?.year ?? 0;
  return [...kept].sort((a, b) => year(b) - year(a));
}

/**
 * The line above the list, which exists so the count is not a lie.
 *
 * A picker showing forty of 671 rows with nothing said about it
 * reads as "these are the printings". They are 6% of them.
 */
export function pickerCount(
  total: number,
  shown: number,
  cap: number,
): string {
  if (shown === total) {
    return total <= cap
      ? `${total} printing${total === 1 ? '' : 's'}`
      : `${total} printings, ${cap} shown`;
  }
  return `${shown} of ${total} match`;
}

/**
 * Yours first, and — when you asked for only yours — only yours.
 *
 * The swipe pager in the deck builder lists every printing of a card,
 * newest first, which is the right order for a card you are choosing
 * to acquire and the wrong one for a card you already have. The copy
 * in your box is the one going on the table; it should not be four
 * swipes in behind three reprints you have never held.
 *
 * And with Only Mine on, the other printings are not an ordering
 * problem, they are noise: the whole filter means "build from what I
 * have", so offering a version you do not own contradicts the thing
 * that was asked for.
 *
 * `onlyYours` is honoured only when it leaves something. A stack
 * filed before printing ids were recorded, or one whose printing is
 * not in this phone's index, means the card IS owned and no variant
 * can be matched to it — and a pager with nothing in it would be a
 * worse answer than a pager with too much. The same rule as
 * everywhere else here: lose detail, never the screen.
 */
export function yoursFirst<T extends { printing_id: string }>(
  rows: T[],
  owned: Set<string> = new Set(),
  onlyYours = false,
): T[] {
  const list = rows ?? [];
  const mine = list.filter((r) => owned.has(r.printing_id));
  if (!mine.length) return [...list];
  if (onlyYours) return mine;
  const rest = list.filter((r) => !owned.has(r.printing_id));
  return [...mine, ...rest];
}

/** The line above the pager, which has to say which list this is. */
export function variantsLine(
  shown: number,
  total: number,
  onlyYours: boolean,
): string {
  if (shown <= 1) return '';
  if (onlyYours && shown < total) {
    return `${shown} of ${total} printings — the ones you own`;
  }
  return `${shown} printings — swipe to see them`;
}

/** How many of one printing are on a shelf, in words. */
export function ownedLine(
  held: { total: number; foil: number } | undefined,
): string {
  const total = held?.total ?? 0;
  if (total <= 0) return '';
  const foil = Math.min(held?.foil ?? 0, total);
  // "You own 1 foil" rather than "You own 1 · 1 foil", which reads
  // as two cards.
  if (foil === total) {
    return total === 1 ? 'You own 1 foil' : `You own ${total}, all foil`;
  }
  if (foil > 0) return `You own ${total} · ${foil} foil`;
  return `You own ${total}`;
}

/** A printing with enough on it to be priced and chosen. */
export interface PricedRow {
  printing_id: string;
  price_usd?: number | null;
}

/**
 * The one to buy, when nobody wants to look.
 *
 * Quick-add exists so a deck can be built by tapping, and the
 * question it silently answers is "which printing" -- forty of them
 * for a common card, all the same card. Cheapest is the only answer
 * that is defensible without looking: it is what the deck would
 * actually cost, which is the number this mode is for.
 *
 * Unpriced rows are not free. They are rows Scryfall has no price
 * for, and treating a missing number as zero would make every
 * unpriced printing win every time -- the exact `Number('')` trap
 * this project has already paid for once. They are used only when
 * nothing at all is priced, and then the order is by id so two taps
 * on the same card give the same answer.
 *
 * `onlyOwned` narrows to the shelf first, for the same reason the
 * pager does: with Only Mine on, offering a printing you do not have
 * contradicts the thing that was asked for. It falls back to the
 * full list rather than to nothing, because a card owned with no
 * printing recorded is still a card you own.
 */
export function cheapestPrinting<T extends PricedRow>(
  rows: T[],
  owned: Set<string> = new Set(),
  onlyOwned = false,
): T | undefined {
  const all = rows ?? [];
  if (!all.length) return undefined;
  const mine = all.filter((r) => owned.has(r.printing_id));
  const pool = onlyOwned && mine.length ? mine : all;

  const priced = pool.filter((r) => typeof r.price_usd === 'number'
    && Number.isFinite(r.price_usd) && (r.price_usd as number) >= 0);
  const from = priced.length ? priced : pool;
  return [...from].sort((a, b) => {
    const left = typeof a.price_usd === 'number' ? a.price_usd : 0;
    const right = typeof b.price_usd === 'number' ? b.price_usd : 0;
    return left - right
      || String(a.printing_id).localeCompare(String(b.printing_id));
  })[0];
}
