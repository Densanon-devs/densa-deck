/**
 * Whether the card in your hand is the shiny one.
 *
 * Nothing in this app could say so. The scanner decided it from a
 * star in the collector line -- and the star is the one glyph the
 * recogniser reliably loses, as the matcher's own comment says:
 * "measured against Windows OCR the star NEVER comes back". So
 * `foilHint` was false almost always, the candidate was built with
 * `finishes: ['nonfoil']`, and no screen offered a way to disagree.
 *
 * A foil filed as an ordinary copy is not a cosmetic error. On a card
 * whose foil is worth ten times its nonfoil, it is most of what the
 * collection is worth.
 *
 * Two separate facts, and conflating them is what went wrong:
 *
 *   * which finishes this PRINTING was made in -- Scryfall knows, and
 *     the index carries it now;
 *   * which one you are HOLDING -- only you know, and you have to be
 *     able to say.
 */

/** The finishes a printing exists in, as the index stores them. */
export function finishesOf(stored: string | null | undefined): string[] {
  return String(stored ?? '')
    .split(',')
    .map((f) => f.trim().toLowerCase())
    .filter(Boolean);
}

/**
 * Whether a foil of this printing exists.
 *
 * Unknown is NOT no. An index downloaded before this column existed
 * has nothing here, and treating that as "there is no foil" is
 * exactly the assumption that caused the problem -- it would hide the
 * toggle on every card until somebody re-downloaded 78 MB, which is
 * the same silence in a new coat.
 */
export function foilExists(stored: string | null | undefined): boolean {
  const known = finishesOf(stored);
  if (!known.length) return true;
  return known.some((f) => f !== 'nonfoil');
}

/** Whether a nonfoil exists. Same rule about not knowing. */
export function nonfoilExists(stored: string | null | undefined): boolean {
  const known = finishesOf(stored);
  if (!known.length) return true;
  return known.includes('nonfoil');
}

/**
 * What to file, given what the printing offers and what was asked.
 *
 * The person's choice wins wherever the printing allows it, because
 * they are holding the card and nothing here is. It is overruled only
 * when the index positively knows that finish does not exist -- an
 * Alpha Island has no foil, and recording one would invent a card.
 */
export function finishToFile(
  stored: string | null | undefined,
  wantFoil: boolean,
): string {
  const known = finishesOf(stored);
  if (!wantFoil) return nonfoilExists(stored) ? 'nonfoil' : (known[0] ?? 'nonfoil');
  if (!known.length) return 'foil';
  // `etched` is a foil for this purpose and is sometimes the only one.
  const shiny = known.find((f) => f !== 'nonfoil');
  return shiny ?? 'nonfoil';
}

/**
 * Why the foil switch is not available, or '' when it is.
 *
 * Said rather than greyed out silently: "this printing was never made
 * in foil" is a fact worth reading, and a disabled control with no
 * explanation reads as a bug.
 */
export function noFoilBecause(stored: string | null | undefined): string {
  return foilExists(stored) ? '' : 'This printing was never made in foil.';
}
