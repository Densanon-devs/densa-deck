/**
 * Making a set symbol visible on a dark screen.
 *
 * Scryfall's symbols are drawn for paper and for white web pages: they
 * are black. On the pick list they came out as black shapes on a black
 * background — present, occupying space, and unreadable.
 *
 * The obvious fix does not work. `SvgUri`'s `color` prop only reaches
 * elements that ask for `currentColor`, and these ask for nothing of
 * the sort; passing `fill` does not override a `fill` the path already
 * carries. So the markup itself is rewritten before it is rendered.
 *
 * Three shapes exist in the wild and each needs different handling,
 * which is why this is not a one-line replace. Checked against real
 * files rather than assumed:
 *
 *   * `fill="#000"` on the path — most sets. Swap it.
 *   * no `fill` at all — ZNC, E01, MKM and others. SVG's default fill
 *     is black, so there is nothing to swap; the colour has to be put
 *     on the root instead and inherited.
 *   * a real palette — The List's symbol is the multi-colour Magic
 *     logo, with `fill="none"` and `fill="white"` and three blues.
 *     Painting that one white erases it. Left alone.
 */

/** Fills that mean "the default black", and may be recoloured. */
const BLACKS = new Set(['#000', '#000000', 'black', '#010101']);

/** Every `fill="..."` value in the markup, lowercased. */
function fillsIn(svg: string): string[] {
  return [...(svg || '').matchAll(/fill="([^"]*)"/gi)]
    .map((m) => (m[1] ?? '').trim().toLowerCase());
}

/**
 * Whether this symbol is a coloured mark that must not be repainted.
 *
 * Anything carrying a fill that is neither black nor `none` was drawn
 * in colour on purpose.
 */
export function isColoured(svg: string): boolean {
  return fillsIn(svg).some((f) => f && f !== 'none' && !BLACKS.has(f));
}

/**
 * The same symbol, drawn in `colour`.
 *
 * Returns the markup unchanged when it is a coloured mark, or when
 * there is nothing recognisable to work with — a symbol in the wrong
 * colour is better than no symbol, and far better than a crash in a
 * list somebody is trying to read.
 */
export function recolourSvg(svg: string, colour: string): string {
  const text = svg || '';
  if (!text.includes('<svg')) return text;
  if (isColoured(text)) return text;

  const fills = fillsIn(text);
  if (fills.some((f) => BLACKS.has(f))) {
    // Swap the black ones and leave `fill="none"` alone: that is a
    // deliberate hole in the shape, and filling it makes a blob.
    return text.replace(/fill="([^"]*)"/gi, (whole, value: string) =>
      (BLACKS.has(String(value).trim().toLowerCase())
        ? `fill="${colour}"`
        : whole));
  }

  // No fill anywhere, so every path is taking SVG's default black.
  // Put the colour on the root and let it inherit.
  return text.replace(/<svg\b/i, `<svg fill="${colour}"`);
}

/**
 * The colour a set symbol is printed in, which is its rarity.
 *
 * Worth saying because it is easy to assume otherwise: Scryfall's
 * `icon_svg_uri` is a single-colour silhouette and carries no rarity
 * at all. Painting them white lost nothing, because there was never
 * anything there to lose — but it also threw away the chance to say
 * something the phone already knows. The index stores a rarity per
 * printing, and on a real card that is exactly what the symbol's
 * colour means.
 *
 * So the pick list shows what the card shows: gold for rare, silver
 * for uncommon, the mythic orange, and purple for the special frames.
 *
 * Common is the one that cannot be faithful. It is printed black, and
 * black on this screen is the unreadable symbol this whole module
 * exists to fix, so it takes a plain light grey — which reads as "no
 * special colour", the same thing black means on paper.
 */
export function rarityColour(rarity: string): string {
  switch ((rarity || '').trim().toLowerCase()) {
    case 'mythic': return '#E0662B';
    case 'rare': return '#D3B25A';
    case 'uncommon': return '#A9B3BD';
    case 'special':
    case 'bonus': return '#B07FD0';
    // Common, and anything the catalogue has not got a rarity for.
    default: return '#D8DDE3';
  }
}
