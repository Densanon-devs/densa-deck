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

/** What this phone holds of the card that was just scanned. */
export interface Held {
  /** Copies of the exact printing in your hand. */
  thisPrinting: number;
  /** Copies of the same card in some other printing. */
  otherPrintings: number;
}

/**
 * Whether to stop and ask before putting this in the deck.
 *
 * Only in the mode that files nothing. "From my collection" is a
 * claim about the card, and when the claim is wrong the deck quietly
 * fills with cards the collection has never heard of -- so the
 * shortfall, the value and the cost-to-finish are all computed
 * against a collection that is missing them.
 *
 * By PRINTING rather than by name. You are holding a specific card;
 * owning a different printing of it does not mean this one is
 * recorded, and a deck slot naming this printing against a
 * collection that has another is exactly the mismatch worth
 * catching.
 */
export function askBeforeDeck(plan: ScanPlan, held: Held): boolean {
  if (!plan.deck || plan.file) return false;
  return (held?.thisPrinting ?? 0) <= 0;
}

/**
 * What to say about it.
 *
 * Owning the card in another printing is a different sentence from
 * owning none of it, and telling someone their card is missing when
 * four of it are on the shelf is how a warning gets dismissed
 * without being read.
 */
export function notOwnedLine(name: string, held: Held): string {
  const other = held?.otherPrintings ?? 0;
  if (other > 0) {
    return `You own ${other} ${name}, but not this printing.`;
  }
  return `${name} is not in your collection yet.`;
}

/**
 * Whether a photo the PC could not be reached for may be kept for later.
 *
 * The queue can do exactly one thing when it drains: add a copy to a
 * collection. That is the right thing only for a plain add. Queued in
 * tag mode it filed a NEW copy of a card the person said they already
 * own -- the one thing tag mode promises never to do -- and queued from
 * inside a deck it filed the card into the collection and never put it
 * in the deck at all. Neither intent survives the queue, so in those
 * modes the photo is not kept and the screen says why.
 */
export function canQueue(plan: ScanPlan): boolean {
  return plan.file && !plan.deck && !plan.tag;
}

/** What to say when a photo could not be kept, by what the scan was for. */
export function notQueuedLine(plan: ScanPlan): string {
  if (plan.tag) {
    return "Couldn't reach your PC, and tagging needs a match now \u2014 "
      + 'nothing was changed. Try again in range, or get the card index.';
  }
  return "Couldn't place that card and your PC isn't reachable, so it "
    + "wasn't added to the deck. Try again, or type the name.";
}
