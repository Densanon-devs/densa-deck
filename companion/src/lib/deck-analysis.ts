/**
 * A deck's full analysis: run once per version of the deck, then kept.
 *
 * Two things the phone did not do. It showed only the structural analysis,
 * not the rest of what the desktop's Analyze view shows -- combos, near-
 * misses, bracket fit, and for Pro the goldfish and the matchup gauntlet.
 * And it forgot everything the moment the sheet closed, so reopening a deck
 * nobody had touched meant asking the PC again (and needing it on).
 *
 * The cache is one entry per deck, keyed by a fingerprint of exactly what
 * was analysed: the decklist text and the format. Change a card and the
 * fingerprint changes, the entry no longer matches, and the next analysis
 * replaces it -- "a new version clears". Nothing expires on a clock; an
 * unchanged deck's numbers are still its numbers next month.
 */

import { ProRequired } from './client.ts';

export const ANALYSIS_CACHE_VERSION = 1;

/** The parts, in the order the sheet shows them. */
export type Part = 'basic' | 'combos' | 'nearMiss' | 'goldfish' | 'gauntlet';
export const PARTS: Part[] = ['basic', 'combos', 'nearMiss', 'goldfish', 'gauntlet'];

export interface DeckAnalysis {
  v: number;
  signature: string;
  at: string;
  results: Partial<Record<Part, unknown>>;
  /** Bracket fit, per target bracket asked about. */
  brackets: Record<string, unknown>;
  /** Parts the desktop refused as Pro on this tier. */
  locked: Part[];
  /** Parts that failed for any other reason, with the message. */
  errors: Partial<Record<Part, string>>;
}

export interface AnalysisApi {
  analyze(text: string, name: string): Promise<unknown>;
  combos(text: string): Promise<unknown>;
  nearMissCombos(text: string): Promise<unknown>;
  goldfish(text: string, name: string): Promise<unknown>;
  gauntlet(text: string, name: string): Promise<unknown>;
  bracketFit(text: string, target: string): Promise<unknown>;
}

export interface CacheIo {
  readAnalysisCache(key: string): Promise<string | undefined>;
  writeAnalysisCache(key: string, value: string): Promise<void>;
}

/**
 * What identifies a version of a deck. Whitespace-insensitive -- re-saving
 * the same list with a trailing blank line is not a new deck -- but
 * everything else counts: a changed count, a card, a zone, the format.
 */
export function deckSignature(text: string, format = 'commander'): string {
  const normal = text
    .split(/\r?\n/)
    .map((l) => l.trim().replace(/\s+/g, ' '))
    .filter(Boolean)
    .join('\n');
  // FNV-1a, twice with different seeds for 64 bits of spread. Not security:
  // it only has to notice that the deck changed.
  const fnv = (seed: number) => {
    let h = seed >>> 0;
    const s = `${format.toLowerCase()}\n${normal}`;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16).padStart(8, '0');
  };
  return fnv(0x811c9dc5) + fnv(0x01234567);
}

export function cacheKey(deckKey: string): string {
  return `analysis:${deckKey}`;
}

export function emptyAnalysis(signature: string): DeckAnalysis {
  return {
    v: ANALYSIS_CACHE_VERSION, signature, at: new Date().toISOString(),
    results: {}, brackets: {}, locked: [], errors: {},
  };
}

/** The cached analysis for this deck, only if it is of this version. */
export async function readCached(
  io: CacheIo, deckKey: string, signature: string,
): Promise<DeckAnalysis | null> {
  let raw: string | undefined;
  try {
    raw = await io.readAnalysisCache(cacheKey(deckKey));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as DeckAnalysis;
    if (parsed.v !== ANALYSIS_CACHE_VERSION || parsed.signature !== signature) return null;
    if (!parsed.results || !parsed.results.basic) return null;
    return { ...emptyAnalysis(signature), ...parsed };
  } catch {
    return null;
  }
}

export async function writeCached(io: CacheIo, deckKey: string, a: DeckAnalysis) {
  try {
    await io.writeAnalysisCache(cacheKey(deckKey), JSON.stringify(a));
  } catch {
    // A cache that cannot be written costs a re-run later, nothing more.
  }
}

/** Forget a deck's analysis -- e.g. when the deck itself is deleted. */
export async function clearCached(io: CacheIo, deckKey: string) {
  try {
    await io.writeAnalysisCache(cacheKey(deckKey), '');
  } catch {
    /* as above */
  }
}

/**
 * Run every part, reporting each as it lands.
 *
 * The structural analysis first -- it is what the sheet opens on, and if
 * the PC cannot do that it cannot do the rest. Then the others together.
 * A Pro refusal marks the part locked (the sheet says "Pro") rather than
 * failed; any other failure is kept per part so one bad section does not
 * blank the sheet. Only a complete run with a basic result is cached.
 */
export async function runAnalysis(
  api: AnalysisApi,
  text: string,
  name: string,
  signature: string,
  onUpdate: (a: DeckAnalysis) => void,
): Promise<DeckAnalysis> {
  const a = emptyAnalysis(signature);
  const publish = () => onUpdate({ ...a, results: { ...a.results },
                                   locked: [...a.locked], errors: { ...a.errors } });

  const run = async (part: Part, call: () => Promise<unknown>) => {
    try {
      a.results[part] = await call();
    } catch (err) {
      if (err instanceof ProRequired || (err as { proRequired?: boolean })?.proRequired) {
        a.locked.push(part);
      } else {
        a.errors[part] = (err as Error)?.message ?? String(err);
      }
    }
    publish();
  };

  await run('basic', () => api.analyze(text, name));
  if (!a.results.basic) return a;   // nothing else is worth asking

  await Promise.all([
    run('combos', () => api.combos(text)),
    run('nearMiss', () => api.nearMissCombos(text)),
    run('goldfish', () => api.goldfish(text, name)),
    run('gauntlet', () => api.gauntlet(text, name)),
  ]);
  a.at = new Date().toISOString();
  return a;
}

/** Bracket fit for one target, added to an analysis (and its cache). */
export async function addBracket(
  api: AnalysisApi, text: string, a: DeckAnalysis, target: string,
): Promise<DeckAnalysis> {
  const result = await api.bracketFit(text, target);
  return { ...a, brackets: { ...a.brackets, [target]: result } };
}

/**
 * Re-ask for the parts a cached analysis is missing.
 *
 * A cached run can hold a part that failed (the PC dropped mid-run) or one
 * refused as Pro on a tier that has since been upgraded. Neither should
 * stick until the deck happens to change, so opening a cached analysis
 * re-runs just those, quietly, and keeps everything else as it was.
 */
export async function refreshMissing(
  api: AnalysisApi,
  text: string,
  name: string,
  cached: DeckAnalysis,
  onUpdate: (a: DeckAnalysis) => void,
): Promise<DeckAnalysis> {
  const wanted = PARTS.filter((p) => p !== 'basic' &&
    (cached.locked.includes(p) || cached.errors[p] !== undefined || !(p in cached.results)));
  if (!wanted.length) return cached;
  const a: DeckAnalysis = {
    ...cached, results: { ...cached.results }, errors: { ...cached.errors },
    locked: cached.locked.filter((p) => !wanted.includes(p)),
  };
  for (const p of wanted) delete a.errors[p];
  const calls: Record<Exclude<Part, 'basic'>, () => Promise<unknown>> = {
    combos: () => api.combos(text),
    nearMiss: () => api.nearMissCombos(text),
    goldfish: () => api.goldfish(text, name),
    gauntlet: () => api.gauntlet(text, name),
  };
  await Promise.all(wanted.map(async (p) => {
    try {
      a.results[p] = await calls[p as Exclude<Part, 'basic'>]();
    } catch (err) {
      if (err instanceof ProRequired || (err as { proRequired?: boolean })?.proRequired) {
        a.locked.push(p);
      } else {
        a.errors[p] = (err as Error)?.message ?? String(err);
      }
    }
    onUpdate({ ...a, results: { ...a.results }, locked: [...a.locked], errors: { ...a.errors } });
  }));
  return a;
}
