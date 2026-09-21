/**
 * When to go and look for new cards.
 *
 * Magic prints a set every few weeks and the index is a snapshot, so a
 * phone that downloaded once is wrong by the next prerelease. The desktop
 * has always handled this — a cheap manifest call, an Update button — and
 * the phone had nothing at all. A standalone phone could not get a new set
 * by any means.
 *
 * Two things decide when to check, and the earlier one wins:
 *
 *  * **A set came out.** Scryfall publishes release dates, including for
 *    sets that have not happened yet, so this does not have to be guessed
 *    or hard-coded — which matters, because a hard-coded calendar is wrong
 *    the moment Wizards moves a date, and nobody would notice.
 *  * **A quarter went by.** The floor, for everything a release date does
 *    not cover: promos, Secret Lairs, errata, and the release list itself
 *    being unavailable when we looked.
 *
 * Opt-in, and checking is not downloading. The check is a few hundred
 * bytes; the download is tens of megabytes on somebody's phone plan, and
 * that stays a decision they make with the set name in front of them.
 */

/** The floor: look at least this often regardless of the calendar. */
export const QUARTER_MS = 90 * 24 * 60 * 60 * 1000;

/**
 * Don't re-check within a day of the last look.
 *
 * Release day is one date but an app gets opened twenty times on it, and
 * "a set came out" stays true all day.
 */
export const COOLDOWN_MS = 24 * 60 * 60 * 1000;

export interface Release {
  code: string;
  /** Epoch ms. */
  at: number;
}

export interface CheckInputs {
  /** When we last asked Scryfall anything. 0 means never. */
  lastCheckedAt: number;
  now: number;
  /**
   * Sets we know about with their release dates. Past and future both: a
   * date that has passed SINCE the last check is the signal.
   */
  releases?: Release[];
  /** Off by default. This is opt-in. */
  enabled?: boolean;
  everyMs?: number;
}

export type CheckReason = 'never' | 'release' | 'quarter' | null;

/**
 * Whether to spend a manifest call now, and what prompted it.
 *
 * The reason comes back rather than a bare boolean because it is worth
 * saying out loud: "a new set came out" is a different message from "it
 * has been a while", and the first one earns a download the second does
 * not.
 */
export function dueForCheck({
  lastCheckedAt, now, releases = [], enabled = false, everyMs = QUARTER_MS,
}: CheckInputs): CheckReason {
  if (!enabled) return null;
  // Never looked. Not the same as overdue: there is nothing to compare
  // against, and it is the cheapest possible call.
  if (!lastCheckedAt) return 'never';
  if (now - lastCheckedAt < COOLDOWN_MS) return null;

  // A set whose release date fell between the last check and now. Dates
  // in the future are not yet news, and ones before the last check were
  // either already picked up or deliberately declined.
  const justOut = releases.some(({ at }) => at > lastCheckedAt && at <= now);
  if (justOut) return 'release';

  return now - lastCheckedAt >= everyMs ? 'quarter' : null;
}

/**
 * Which released sets this phone has no cards from.
 *
 * The honest question, and not the one that was tempting. Comparing the
 * bulk file's `updated_at` cannot answer it: Scryfall rebuilds those
 * files daily whether or not a single card changed, so a timestamp check
 * announces new cards every day and asks for a 74 MB download to prove
 * itself wrong. Within a week nobody reads the prompt.
 *
 * Asking which SETS are missing is exact, survives any number of
 * no-op rebuilds, and gives the prompt something worth saying — the name
 * of the set you have not got.
 *
 * Unreleased sets are excluded: a set that is spoiled but not out is not
 * something the phone is behind on. Prerelease weekend is the interesting
 * edge, and it is handled by the release date being the date Scryfall
 * publishes, not by us guessing.
 */
export function missingSets(
  held: Set<string>,
  releases: Release[],
  now: number,
  /*
    Sets a download has already proved this phone cannot hold.

    The index keeps English paper cards, and some released sets have
    none: Foreign Black Border, Fourth Edition Foreign Black Border,
    Chronicles FBB, Renaissance, Rinascimento -- all of them
    non-English printings of cards that exist in English elsewhere.
    Scryfall lists them as released, the index will never contain
    them, and so the phone reported "28 sets not on this phone"
    immediately after finishing a complete download and reported the
    same 28 for ever.

    Which is worse than a cosmetic wrong number: the prompt asks for
    78 MB to fix something no download can fix.
  */
  absent: Set<string> = new Set(),
): string[] {
  const out: string[] = [];
  for (const { code, at } of releases) {
    if (at > now) continue;
    const key = code.toLowerCase();
    if (!key || held.has(key) || absent.has(key)) continue;
    out.push(key);
  }
  return out;
}

/**
 * How long after a set's release day to still believe it is coming.
 *
 * Scryfall rebuilds the bulk files daily, so a set that is out and
 * not in today's file is a matter of hours rather than a permanent
 * absence. Two weeks is far more than that gap and far less than the
 * gap to the next set, so nothing real gets written off and nothing
 * imaginary survives a fortnight.
 */
export const SETTLE_MS = 14 * 24 * 60 * 60 * 1000;

/**
 * Which released sets a COMPLETE download did not bring.
 *
 * Only meaningful straight after one. The reasoning is: the file just
 * downloaded is everything Scryfall has that this index keeps, so a
 * set released a fortnight ago that still is not here is not late --
 * it is a set with no English paper cards in it, and no future
 * download will contain one either.
 *
 * Recomputed on every full refresh rather than accumulated, so a set
 * that later gains an English printing stops being written off
 * without anybody having to notice.
 */
export function absentAfterRefresh(
  held: Set<string>,
  releases: Release[],
  now: number,
  settleMs = SETTLE_MS,
): string[] {
  const out: string[] = [];
  for (const { code, at } of releases) {
    if (at > now - settleMs) continue;
    const key = code.toLowerCase();
    if (!key || held.has(key)) continue;
    out.push(key);
  }
  return out;
}

/** How long ago, in words, for a settings line rather than a log. */
export function lastCheckedInWords(at: number, now: number): string {
  if (!at) return 'never';
  const days = Math.floor((now - at) / (24 * 60 * 60 * 1000));
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days} days ago`;
  const months = Math.round(days / 30);
  return months <= 1 ? 'about a month ago' : `about ${months} months ago`;
}
