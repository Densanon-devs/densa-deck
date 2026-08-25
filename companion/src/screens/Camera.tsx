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
 */

import React, { useEffect, useRef, useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import { CameraView, useCameraPermissions } from 'expo-camera';

import { recordCrash } from '../lib/crash.ts';

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
  const [permission, request] = useCameraPermissions();
  const [asking, setAsking] = useState(false);
  const asked = useRef(false);

  useEffect(() => {
    if (!permission || permission.granted || asked.current) return;
    if (!permission.canAskAgain) return;
    asked.current = true;
    setAsking(true);
    void request()
      .catch((err) => recordCrash(err, 'camera permission', false))
      .finally(() => setAsking(false));
  }, [permission, request]);

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
