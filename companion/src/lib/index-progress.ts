/**
 * Saying how a card-index download is going.
 *
 * Reported as "it never actually seems to finish or update me on how
 * long or how far". Three separate causes, and only one of them was
 * the download:
 *
 *   * the download itself reported nothing until it was finished, so
 *     the slow half showed no movement at all;
 *   * the fetch and the read-back shared one bar, so when it did move
 *     it filled twice;
 *   * and Settings never rendered the progress it was already being
 *     handed, so from that screen there was nothing to see either way.
 *
 * The words live here because two screens need the same ones, and
 * because a percentage that lies is worse than no percentage.
 */

/** What the app-state hands out while an index fetch is running. */
export interface FetchProgress {
  stage: string;
  phase?: 'downloading' | 'reading';
  done: number;
  total: number;
}

/** Bytes as a person would say them. */
export function inMegabytes(bytes: number): string {
  const mb = Math.max(0, bytes) / 1_000_000;
  if (mb >= 100) return `${Math.round(mb)} MB`;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.max(0, Math.round(bytes / 1000))} kB`;
}

/**
 * How far along, 0 to 1, or null when that cannot be known.
 *
 * Null rather than zero: a bar pinned at the left for a minute reads
 * as a stall, and a spinner reads as work. The server does not always
 * send a length, so this happens.
 */
export function fractionDone(p: FetchProgress | null): number | null {
  if (!p || !(p.total > 0) || !(p.done >= 0)) return null;
  // Clamped. A server that under-reports its own length would
  // otherwise produce 114%, which makes the whole number look made up.
  return Math.min(1, p.done / p.total);
}

/** Which of the two files, in words rather than in a key. */
function whichFile(stage: string): string {
  if (stage === 'printings') return 'card printings';
  if (stage === 'cards') return 'card rules';
  return '';
}

/**
 * One line describing what is happening right now.
 *
 * Deliberately names the phase. "Downloading 34 of 75 MB" and
 * "Reading it in, 40%" are different enough that watching the second
 * one start over does not look like the first one failing.
 */
export function progressLine(p: FetchProgress | null): string {
  if (!p) return '';
  const file = whichFile(p.stage);
  if (p.stage === 'starting' || (!file && !p.total)) {
    return 'Starting…';
  }
  const share = fractionDone(p);
  if (p.phase === 'downloading') {
    return p.total > 0
      ? `Downloading ${file}: ${inMegabytes(p.done)} of `
        + `${inMegabytes(p.total)}`
      : `Downloading ${file}…`;
  }
  if (p.phase === 'reading') {
    return share === null
      ? `Reading in ${file}…`
      : `Reading in ${file}: ${Math.round(share * 100)}%`;
  }
  return share === null
    ? `Working on ${file}…`
    : `${file}: ${Math.round(share * 100)}%`;
}
