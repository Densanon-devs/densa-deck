/**
 * What this phone can do on its own, and what needs the PC.
 *
 * The split the app is built around: **the phone is the collection, the PC
 * is the analysis.** Everything about owning cards — scanning, filing,
 * grouping, searching, sorting, tagging, decks — works with no PC in reach
 * and no PC ever having existed. Everything that reasons ABOUT the
 * collection — analysing a deck, suggesting cards, finding combos, power
 * level, prices from a live catalogue — is the PC's job, offered when it is
 * there and absent when it is not.
 *
 * "Connected" deliberately means two things at once, because they fail
 * differently and a user can act on the difference:
 *
 *  * **Paired** — a PC exists for this phone at all. Not paired is not a
 *    fault; it is a phone being used standalone, which is a supported way
 *    to own the app. Nothing should nag about it.
 *  * **Reachable** — that PC is answering right now. Out of range IS
 *    temporary, and saying so is useful: the feature is coming back.
 *
 * Collapsing them loses the distinction between "you do not have this" and
 * "you cannot have this at the moment", which is the difference between an
 * upsell and a status line.
 */

export type Connection = 'connected' | 'offline' | 'unpaired' | 'unknown';

/** Why a PC-backed feature is unavailable, or null when it is available. */
export type Barrier = 'unpaired' | 'offline';

export interface Reach {
  connection: Connection;
  /** Whether a desktop has ever been paired with this phone. */
  paired: boolean;
}

/**
 * Whether the PC's analysis is available right now.
 *
 * Requires both halves. 'unknown' — the state before the first sync has
 * finished — counts as reachable IF paired: refusing there would blank the
 * analysis on every cold open for as long as the first round trip takes,
 * and then fill it in, which reads as the app changing its mind.
 */
export function canAnalyse({ connection, paired }: Reach): boolean {
  if (!paired) return false;
  return connection === 'connected' || connection === 'unknown';
}

/** What is standing in the way, for a screen that wants to say so. */
export function barrier(reach: Reach): Barrier | null {
  if (!reach.paired) return 'unpaired';
  return canAnalyse(reach) ? null : 'offline';
}

/**
 * One line explaining why, in words that match which problem it is.
 *
 * Never an error. Being unpaired is a choice and being out of range is
 * weather; neither is the user doing something wrong.
 */
export function explainBarrier(what: Barrier, feature = 'This'): string {
  return what === 'unpaired'
    ? `${feature} needs the desktop app. Your collection works without it.`
    : `${feature} needs your PC, which is not in reach right now.`;
}
