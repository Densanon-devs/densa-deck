import { useCallback, useRef, useState } from 'react';

import type { AppState } from '../lib/app-state.ts';
import {
  addBracket, deckSignature, readCached, refreshMissing, runAnalysis, writeCached,
  type DeckAnalysis,
} from '../lib/deck-analysis.ts';

export interface DeckInput {
  /** Cache identity: `local:<id>` / `pc:<id>`, or null to never cache. */
  key: string | null;
  text: string;
  name: string;
  format?: string;
}

/**
 * The analysis for whichever deck the screen is showing. See
 * lib/deck-analysis.ts for the cache rules.
 *
 *   show(deck)  -- the screen moved to this deck: load its cached analysis
 *                  for THIS version, if any, and quietly fill in parts the
 *                  cache lacks.
 *   run(deck)   -- ask the PC for everything, fresh, and keep the result.
 */
export function useDeckAnalysis(state: AppState) {
  const [analysis, setAnalysis] = useState<DeckAnalysis | null>(null);
  const [running, setRunning] = useState(false);
  const [problem, setProblem] = useState('');
  const deckRef = useRef<DeckInput | null>(null);
  // Which deck-version the latest request was for: a slow answer for a deck
  // the screen has since moved off must not land on the one now shown.
  const identity = (d: DeckInput | null) =>
    d ? `${d.key ?? ''}|${deckSignature(d.text, d.format)}` : '';
  const isCurrent = (id: string) => identity(deckRef.current) === id;

  const show = useCallback((deck: DeckInput | null) => {
    deckRef.current = deck;
    setAnalysis(null);
    setProblem('');
    if (!deck?.key || !deck.text.trim()) return;
    const id = identity(deck);
    const signature = deckSignature(deck.text, deck.format);
    void (async () => {
      const cached = await readCached(state, deck.key as string, signature);
      if (!cached || !isCurrent(id)) return;
      setAnalysis(cached);
      if (state.soloForever) return;
      const filled = await refreshMissing(state, deck.text, deck.name, cached,
        (a) => { if (isCurrent(id)) setAnalysis(a); })
        .catch(() => cached);
      if (filled !== cached) await writeCached(state, deck.key as string, filled);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const run = useCallback(async (deck: DeckInput) => {
    if (!deck.text.trim()) return null;
    deckRef.current = deck;
    const id = identity(deck);
    setRunning(true);
    setProblem('');
    try {
      const done = await runAnalysis(state, deck.text, deck.name,
        deckSignature(deck.text, deck.format),
        (a) => { if (isCurrent(id)) setAnalysis(a); });
      if (!done.results.basic) {
        if (isCurrent(id)) {
          setAnalysis(null);
          setProblem(`${done.errors.basic ?? 'The analysis did not come back'}. ` +
            'Analysis runs on your PC — it needs the card database, so it ' +
            'only works when your PC is reachable.');
        }
        return null;
      }
      if (deck.key) await writeCached(state, deck.key, done);
      return done;
    } finally {
      setRunning(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const bracket = useCallback(async (target: string) => {
    const deck = deckRef.current;
    if (!deck || !analysis) return;
    try {
      const next = await addBracket(state, deck.text, analysis, target);
      if (identity(deckRef.current) !== identity(deck)) return;
      setAnalysis(next);
      if (deck.key) await writeCached(state, deck.key, next);
    } catch (err) {
      setProblem((err as Error).message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, analysis]);

  return { analysis, running, problem, show, run, bracket };
}
