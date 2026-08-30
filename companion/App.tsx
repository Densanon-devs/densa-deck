/**
 * The companion, assembled.
 *
 * Everything here is wiring: open the database, find out whether this phone is
 * paired, and show a screen. All the decisions live in `src/lib`, which is
 * plain TypeScript and covered by the Node suite — a screen that made a
 * decision would be a decision nothing could test.
 *
 * The tab bar is not decoration. A screen that nothing navigates to is not
 * shipped at all: Metro bundles from the entry point outward, so an unreached
 * screen is silently absent from the APK. The deck and scan screens were
 * exactly that until this file learned to open them.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  AppState as ForegroundState,
  Pressable,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {
  SafeAreaProvider,
  useSafeAreaInsets,
} from 'react-native-safe-area-context';

import { AppState, buildAppState } from './src/lib/app-state.ts';
import type { AppSnapshot } from './src/lib/app-state.ts';
import type { StackRow } from './src/lib/store.ts';
import { cameBackToForeground } from './src/lib/permission.ts';
import type { Phase } from './src/lib/permission.ts';
import { describeConnection } from './src/lib/status.ts';
import type { Crash } from './src/lib/crash.ts';
import { installGlobalErrorTrap, onCrash, recordCrash } from './src/lib/crash.ts';
import type { Pairing } from './src/lib/client.ts';
import { DeckStore } from './src/lib/decks.ts';
import { deviceId, loadPairing, savePairing } from './src/lib/pairing.ts';
import { openDeviceDatabase } from './src/lib/sqlite.ts';
import { uuid } from './src/lib/uuid.ts';
import { LocalStore } from './src/lib/store.ts';
import { ErrorBoundary, CrashScreen } from './src/screens/Boundary.tsx';
import { CardScreen } from './src/screens/Card.tsx';
import { CollectionScreen } from './src/screens/Collection.tsx';
import { OverlapsScreen } from './src/screens/Overlaps.tsx';
import { ConnectionScreen } from './src/screens/Connection.tsx';
import { DeckListScreen, DeckScreen } from './src/screens/Decks.tsx';
import { PcDecksScreen } from './src/screens/PcDecks.tsx';
import { PairScreen } from './src/screens/Pair.tsx';
import { ScanScreen } from './src/screens/Scan.tsx';
import { WishlistScreen } from './src/screens/Wishlist.tsx';

// Before the first render, so a failure during startup is shown rather than
// taking the process down with it.
installGlobalErrorTrap();

type Tab = 'collection' | 'decks' | 'pc' | 'overlaps' | 'wishlist' | 'scan';

type Phase =
  | { kind: 'starting' }
  | { kind: 'pairing'; reason?: string }
  | { kind: 'ready'; state: AppState; decks: DeckStore };

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'collection', label: 'Cards' },
  { id: 'decks', label: 'Decks' },
  // The PC's decks are a different set from this phone's — versioned,
  // analysed, built at a desk — so they get their own tab rather than being
  // mixed into a list where you cannot tell which machine one lives on.
  { id: 'pc', label: 'PC' },
  { id: 'overlaps', label: 'Shared' },
  { id: 'wishlist', label: 'Wishlist' },
  { id: 'scan', label: 'Scan' },
];

/**
 * The app runs edge to edge with the system bars hidden — see
 * `plugins/with-immersive.js`. Android 15 forces edge-to-edge on anything
 * targeting SDK 35+, so this is not a style choice: content draws under the
 * status bar and the navigation buttons whatever the app does, and the only
 * question is whether it accounts for that. It did not, so the offline banner
 * sat behind the clock and the tab bar behind the gesture pill.
 *
 * `SafeAreaView` from react-native does nothing on Android — it is an iOS
 * notch shim. The insets have to come from the provider below.
 */
export default function App() {
  return (
    <SafeAreaProvider>
      <Shell />
    </SafeAreaProvider>
  );
}

function Shell() {
  const insets = useSafeAreaInsets();
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' });
  const [store, setStore] = useState<LocalStore | null>(null);
  const [tab, setTab] = useState<Tab>('collection');
  const [openDeck, setOpenDeck] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null);
  const [fatal, setFatal] = useState<Crash | null>(null);
  const [showConnection, setShowConnection] = useState(false);
  const [openCard, setOpenCard] = useState<StackRow | null>(null);

  useEffect(
    () => onCrash((crash) => crash.fatal && setFatal(crash)),
    [],
  );

  const connect = useCallback(async (local: LocalStore, pairing: Pairing,
                                     decks?: DeckStore) => {
    const device = await deviceId(local, uuid);
    // The deck store goes in HERE rather than only to the screens, so decks
    // and results arriving from the PC are applied rather than remembered
    // and ignored.
    const state = buildAppState(local, pairing, device, uuid, undefined, decks);
    state.subscribe((next) => {
      setSnapshot(next);
      if (next.connection === 'unpaired') {
        // The desktop revoked this phone; there is nothing to retry, so say
        // so and send the user back to pairing rather than looping.
        setPhase({ kind: 'pairing', reason: next.lastError });
      }
    });
    // Deliberately not awaited: a slow or absent desktop must not hold up the
    // first paint of a screen whose data is already local. Not being awaited
    // is exactly why it needs its own catch — nothing else would ever see it.
    void state.sync().catch((err) => recordCrash(err, 'first sync', false));
    return state;
  }, []);

  /**
   * Catch up whenever the app comes back to the foreground.
   *
   * Whether the PC is reachable is only ever decided by a sync, and a sync
   * only happened when a screen asked for one. So walking back into range
   * and reopening the app left it insisting it was offline until you
   * happened to find a screen with a pull-to-refresh — the same trap as the
   * camera permission, one level up, and affecting every screen at once.
   *
   * Best-effort on purpose: being unreachable is a state this app is built
   * to sit in, not an error to report.
   */
  useEffect(() => {
    const phase = { current: 'active' as Phase };
    const subscription = ForegroundState.addEventListener('change', (next) => {
      const previous = phase.current;
      phase.current = next as Phase;
      if (cameBackToForeground(previous, next as Phase)) {
        void state.sync().catch(() => undefined);
      }
    });
    return () => subscription.remove();
  }, [state]);

  useEffect(() => {
    let live = true;
    void (async () => {
      try {
        const database = await openDeviceDatabase();
        const local = new LocalStore(database);
        await local.init();
        if (!live) return;
        setStore(local);

        const pairing = await loadPairing(local);
        if (!pairing) {
          setPhase({ kind: 'pairing' });
          return;
        }
        // One store, shared by the sync engine and the screens. Two would
        // be two views of the same table, which works, but the engine has
        // to have one at all or deck events land nowhere.
        const deckStore = new DeckStore(database);
        setPhase({
          kind: 'ready',
          state: await connect(local, pairing, deckStore),
          decks: deckStore,
        });
      } catch (err) {
        // Rejecting here used to leave "Opening your collection…" on screen
        // forever, which reads as a hang and reports nothing.
        if (live) setFatal(recordCrash(err, 'opening the collection'));
      }
    })();
    return () => {
      live = false;
    };
  }, [connect]);

  // A floor under each inset: the bars are hidden, so the system reports
  // nothing for them, but a punch-hole camera still occupies the top of the
  // screen and a swipe brings the bars back on top of whatever is there.
  const status = describeConnection(
    snapshot ?? { connection: 'unknown', pendingEdits: 0 },
  );

  const frame = {
    paddingTop: Math.max(insets.top, 10),
    paddingBottom: Math.max(insets.bottom, 0),
    paddingLeft: insets.left,
    paddingRight: insets.right,
  };

  if (fatal) {
    return (
      <View style={[styles.app, frame]}>
        <StatusBar hidden />
        <CrashScreen crash={fatal} onDismiss={() => setFatal(null)} />
      </View>
    );
  }

  return (
    <View style={[styles.app, { paddingTop: frame.paddingTop, paddingLeft: frame.paddingLeft, paddingRight: frame.paddingRight }]}>
      <StatusBar hidden />
      {/*
        Always here, never conditional. "Nothing is wrong" and "I have not
        checked" look identical when both are silence, and they are entirely
        different situations to be in while filing a box of cards.
      */}
      {phase.kind === 'ready' ? (
        <Pressable
          style={[styles.banner, styles[`banner_${status.tone}`]]}
          onPress={() => setShowConnection((open) => !open)}
        >
          <View style={[styles.dot, styles[`dot_${status.tone}`]]} />
          <Text style={[styles.bannerText, styles[`text_${status.tone}`]]}>
            {status.headline}
          </Text>
          <Text style={styles.bannerHint}>
            {showConnection ? 'Close' : 'Details'}
          </Text>
        </Pressable>
      ) : null}

      <ErrorBoundary where={`the ${tab} screen`}>
      <View style={styles.body}>
        {phase.kind === 'ready' && showConnection ? (
          <ConnectionScreen
            state={phase.state}
            onClose={() => setShowConnection(false)}
          />
        ) : null}

        {phase.kind === 'starting' ? (
          <View style={styles.centre}>
            <Text style={styles.muted}>Opening your collection…</Text>
          </View>
        ) : null}

        {phase.kind === 'pairing' ? (
          <PairScreen
            reason={phase.reason}
            onPaired={async (pairing) => {
              // Returning quietly used to make a real failure — the database
              // never opened — look like a button that does nothing.
              if (!store) {
                throw new Error(
                  'The local collection is not open yet. Give it a moment ' +
                    'and try again.',
                );
              }
              await savePairing(store, pairing);
              const database = await openDeviceDatabase();
              // Built BEFORE connecting, and handed to it. Built after, the
              // engine would have no deck store on the one run that matters
              // most — a phone's first sync is when the PC's whole deck
              // history arrives, and it would have gone nowhere.
              const deckStore = new DeckStore(database);
              setPhase({
                kind: 'ready',
                state: await connect(store, pairing, deckStore),
                decks: deckStore,
              });
            }}
          />
        ) : null}

        {phase.kind === 'ready' && !showConnection && tab === 'collection' ? (
          openCard ? (
            <CardScreen
              state={phase.state}
              stack={openCard}
              onClose={() => setOpenCard(null)}
            />
          ) : (
            <CollectionScreen state={phase.state} onOpenCard={setOpenCard} />
          )
        ) : null}

        {phase.kind === 'ready' && !showConnection && tab === 'decks' ? (
          openDeck ? (
            <DeckScreen
              state={phase.state}
              decks={phase.decks}
              deckId={openDeck}
              onBack={() => setOpenDeck(null)}
            />
          ) : (
            <DeckListScreen state={phase.state} decks={phase.decks}
                            onOpen={setOpenDeck} />
          )
        ) : null}

        {phase.kind === 'ready' && !showConnection && tab === 'pc' ? (
          <PcDecksScreen
            state={phase.state}
            decks={phase.decks}
            onOpenLocal={(deckId) => {
              // A deck copied down or built is a deck on THIS phone, so it
              // opens where this phone's decks live rather than leaving you
              // to go and find it.
              setOpenDeck(deckId);
              setTab('decks');
            }}
          />
        ) : null}

        {phase.kind === 'ready' && !showConnection && tab === 'overlaps' ? (
          <OverlapsScreen state={phase.state} />
        ) : null}

        {phase.kind === 'ready' && !showConnection && tab === 'wishlist' ? (
          <WishlistScreen state={phase.state} decks={phase.decks} />
        ) : null}

        {phase.kind === 'ready' && !showConnection && tab === 'scan' ? (
          <ScanScreen state={phase.state} />
        ) : null}
      </View>
      </ErrorBoundary>

      {phase.kind === 'ready' ? (
        <View style={[styles.tabs, { paddingBottom: frame.paddingBottom + 6 }]}>
          {TABS.map((entry) => (
            <Pressable
              key={entry.id}
              style={styles.tab}
              onPress={() => {
                setTab(entry.id);
                if (entry.id !== 'decks') setOpenDeck(null);
                if (entry.id !== 'collection') setOpenCard(null);
              }}
            >
              <Text style={[styles.tabText, tab === entry.id && styles.tabOn]}>
                {entry.label}
              </Text>
            </Pressable>
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: '#0f1117' },
  body: { flex: 1 },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  muted: { color: '#8a8f9c' },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: '#1a1d27',
    borderBottomWidth: 1,
    borderBottomColor: '#2d3142',
    paddingHorizontal: 14,
    paddingVertical: 9,
  },
  banner_good: { backgroundColor: '#16241c' },
  banner_warn: { backgroundColor: '#2a2517' },
  banner_bad: { backgroundColor: '#2a1919' },
  banner_idle: { backgroundColor: '#1a1d27' },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dot_good: { backgroundColor: '#38a169' },
  dot_warn: { backgroundColor: '#ecc94b' },
  dot_bad: { backgroundColor: '#e53e3e' },
  dot_idle: { backgroundColor: '#8a8f9c' },
  bannerText: { flex: 1, fontSize: 13 },
  text_good: { color: '#68d391' },
  text_warn: { color: '#ecc94b' },
  text_bad: { color: '#fc8181' },
  text_idle: { color: '#8a8f9c' },
  bannerHint: { color: '#8a8f9c', fontSize: 11 },
  tabs: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderTopColor: '#2d3142',
    backgroundColor: '#1a1d27',
  },
  tab: { flex: 1, paddingTop: 14, paddingBottom: 8, alignItems: 'center' },
  tabText: { color: '#8a8f9c', fontSize: 13 },
  tabOn: { color: '#e53e3e', fontWeight: '700' },
});
