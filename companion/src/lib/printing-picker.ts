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
