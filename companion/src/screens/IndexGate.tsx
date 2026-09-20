/**
 * Setting the app up, in the order the questions actually depend on.
 *
 * Two things have to be settled before anything works, and one decides the
 * other: **which version you are running**, and **where the card index
 * comes from**. A phone with a PC gets the index off it in seconds; a
 * phone on its own downloads it from Scryfall, which is large.
 *
 * It used to ask only the second, and only implicitly — it picked a source
 * itself and started downloading. That was wrong twice over. A phone whose
 * PC was simply switched off got quietly committed to a 74 MB download it
 * never agreed to, while the screen cheerfully said "if your PC is on and
 * reachable this takes seconds" about a decision already taken. And a
 * phone that had a pairing restored by Android's auto-backup was never
 * asked which version it was at all — it just arrived here, downloading.
 *
 * So: which version first, then where the index comes from. The second
 * question mostly answers itself, and it is only put to the user when it
 * genuinely is a question — a PC that is not answering right now.
 *
 * Nothing in this app works without the index. Scanning has nothing to
 * match against, searching has no cards to find, and a deck cannot be
 * built out of a catalogue that is not there. So this is not a setting; it
 * is the thing being installed, and it does not get out of the way until
 * it is done.
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
import type { IndexSource } from '../lib/index-source.ts';
import { VERSION } from '../lib/version.ts';

interface Props {
  state: AppState;
  /** Called once the index is complete and the app can be opened. */
  onReady: () => void;
  /** Settings, so a phone paired to a dead PC is not trapped here. */
  onSettings?: () => void;
  /**
   * Go and pair with a desktop.
   *
   * Absent when there is nothing to pair with from here; present means the
   * "with a PC" answer has somewhere to lead.
   */
  onPairPc?: () => void;
  /** Commit to running with no PC, for ever. Remembered. */
  onStandalone?: () => Promise<void> | void;
  /** Whether this phone has already been told it has no PC. */
  standalone: boolean;
  /** Whether a desktop address is stored, answering or not. */
  paired: boolean;
}

/** Which question is on screen. */
type Step = 'version' | 'index';

export function IndexGate({
  state, onReady, onSettings, onPairPc, onStandalone, standalone, paired,
}: Props) {
  const [fetching, setFetching] = useState<IndexFetch | null>(null);
  const [rows, setRows] = useState(0);
  const [problem, setProblem] = useState('');
  const [started, setStarted] = useState(false);
  // Null until the probe answers. Naming a source before then would be
  // wrong about half the time, which is the mistake this screen is here
  // to stop making.
  const [pcAnswers, setPcAnswers] = useState<boolean | null>(null);

  /*
    A phone that has already answered the version question does not get
    asked it again: choosing standalone is an explicit decision and so is
    scanning a pairing QR code. Everything else starts at the top.

    Note that a STORED pairing is not on its own an answer. A pairing
    restored from a backup onto a phone whose index is missing is a phone
    at the start of setup, whatever the database says, and that is exactly
    the phone that was landing here mid-download.
  */
  const [step, setStep] = useState<Step>(standalone ? 'index' : 'version');

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

  // Asked once we are on the question it affects, and not before — it is a
  // round trip to a machine that is usually off.
  useEffect(() => {
    if (step !== 'index' || standalone) {
      setPcAnswers(standalone ? false : null);
      return;
    }
    let live = true;
    void state.desktopReachable()
      .then((yes) => { if (live) setPcAnswers(yes); })
      .catch(() => { if (live) setPcAnswers(false); });
    return () => { live = false; };
  }, [state, step, standalone]);

  const fetchIt = useCallback(async (prefer?: IndexSource) => {
    setProblem('');
    setStarted(true);
    try {
      await state.startIndexFetch(undefined, prefer);
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

  const where = !fetching ? ''
    : fetching.source === null ? 'Working out where to get it…'
      : fetching.source === 'desktop'
        ? `Getting ${fetching.stage} from your PC`
        : `Downloading ${fetching.stage} from Scryfall`;

  // ---------------------------------------------------------------- step 1

  if (step === 'version' && !busy) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
        <Text style={styles.title}>How will you use Densa Deck?</Text>
        <Text style={styles.body}>
          Your collection, decks and scanning work either way. A PC adds
          deck analysis, card suggestions and combo detection — and it hands
          over the card index in seconds instead of minutes.
        </Text>

        <Pressable
          style={styles.go}
          onPress={() => {
            // Already paired means the address is on file; there is
            // nothing to scan and the next question is the index.
            if (paired || !onPairPc) setStep('index');
            else onPairPc();
          }}
        >
          <Text style={styles.goText}>With a PC</Text>
        </Pressable>
        <Text style={styles.under}>
          {paired
            ? 'This phone already has a PC saved.'
            : 'Densa Deck runs on the PC and this phone pairs to it.'}
        </Text>

        <Pressable
          style={[styles.go, styles.goQuiet]}
          onPress={() => {
            void (async () => {
              try {
                await onStandalone?.();
                setStep('index');
              } catch (err) {
                setProblem(recordCrash(err, 'going standalone', false).message);
              }
            })();
          }}
        >
          <Text style={styles.goText}>This phone only</Text>
        </Pressable>
        <Text style={styles.under}>
          Everything about owning cards, with no PC and no signal. You can
          add a PC later from Settings.
        </Text>

        {problem ? <Text style={styles.problem}>{problem}</Text> : null}

        <Text style={styles.credit}>{SCRYFALL_CREDIT}</Text>
        <Text style={styles.version}>Densa Deck companion {VERSION}</Text>
      </ScrollView>
    );
  }

  // ---------------------------------------------------------------- step 2

  // The one case where the source is a real question rather than a
  // consequence: a PC was chosen, and it is not answering. Downloading the
  // whole thing from Scryfall is a fine answer, but it is a big one and it
  // is theirs to give.
  const pcChosenButSilent = !standalone && pcAnswers === false;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>The card index</Text>
      <Text style={styles.body}>
        Every card in Magic, so this phone can recognise one and find one
        without asking anybody. Densa Deck needs it before it can do
        anything.
      </Text>

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
      ) : pcAnswers === null && !standalone ? (
        <View style={styles.progress}>
          <ActivityIndicator color="#7db8e8" />
          <Text style={styles.stage}>Checking whether your PC is awake…</Text>
        </View>
      ) : pcChosenButSilent ? (
        <>
          <Text style={styles.partial}>
            Your PC is not answering, so it cannot hand the index over right
            now.
          </Text>
          <Pressable style={styles.go} onPress={() => setPcAnswers(null)}>
            <Text style={styles.goText}>Try my PC again</Text>
          </Pressable>
          <Text style={styles.under}>
            Open Densa Deck on the PC, with phone scanning switched on.
          </Text>
          <Pressable
            style={[styles.go, styles.goQuiet]}
            onPress={() => void fetchIt('scryfall')}
          >
            <Text style={styles.goText}>Download it here instead</Text>
          </Pressable>
          <Text style={styles.under}>
            Straight from Scryfall — a large one-time download, so use wifi.
          </Text>
        </>
      ) : (
        <>
          <Pressable style={styles.go} onPress={() => void fetchIt()}>
            <Text style={styles.goText}>
              {rows > 0 ? 'Finish the download' : 'Get the card index'}
            </Text>
          </Pressable>
          <Text style={styles.under}>
            {standalone
              ? 'Straight from Scryfall — a large one-time download, so use '
                + 'wifi. Afterwards the app works with no PC and no signal '
                + 'at all.'
              : 'From your PC, which takes a few seconds.'}
          </Text>
        </>
      )}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

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
  content: { gap: 12, padding: 22, paddingTop: 40, paddingBottom: 40 },
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
    marginTop: 10,
    paddingVertical: 14,
  },
  // The second answer is not the lesser one; it is just not the default.
  goQuiet: { backgroundColor: '#242b3a' },
  goText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  under: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  problem: { color: '#e53e3e', fontSize: 14, lineHeight: 20 },
  settings: { color: '#7db8e8', fontSize: 14, marginTop: 10 },
  credit: { color: '#6b7079', fontSize: 12, marginTop: 20 },
  version: { color: '#4d525c', fontSize: 12 },
});
