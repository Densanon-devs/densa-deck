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
import type { LocalIdentifyResult } from './identify.ts';
import type { ScanResult } from './scanner.ts';

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
export function describeLocalMiss(text: string, reason = ''): string {
  const read = (text || '').replace(/\s+/g, ' ').trim();
  if (!read) {
    return 'Nothing legible in that picture. Try more light, or lock the '
      + 'focus once it looks sharp.';
  }
  // The matcher's own account first, whenever it has one. It knows
  // what it looked up and what came back; this function only knows what
  // the text looks like. Checking the keys first meant a card the name
  // fallback had something to say about -- "828 printings" -- was told
  // instead that its footer was unreadable, which was true and not the
  // point.
  if (reason) return reason;
  const keys = footerKeys(read);
  if (!keys.length) {
    // The TAIL, not the head. The footer is the last thing on a card,
    // so quoting the first sixty characters showed the title and the
    // type line -- the part that plainly worked -- and hid the only
    // region in question. It sent me looking at glare on the artwork
    // twice.
    const tail = read.length > 60 ? `...${read.slice(-60)}` : read;
    return `Read "${tail}" but no set code and number in it. `
      + 'The small line along the bottom edge is the part that matters — '
      + 'zoom in on it, or tilt the card away from the glare.';
  }
  const [setCode, number] = keys[0] ?? ['', ''];
  return `Read ${setCode.toUpperCase()} #${number}, which is not in this `
    + "phone's index. If it is a date-stamped prerelease, turn that on "
    + 'above.';
}

/**
 * The phone's answer, in the shape the picker already speaks.
 *
 * The screen has shown a list of printings for the desktop's ambiguous
 * reads since the beginning. The phone reached the same kind of answer
 * — "it is one of these seven" — and had no way to say it, because
 * `identifyOffline` returns null whenever it cannot auto-file and the
 * candidates went in the bin on the way out.
 *
 * Two fields are invented because the phone index does not carry them.
 * `finishes` matters: `defaultFinish` falls back to nonfoil on an empty
 * list, which would file a foil as an ordinary copy and quietly
 * misprice it, so the foil hint from the footer star is turned into a
 * real option here. `set_name` is cosmetic and the picker shows the set
 * CODE, which the index does have.
 */
export function asScanResult(local: LocalIdentifyResult): ScanResult {
  const foil = local.identity.foilHint;
  return {
    confidence: local.autoAddable ? 'exact' : 'ambiguous',
    auto_addable: local.autoAddable,
    suggested_finish: foil ? 'foil' : 'nonfoil',
    foil_detected: foil,
    candidates: local.candidates.map((row) => ({
      printing_id: row.printing_id,
      name: row.name,
      set_code: row.set_code,
      set_name: '',
      collector_number: row.collector_number,
      finishes: foil ? ['nonfoil', 'foil'] : ['nonfoil'],
    })),
  };
}
