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
import type { Crash } from './src/lib/crash.ts';
import { installGlobalErrorTrap, onCrash, recordCrash } from './src/lib/crash.ts';
import type { Pairing } from './src/lib/client.ts';
import { DeckStore } from './src/lib/decks.ts';
import { deviceId, loadPairing, savePairing } from './src/lib/pairing.ts';
import { openDeviceDatabase } from './src/lib/sqlite.ts';
import { uuid } from './src/lib/uuid.ts';
import { DEFAULT_COLLECTION_UID, LocalStore } from './src/lib/store.ts';
import { ErrorBoundary, CrashScreen } from './src/screens/Boundary.tsx';
import { CollectionScreen } from './src/screens/Collection.tsx';
import { DeckListScreen, DeckScreen } from './src/screens/Decks.tsx';
import { PairScreen } from './src/screens/Pair.tsx';
import { ScanScreen } from './src/screens/Scan.tsx';
import { WishlistScreen } from './src/screens/Wishlist.tsx';

// Before the first render, so a failure during startup is shown rather than
// taking the process down with it.
installGlobalErrorTrap();

type Tab = 'collection' | 'decks' | 'wishlist' | 'scan';

type Phase =
  | { kind: 'starting' }
  | { kind: 'pairing'; reason?: string }
  | { kind: 'ready'; state: AppState; decks: DeckStore };

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'collection', label: 'Cards' },
  { id: 'decks', label: 'Decks' },
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
  const [banner, setBanner] = useState('');
  const [fatal, setFatal] = useState<Crash | null>(null);

  useEffect(
    () => onCrash((crash) => crash.fatal && setFatal(crash)),
    [],
  );

  const connect = useCallback(async (local: LocalStore, pairing: Pairing) => {
    const device = await deviceId(local, uuid);
    const state = buildAppState(local, pairing, device, uuid);
    state.subscribe((snapshot) => {
      if (snapshot.connection === 'unpaired') {
        // The desktop revoked this phone; there is nothing to retry, so say
        // so and send the user back to pairing rather than looping.
        setPhase({ kind: 'pairing', reason: snapshot.lastError });
        return;
      }
      setBanner(
        snapshot.connection === 'offline'
          ? snapshot.pendingEdits > 0
            ? `Offline — ${snapshot.pendingEdits} change${
                snapshot.pendingEdits === 1 ? '' : 's'
              } waiting to sync`
            : 'Offline — your collection is still here'
          : '',
      );
    });
    // Deliberately not awaited: a slow or absent desktop must not hold up the
    // first paint of a screen whose data is already local. Not being awaited
    // is exactly why it needs its own catch — nothing else would ever see it.
    void state.sync().catch((err) => recordCrash(err, 'first sync', false));
    return state;
  }, []);

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
        setPhase({
          kind: 'ready',
          state: await connect(local, pairing),
          decks: new DeckStore(database),
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
      {banner ? (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>{banner}</Text>
        </View>
      ) : null}

      <ErrorBoundary where={`the ${tab} screen`}>
      <View style={styles.body}>
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
              setPhase({
                kind: 'ready',
                state: await connect(store, pairing),
                decks: new DeckStore(database),
              });
            }}
          />
        ) : null}

        {phase.kind === 'ready' && tab === 'collection' ? (
          <CollectionScreen state={phase.state} />
        ) : null}

        {phase.kind === 'ready' && tab === 'decks' ? (
          openDeck ? (
            <DeckScreen
              state={phase.state}
              decks={phase.decks}
              deckId={openDeck}
              onBack={() => setOpenDeck(null)}
            />
          ) : (
            <DeckListScreen decks={phase.decks} onOpen={setOpenDeck} />
          )
        ) : null}

        {phase.kind === 'ready' && tab === 'wishlist' ? (
          <WishlistScreen state={phase.state} decks={phase.decks} />
        ) : null}

        {phase.kind === 'ready' && tab === 'scan' ? (
          <ScanScreen
            state={phase.state}
            collectionUid={DEFAULT_COLLECTION_UID}
            collectionName="Main Collection"
          />
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
  banner: { backgroundColor: '#232837', padding: 10 },
  bannerText: { color: '#ecc94b', textAlign: 'center', fontSize: 13 },
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
