/**
 * Where the card index comes from.
 *
 * Two sources for the same data, and the choice is a preference rather
 * than a fallback: the PC when it is there, Scryfall when it is not.
 *
 * The PC is enormously better when available — the whole printing index is
 * 7 MB over the LAN in under a second, against 74 MB from the internet
 * that has to be inflated and parsed on the phone. But it must never be
 * REQUIRED, because a phone-only customer has no PC to offload to, and
 * scanning is the first thing they try.
 *
 * So this is the "offload if it ever connects" rule, written down once:
 * ask the PC, and go to Scryfall only when there is no PC to ask.
 */

export type IndexSource = 'desktop' | 'scryfall';

export interface Reachability {
  /** Whether a desktop is paired AND answering right now. */
  desktopAvailable: boolean;
}

/**
 * Which source to use for a fetch starting now.
 *
 * Deliberately not sticky. A pull that began on Scryfall and finishes
 * after the PC comes back should still finish — the rows are the same rows
 * — but the next one takes the fast road.
 */
export function chooseSource({ desktopAvailable }: Reachability): IndexSource {
  return desktopAvailable ? 'desktop' : 'scryfall';
}

/** What to tell someone who is about to wait. */
export function describeSource(source: IndexSource): string {
  return source === 'desktop'
    ? 'Fetching the card index from your PC — a few seconds.'
    : 'Fetching the card index from Scryfall. It is a large one-time '
      + 'download, so use wifi. After this, scanning works with no PC at all.';
}

/**
 * What is downloading, said so that two steps read as two steps.
 *
 * The index is two files: every printing, then the rules text. They were
 * labelled "printings" and "cards", which are the same word to anyone who
 * does not already know the shape of Scryfall's bulk data -- so when the
 * first finished and the second began, at a fresh percentage, it read as
 * the whole download starting over.
 *
 * Numbering them is the fix. "Step 2 of 2" cannot be mistaken for a
 * restart, whatever the bar does.
 */
export function describeStage(
  stage: string,
  source: IndexSource | null,
  /*
    Which half of the work on this file.

    A bulk file is fetched whole and then read back, and the bar
    fills once for each. Unnamed, the second pass looks like the
    first one starting over -- and since the fetch itself used to
    report nothing at all, what people saw was a bar that sat
    still for two minutes and then raced. Naming the phase is
    most of the fix.
  */
  phase?: 'downloading' | 'reading',
): string {
  if (source === null) return 'Working out where to get it\u2026';
  const from = source === 'desktop' ? 'from your PC' : 'from Scryfall';
  const what = stage === 'printings' ? 'every printing' : 'card text';
  const step = stage === 'printings' ? 'Step 1 of 2' : 'Step 2 of 2';
  if (phase === 'reading') return `${step} \u00b7 reading in ${what}`;
  return `${step} \u00b7 `
    + `${phase === 'downloading' ? 'downloading ' : ''}${what} ${from}`;
}

/**
 * Attribution, which is not optional.
 *
 * The card data is Scryfall's, and every surface that shows it says so.
 */
export const SCRYFALL_CREDIT =
  'Card data from Scryfall. Not affiliated with Wizards of the Coast.';
