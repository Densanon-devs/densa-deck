/**
 * The one place that touches expo-camera.
 *
 * Two things were wrong here and both were invisible to every test in the
 * repo, because both are facts about how React and Android behave rather than
 * decisions the code makes.
 *
 * **The component was being put into state.** `useState`'s setter treats a
 * function argument as an updater and *calls it*. `CameraView` is an ES class,
 * so storing it invoked a constructor without `new`, which throws. React
 * swallows that throw on its eager pass — deliberately, because it expects to
 * hit it again while rendering — and hits it again while rendering, where
 * nothing was catching. An uncaught render error with no boundary above it
 * ends the process, so the app closed the moment the button was pressed.
 *
 * Storing a component in state is a trap with no upside, so this module
 * imports it once, statically, and there is nothing left to get wrong.
 *
 * **Nobody ever asked for the camera.** Android has required a runtime grant
 * since 6.0; the manifest entry only makes the request possible. Mounting a
 * camera without one gets you a black rectangle at best, and CameraX opening
 * the device on its own executor thread at worst — an exception on a thread
 * with no handler takes the process down, which is what a crash on a phone
 * with no debugger attached looks like from the outside.
 *
 * **And nobody looked again.** The grant is made in Android's settings, in
 * another app, and `useCameraPermissions` has no idea that happened: it
 * reads once on mount and again only when something calls it. So the screen
 * that sent you to settings still said "camera access needed" when you came
 * back, and the only way out was to leave for another tab, pull to refresh,
 * and return — which works, and which nobody would ever guess. Coming back
 * to the foreground now re-reads it.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  AppState as ForegroundState,
  Linking,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { CameraView, useCameraPermissions } from 'expo-camera';

import { recordCrash } from '../lib/crash.ts';
import { shouldRecheck } from '../lib/permission.ts';
import type { Phase } from '../lib/permission.ts';

export { CameraView };

interface Props {
  /** Why the camera is wanted, shown if the user has to be asked twice. */
  purpose: string;
  children: React.ReactNode;
}

/**
 * Renders `children` only once the camera is actually usable.
 *
 * Asks once on mount — the user reached this screen by tapping something that
 * says "scan", so the intent is not in doubt — and explains itself only if
 * that first ask is refused.
 */
export function CameraGate({ purpose, children }: Props) {
  // The third element re-reads the grant WITHOUT prompting, which is the
  // one that matters here: after a trip to settings there is nothing to ask
  // for, only something to find out.
  const [permission, request, recheck] = useCameraPermissions();
  const [asking, setAsking] = useState(false);
  const asked = useRef(false);
  const phase = useRef<Phase>('active');

  useEffect(() => {
    if (!permission || permission.granted || asked.current) return;
    if (!permission.canAskAgain) return;
    asked.current = true;
    setAsking(true);
    void request()
      .catch((err) => recordCrash(err, 'camera permission', false))
      .finally(() => setAsking(false));
  }, [permission, request]);

  // Back from settings. Android grants live in another app, so the only
  // signal this side is the app returning to the foreground.
  useEffect(() => {
    const granted = !!permission?.granted;
    const subscription = ForegroundState.addEventListener('change', (next) => {
      const previous = phase.current;
      phase.current = next as Phase;
      if (shouldRecheck(previous, next as Phase, granted)) {
        void recheck().catch((err) =>
          recordCrash(err, 'rechecking camera permission', false));
      }
    });
    return () => subscription.remove();
  }, [permission?.granted, recheck]);

  if (!permission || asking) {
    return <Text style={styles.muted}>Checking camera access…</Text>;
  }

  if (permission.granted) return <>{children}</>;

  // Denied. Which of the two kinds of denial matters: one is a button away,
  // the other can only be undone in Android's settings, and telling the user
  // to "allow" something the app can no longer ask for is a dead end.
  return (
    <View style={styles.gate}>
      <Text style={styles.title}>Camera access needed</Text>
      <Text style={styles.muted}>{purpose}</Text>
      {permission.canAskAgain ? (
        <Pressable
          style={styles.button}
          onPress={() => {
            void request().catch((err) =>
              recordCrash(err, 'camera permission', false),
            );
          }}
        >
          <Text style={styles.buttonText}>Allow camera</Text>
        </Pressable>
      ) : (
        <>
          <Text style={styles.muted}>
            You’ve turned this down before, so Android won’t let the app ask
            again. It has to be switched on in settings.
          </Text>
          <Pressable
            style={styles.button}
            onPress={() => {
              void Linking.openSettings().catch((err) =>
                recordCrash(err, 'open settings', false),
              );
            }}
          >
            <Text style={styles.buttonText}>Open app settings</Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  gate: { gap: 10, paddingVertical: 8 },
  title: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  muted: { color: '#8a8f9c', lineHeight: 20 },
  button: {
    backgroundColor: '#e53e3e',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  buttonText: { color: '#fff', fontWeight: '600', fontSize: 15 },
});
