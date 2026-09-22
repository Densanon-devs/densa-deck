/**
 * Where a scanned card goes.
 *
 * The Scan tab has always had one answer: into a collection. Opened
 * from inside a deck it has two more, and they differ by exactly one
 * step — whether the collection is written to at all.
 *
 *   From my collection   the card is already yours. It goes in the
 *                        deck and the collection is untouched, which
 *                        is what you want walking a deck out of a box
 *                        you have already catalogued.
 *
 *   New card             fresh out of a booster. Filed into the
 *                        chosen collection AND put in the deck.
 *
 * Kept out of the screen because getting it wrong is silent in both
 * directions, exactly like the tag/file pair it sits beside: filing
 * when you meant not to inflates what you own, and not filing when
 * you meant to loses a card you have just bought.
 */

/** What a scan should do, once a card has been identified. */
export interface ScanPlan {
  /** Add a copy to the collection. */
  file: boolean;
  /** Put a slot in the deck. */
  deck: boolean;
  /** Put an owned card into a group, without changing quantities. */
  tag: boolean;
}

/**
 * The plan for one scan.
 *
 * `mode` is the same two-way switch in both worlds, which is
 * deliberate: it is the same question — is this card already yours?
 * — and one switch that means one thing is easier to trust than two
 * that look alike.
 */
export function planFor(mode: 'add' | 'tag', intoDeck: boolean): ScanPlan {
  if (intoDeck) {
    return { file: mode === 'add', deck: true, tag: false };
  }
  return { file: mode === 'add', deck: false, tag: mode === 'tag' };
}

/**
 * The word on the green flash.
 *
 * Different for each outcome, because they are different operations
 * and a flash that said the same thing for all of them would be
 * lying about two.
 */
export function flashVerb(
  plan: ScanPlan,
  alsoTagged = 0,
): string {
  if (plan.deck) return plan.file ? 'ADDED → DECK' : 'INTO DECK';
  if (plan.tag) return 'TAGGED';
  return alsoTagged ? `ADDED +${alsoTagged}` : 'ADDED';
}

/**
 * Whether the collection picker is worth showing.
 *
 * It chooses where a card is FILED, so in the deck mode that files
 * nothing it is a control that does nothing — and a control that
 * does nothing reads as one that is broken.
 */
export function needsCollection(plan: ScanPlan): boolean {
  return plan.file || plan.tag;
}
