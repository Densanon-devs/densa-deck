/**
 * The companion, assembled.
 *
 * Everything here is wiring: open the database, find out whether this phone is
 * paired, and show a screen. All the decisions live in `src/lib`, which is
 * plain TypeScript and covered by the Node suite — a screen that made a
 * decision would be a decision nothing could test.
 */

import React, { useEffect, useState } from 'react';
import { SafeAreaView, StatusBar, StyleSheet, Text, View } from 'react-native';

import { AppState, buildAppState } from './src/lib/app-state.ts';
import type { Pairing } from './src/lib/client.ts';
import { deviceId, loadPairing, savePairing } from './src/lib/pairing.ts';
import { openDeviceDatabase } from './src/lib/sqlite.ts';
import { LocalStore } from './src/lib/store.ts';
import { CollectionScreen } from './src/screens/Collection.tsx';
import { PairScreen } from './src/screens/Pair.tsx';

type Phase =
  | { kind: 'starting' }
  | { kind: 'pairing'; reason?: string }
  | { kind: 'ready'; state: AppState };

export default function App() {
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' });
  const [store, setStore] = useState<LocalStore | null>(null);
  const [banner, setBanner] = useState('');

  // Open the database once, whatever happens next. The phone's own copy is
  // the thing it reads from, so it has to exist before any screen renders.
  useEffect(() => {
    let live = true;
    void (async () => {
      const local = new LocalStore(await openDeviceDatabase());
      await local.init();
      if (!live) return;
      setStore(local);

      const pairing = await loadPairing(local);
      if (!pairing) {
        setPhase({ kind: 'pairing' });
        return;
      }
      setPhase({ kind: 'ready', state: await connect(local, pairing) });
    })();
    return () => {
      live = false;
    };
  }, []);

  async function connect(local: LocalStore, pairing: Pairing) {
    const device = await deviceId(local, () => globalThis.crypto.randomUUID());
    const state = buildAppState(local, pairing, device, () =>
      globalThis.crypto.randomUUID(),
    );
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
    // first paint of a screen whose data is already local.
    void state.sync();
    return state;
  }

  return (
    <SafeAreaView style={styles.app}>
      <StatusBar barStyle="light-content" />
      {banner ? (
        <View style={styles.banner}>
          <Text style={styles.bannerText}>{banner}</Text>
        </View>
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
            if (!store) return;
            await savePairing(store, pairing);
            setPhase({ kind: 'ready', state: await connect(store, pairing) });
          }}
        />
      ) : null}

      {phase.kind === 'ready' ? <CollectionScreen state={phase.state} /> : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: '#0f1117' },
  centre: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  muted: { color: '#8a8f9c' },
  banner: { backgroundColor: '#232837', padding: 10 },
  bannerText: { color: '#ecc94b', textAlign: 'center', fontSize: 13 },
});
