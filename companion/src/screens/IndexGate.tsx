/**
 * The card index, before anything else.
 *
 * Nothing in this app works without it. Scanning has nothing to match a
 * photo against, searching has no cards to find, and a deck cannot be
 * built out of a catalogue that is not there — so the index is not a
 * setting, it is the thing being installed.
 *
 * It used to be a banner on the Scan tab, which reads as optional. A
 * tester spent a session photographing cards into an app with an empty
 * index, being told by it to try better lighting. This is that banner
 * turned into a door.
 *
 * Where it comes from is decided for you, because there is only one right
 * answer at any moment: a paired PC that is answering has it already and
 * hands it over in seconds, and everything else means fetching it from
 * Scryfall. That choice lives in `chooseSource`; this screen only shows
 * what is happening and refuses to get out of the way until it has.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AppState, IndexFetch } from '../lib/app-state.ts';
import { recordCrash } from '../lib/crash.ts';
import { SCRYFALL_CREDIT } from '../lib/index-source.ts';
import { VERSION } from '../lib/version.ts';

interface Props {
  state: AppState;
  /** Called once the index is complete and the app can be opened. */
  onReady: () => void;
  /** Settings, so a phone paired to a dead PC is not trapped here. */
  onSettings?: () => void;
}

export function IndexGate({ state, onReady, onSettings }: Props) {
  const [fetching, setFetching] = useState<IndexFetch | null>(null);
  const [rows, setRows] = useState(0);
  const [problem, setProblem] = useState('');
  const [started, setStarted] = useState(false);

  useEffect(() => state.subscribe((snap) => setFetching(snap.indexFetch ?? null)),
    [state]);

  useEffect(() => {
    void state.catalogueReady()
      .then(({ rows: have, ready }) => {
        setRows(have);
        if (ready) onReady();
      })
      .catch(() => {});
  }, [state, onReady]);

  const fetchIt = useCallback(async () => {
    setProblem('');
    setStarted(true);
    try {
      await state.startIndexFetch();
      const { ready, rows: have } = await state.catalogueReady();
      setRows(have);
      if (ready) onReady();
      else setProblem('That did not finish. Try again — it picks up from '
                      + 'where the last one stopped.');
    } catch (err) {
      setProblem(recordCrash(err, 'fetching the card index', false).message);
    } finally {
      setStarted(false);
    }
  }, [state, onReady]);

  const pct = fetching?.total
    ? Math.max(1, Math.round((fetching.done / fetching.total) * 100))
    : fetching ? 1 : 0;
  const busy = started || !!fetching;

  // Where it is coming from, once that has been decided. Null means the
  // round trip that decides has not answered yet, and naming a source
  // before then would be wrong about half the time.
  const where = !fetching ? ''
    : fetching.source === null ? 'Working out where to get it…'
      : fetching.source === 'desktop'
        ? `Getting ${fetching.stage} from your PC`
        : `Downloading ${fetching.stage} from Scryfall`;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>One thing first</Text>
      <Text style={styles.body}>
        Densa Deck needs the card index before it can do anything — every
        card in Magic, so your phone can recognise one and find one without
        asking anybody.
      </Text>

      {/*
        Partly-there is its own state and says so. "Half-downloaded" and
        "not started" want different words: one of them is nearly done.
      */}
      {rows > 0 && !busy ? (
        <Text style={styles.partial}>
          {rows.toLocaleString()} cards are already here, but the index is
          not finished. Carrying on will complete it.
        </Text>
      ) : null}

      {busy ? (
        <View style={styles.progress}>
          <ActivityIndicator color="#7db8e8" />
          <Text style={styles.stage}>{where || 'Starting…'}</Text>
          {pct > 0 ? <Text style={styles.pct}>{pct}%</Text> : null}
          <View style={styles.track}>
            <View style={[styles.fill, { width: `${Math.min(pct, 100)}%` }]} />
          </View>
        </View>
      ) : (
        <Pressable style={styles.go} onPress={() => void fetchIt()}>
          <Text style={styles.goText}>
            {rows > 0 ? 'Finish the download' : 'Get the card index'}
          </Text>
        </Pressable>
      )}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      <Text style={styles.note}>
        If your PC is on and reachable this takes seconds. Otherwise it comes
        straight from Scryfall — a large one-time download, so use wifi.
        Either way you only do this once, and afterwards the app works with
        no PC and no signal at all.
      </Text>

      {/*
        A phone paired to a PC that is switched off would otherwise be
        stuck here with no way to reach the setting that unpairs it.
      */}
      {onSettings && !busy ? (
        <Pressable onPress={onSettings}>
          <Text style={styles.settings}>Settings</Text>
        </Pressable>
      ) : null}

      <Text style={styles.credit}>{SCRYFALL_CREDIT}</Text>
      <Text style={styles.version}>Densa Deck companion {VERSION}</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { backgroundColor: '#0f1117', flex: 1 },
  content: { gap: 14, padding: 22, paddingTop: 40 },
  title: { color: '#e4e6eb', fontSize: 24, fontWeight: '700' },
  body: { color: '#b6bac4', fontSize: 15, lineHeight: 22 },
  partial: { color: '#d9ae62', fontSize: 14, lineHeight: 20 },
  progress: { gap: 8, marginTop: 6 },
  stage: { color: '#e4e6eb', fontSize: 15 },
  pct: { color: '#7db8e8', fontSize: 28, fontWeight: '700' },
  track: {
    backgroundColor: '#1d2433',
    borderRadius: 3,
    height: 6,
    overflow: 'hidden',
  },
  fill: { backgroundColor: '#2f6f9f', height: 6 },
  go: {
    alignItems: 'center',
    backgroundColor: '#2f6f9f',
    borderRadius: 10,
    marginTop: 8,
    paddingVertical: 14,
  },
  goText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  problem: { color: '#e53e3e', fontSize: 14, lineHeight: 20 },
  note: { color: '#8a8f9c', fontSize: 13, lineHeight: 19, marginTop: 4 },
  settings: { color: '#7db8e8', fontSize: 14, marginTop: 10 },
  credit: { color: '#6b7079', fontSize: 12, marginTop: 20 },
  version: { color: '#4d525c', fontSize: 12 },
});
