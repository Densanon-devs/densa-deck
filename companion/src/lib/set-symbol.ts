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
