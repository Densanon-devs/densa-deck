/**
 * The credit line, and why it is the answer for basic lands.
 *
 * Every card that cannot be identified by its footer key falls back
 * to its name, and the catalogue is shaped so that usually works:
 * 46% of names have exactly one printing, 77% have three or fewer.
 *
 * Basic lands are the hole in that. `Island` has 828 paper printings,
 * `Forest` 864 -- 4,203 across the six names -- and a list of eight
 * hundred is not a shortlist, so the scanner refuses rather than
 * guessing. Which is correct, and leaves a basic land scannable only
 * when the collector number reads, with nothing at all behind it.
 *
 * The way out is already printed on the card, in the same footer
 * strip the collector number is in. Measured over 2,319 real basic
 * printings (Island, Forest, Plains):
 *
 *   artist alone      10% to one row, 24% to three
 *   year alone         0% to one row,  0% to three
 *   artist AND year   35% to one row, 68-74% to three, 83-87% to five
 *
 * Artist alone is useless because John Avon painted seventy-nine
 * Islands. The year alone is useless because a year has forty rows in
 * it. Together they turn a refusal into a three-card tap.
 *
 * Nothing here parses the word "Illus." or the copyright glyph. The
 * candidate artists come from the index -- 169 of them for Island --
 * and the text is searched for one of those, so the label, its
 * punctuation and its position do not have to survive the OCR. Only
 * the name does.
 */

/**
 * Strip a name down to what two spellings of it have in common.
 *
 * Accents come and go through OCR and through data entry both, so
 * `Alexandre Honore` has to match `Alexandre Honoré`. Punctuation
 * becomes a space rather than vanishing, so `Illus.John Avon` still
 * has a word boundary where it needs one. CJK is left alone, which
 * matters for at least one artist here.
 */
export function flatten(text: string): string {
  return String(text ?? '')
    .normalize('NFD')
    // Combining marks, which is what NFD just split the accents into.
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

/**
 * The shortest artist name worth searching for.
 *
 * Three, because one artist here signs in three CJK characters.
 * Anything shorter would start matching fragments of other words.
 */
const SHORTEST_ARTIST = 3;

/**
 * Which of these artists the card credits, or ''.
 *
 * The candidates come from the index for one card name, so this is a
 * search through a couple of hundred strings, not a guess. Longest
 * match wins -- no artist in the set is a substring of another today,
 * and if one ever is, the more specific answer is the right one.
 */
export function artistIn(text: string, artists: string[]): string {
  const hay = ` ${flatten(text)} `;
  let best = '';
  for (const artist of artists ?? []) {
    const flat = flatten(artist);
    if (flat.length < SHORTEST_ARTIST) continue;
    if (flat.length <= best.length) continue;
    if (hay.includes(` ${flat} `)) best = artist;
  }
  return best;
}

/**
 * Four-digit years in the text, newest first.
 *
 * The copyright line is the one on a card -- and a card can carry two
 * of them, an original and a reprint date, so this returns all of
 * them rather than picking. 1993 is the earliest Magic year and
 * anything past next year is a misread rather than a date.
 */
export function yearsIn(text: string, until = 2100): number[] {
  const found = new Set<number>();
  for (const match of String(text ?? '').matchAll(/\b(19|20)\d{2}\b/g)) {
    const year = Number(match[0]);
    if (year >= 1993 && year <= until) found.add(year);
  }
  return [...found].sort((a, b) => b - a);
}

/**
 * Narrow a long list of printings by what the credit line says.
 *
 * The year is a filter and then, if it leaves nothing, not a filter.
 * A collector number can read as a year, a card can be reprinted
 * under a date its set does not share, and in both cases throwing
 * away the artist's shortlist to honour a bad year would be trading
 * a good answer for none.
 */
export function narrowByCredits<T extends {
  artist?: string; released_year?: number | null;
}>(printings: T[], years: number[]): T[] {
  if (!years.length) return printings;
  const wanted = new Set(years);
  const dated = printings.filter(
    (p) => p.released_year != null && wanted.has(p.released_year));
  return dated.length ? dated : printings;
}
