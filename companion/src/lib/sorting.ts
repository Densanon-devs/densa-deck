/**
 * Ordering a list of cards, both ways round.
 *
 * The same shape as the desktop's `resolve_order`: what you sort BY and
 * which way it runs are separate choices. Baked together they have to be
 * enumerated, and half of them never get their second half — which is how
 * the desktop ended up able to show your most valuable cards but not your
 * least, and the phone able to show neither.
 *
 * Pure, and sorting a copy, so it is testable in Node and cannot surprise a
 * caller by rearranging the array it was handed.
 */

export type SortKey = 'name' | 'cmc' | 'price' | 'quantity' | 'set';
export type Direction = 'asc' | 'desc';

/** The way each sort reads when nobody has said otherwise. */
export const NATURAL: Record<SortKey, Direction> = {
  name: 'asc',
  cmc: 'asc',
  // Nobody opens a collection to find their cheapest card.
  price: 'desc',
  quantity: 'desc',
  set: 'asc',
};

export const SORT_LABELS: Record<SortKey, string> = {
  name: 'Name',
  cmc: 'Mana value',
  price: 'Price',
  quantity: 'Copies',
  set: 'Set',
};

export interface SortableCard {
  card_name: string;
  printing_id: string;
  quantity: number;
  price_usd?: number | null;
}

/**
 * Sort cards, leaving the original alone.
 *
 * `manaValues` comes from the index the phone pulled off the PC; a card
 * missing from it has no mana value, which is different from having zero.
 *
 * Two rules the reverse must not break, matching the desktop exactly:
 *
 * * Unknowns stay at the bottom. A card with no price is not the cheapest
 *   card. Sorted naively, reversing floats every row the phone knows least
 *   about to the top of a list that is supposed to be about value.
 * * The tiebreaker never flips. Cards that tie stay alphabetical whichever
 *   way the list runs, so a reversed list is the same rows in the opposite
 *   order rather than a reshuffle.
 */
export function sortCards<T extends SortableCard>(
  cards: readonly T[],
  key: SortKey,
  direction: Direction | '' = '',
  manaValues?: Map<string, number>,
): T[] {
  const way = direction || NATURAL[key] || 'asc';
  const flip = way === 'desc' ? -1 : 1;

  const valueOf = (card: T): number | string | null => {
    switch (key) {
      case 'cmc': return manaValues?.get(card.printing_id) ?? null;
      case 'price': return card.price_usd ?? null;
      case 'quantity': return card.quantity;
      case 'set': return null;      // handled by the name fallback below
      default: return null;
    }
  };

  return [...cards].sort((a, b) => {
    if (key !== 'name') {
      const left = valueOf(a);
      const right = valueOf(b);
      // Missing sinks, in both directions, so it is never the answer to
      // "what is my most expensive card".
      if (left == null && right != null) return 1;
      if (left != null && right == null) return -1;
      if (left != null && right != null && left !== right) {
        return (left < right ? -1 : 1) * flip;
      }
    } else {
      const byName = a.card_name.localeCompare(b.card_name);
      if (byName !== 0) return byName * flip;
    }
    // Ties: alphabetical, never flipped.
    return a.card_name.localeCompare(b.card_name);
  });
}

/** The arrow to show, given a sort and whatever direction is in force. */
export function directionOf(key: SortKey, direction: Direction | ''): Direction {
  return direction || NATURAL[key] || 'asc';
}
