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
 *
 * Exact first, then one character out.
 *
 * A proper noun set in six-point italic on a beige background is
 * where OCR slips, and it slips by a letter: Frazier for Frazier with
 * a misread z, Honore for Honore with a stray mark. An exact-only
 * search throws away the whole card for that.
 *
 * This is the fuzzy matching the card-name path refuses, and the
 * difference is what a wrong answer costs. A fuzzy NAME match lands
 * on a different real card and files it. A fuzzy ARTIST match lands
 * on a shortlist of pictures that a person then looks at, and
 * nothing here is ever auto-added. One edit, not two, and only
 * against names long enough that one edit is not most of the word.
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
  if (best) return best;
  return nearlyIn(hay, artists ?? []);
}

/**
 * The shortest name worth allowing a typo in.
 *
 * Eight. One edit in a six-letter word is most of the word: Chippy
 * and Chappy are not the same person, and there is a real artist
 * called each of the first two. Every two-part name clears this
 * easily -- "john avon" is nine -- and the short single-word
 * signatures still match exactly, which is what they need.
 */
const SHORTEST_FUZZY = 8;

/**
 * One artist whose name appears in the text but for a single
 * character, or '' if none or more than one does.
 *
 * Ambiguity returns nothing rather than a coin toss: two artists both
 * one letter away from the same scrap of text means the text does not
 * say which, and a shortlist built on the wrong one is worse than the
 * refusal it replaced.
 */
function nearlyIn(hay: string, artists: string[]): string {
  const words = hay.trim().split(' ').filter(Boolean);
  let found = '';
  for (const artist of artists) {
    const flat = flatten(artist);
    if (flat.length < SHORTEST_FUZZY) continue;
    const span = flat.split(' ').length;
    let hit = false;
    for (let i = 0; i + span <= words.length && !hit; i += 1) {
      hit = withinOneEdit(words.slice(i, i + span).join(' '), flat);
    }
    if (!hit) continue;
    // A second candidate means the answer is not knowable from this.
    if (found && found !== artist) return '';
    found = artist;
  }
  return found;
}

/**
 * Whether two strings differ by at most one insertion, deletion or
 * substitution.
 *
 * Not a general edit distance -- this only ever needs to know "one or
 * not", and a full matrix over two hundred artists per frame is work
 * nobody asked for.
 */
export function withinOneEdit(a: string, b: string): boolean {
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > 1) return false;
  const [short, long] = a.length <= b.length ? [a, b] : [b, a];
  let i = 0;
  let j = 0;
  let slips = 0;
  while (i < short.length && j < long.length) {
    if (short[i] === long[j]) {
      i += 1;
      j += 1;
      continue;
    }
    slips += 1;
    if (slips > 1) return false;
    // Same length means a substitution, so both advance. Different
    // lengths means the longer one has a character the shorter does
    // not, so only it advances.
    if (short.length === long.length) i += 1;
    j += 1;
  }
  // A trailing character left over on the longer string is the one
  // allowed edit, if it has not been spent.
  return slips + (long.length - j) + (short.length - i) <= 1;
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

/**
 * The end of what was read, for putting in a message.
 *
 * The TAIL, not the head. The credit line is the last thing on a
 * card, so quoting the first sixty characters shows the title and the
 * type line -- the part that plainly worked -- and hides the only
 * region in question.
 */
export function tailOf(text: string, keep = 60): string {
  const read = String(text ?? '').replace(/\s+/g, ' ').trim();
  if (!read) return '';
  return read.length > keep ? `...${read.slice(-keep)}` : read;
}
