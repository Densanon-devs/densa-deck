/**
 * Is there a newer Densa Deck for this phone?
 *
 * The Table of War pattern, which the desktop follows too: one anonymous GET
 * of a static JSON on the toolkit site, compared against this build's own
 * version, and -- if newer -- a banner with a manual download link. Nothing
 * installs itself, and it is on by default with a switch in Settings.
 *
 * The phone had no check at all: every APK since 0.57 reached people only
 * because somebody told them. The download link in the feed must be a
 * PINNED tag (`.../releases/download/companion-vX.Y.Z/...`), never
 * `/releases/latest/`, which moves as soon as anything is released and
 * leaves a stale feed pointing at a file that no longer exists.
 */

export const MOBILE_FEED_URL = 'https://toolkit.densanon.com/densa-deck-mobile-version.json';

export interface AppUpdate {
  latest: string;
  url: string;
  releaseDate: string;
  changelog: string[];
}

/** -1, 0, 1 for dotted numeric versions; a missing part counts as 0. */
export function compareVersions(a: string, b: string): number {
  const pa = String(a).split('.').map((x) => parseInt(x, 10) || 0);
  const pb = String(b).split('.').map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d > 0 ? 1 : -1;
  }
  return 0;
}

/**
 * The update the feed offers this build, or null.
 *
 * Null for anything not strictly newer, and for a feed that is malformed or
 * whose link is not an https download -- a bad feed must not put a broken
 * or foreign link in front of somebody.
 */
export function updateFrom(feed: unknown, current: string): AppUpdate | null {
  if (!feed || typeof feed !== 'object') return null;
  const f = feed as Record<string, unknown>;
  const latest = typeof f.version === 'string' ? f.version.trim() : '';
  const url = typeof f.downloadUrl === 'string' ? f.downloadUrl.trim() : '';
  if (!latest || !/^https:\/\//i.test(url)) return null;
  if (compareVersions(latest, current) <= 0) return null;
  return {
    latest,
    url,
    releaseDate: typeof f.releaseDate === 'string' ? f.releaseDate : '',
    changelog: Array.isArray(f.changelog)
      ? f.changelog.filter((x): x is string => typeof x === 'string')
      : [],
  };
}
