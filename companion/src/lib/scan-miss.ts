/**
 * Why a scan came to nothing, in words the person holding the card can
 * act on.
 *
 * Pure and here rather than in the screen so it can be tested: Node can
 * strip types from a .ts file but not JSX from a .tsx one, and a message
 * that is wrong in three different situations is exactly the kind of
 * thing that needs pinning.
 */

import { footerKeys } from './identify.ts';

/**
 * Why a scan on a phone with no PC came to nothing.
 *
 * Three different failures wore the same sentence, and they want three
 * different things from the user:
 *
 *   * nothing legible at all — a focus or light problem;
 *   * text, but no set code and number in it — the footer specifically
 *     was not read, usually glare on a foil or simply too far away,
 *     which is invisible when the name and rules text came back fine;
 *   * a key that was read and matched nothing — the printing is not in
 *     the index, and no amount of better photography will help.
 *
 * Saying which turns "it does not work" into something to act on, and
 * is what the desktop path has always done with `capture.text`.
 */
export function describeLocalMiss(text: string): string {
  const read = (text || '').replace(/\s+/g, ' ').trim();
  if (!read) {
    return 'Nothing legible in that picture. Try more light, or lock the '
      + 'focus once it looks sharp.';
  }
  const keys = footerKeys(read);
  if (!keys.length) {
    return `Read "${read.slice(0, 60)}" but no set code and number in it. `
      + 'The small line along the bottom edge is the part that matters — '
      + 'zoom in on it, or tilt the card away from the glare.';
  }
  const [setCode, number] = keys[0] ?? ['', ''];
  return `Read ${setCode.toUpperCase()} #${number}, which is not in this `
    + "phone's index. If it is a date-stamped prerelease, turn that on "
    + 'above.';
}
