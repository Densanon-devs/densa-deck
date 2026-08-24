/**
 * Pairing with the desktop.
 *
 * Scan the QR code the desktop shows, or type the address by hand when a
 * camera is not cooperating. The token lives in the URL because that is the
 * only form that survives being saved and reopened later.
 */

import React, { useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { parsePairingUrl } from '../lib/client.ts';
import type { Pairing } from '../lib/client.ts';

interface Props {
  onPaired: (pairing: Pairing) => void;
  /** Rendered when the desktop has revoked this phone. */
  reason?: string;
}

export function PairScreen({ onPaired, reason }: Props) {
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState('');
  const [scanning, setScanning] = useState(false);

  const accept = (raw: string) => {
    const pairing = parsePairingUrl(raw);
    if (!pairing) {
      // Being specific matters: the usual mistake is a link whose token was
      // stripped, which looks like a perfectly good URL.
      setProblem(
        'That link has no pairing code in it. Use the QR code from ' +
          'Settings on the desktop, address and all.',
      );
      return;
    }
    setProblem('');
    onPaired(pairing);
  };

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>Connect to your PC</Text>
      {reason ? <Text style={styles.warn}>{reason}</Text> : null}

      <Text style={styles.body}>
        Open Densa Deck on your computer, go to Settings, and turn on
        “Scan from your phone”. Then scan the QR code it shows.
      </Text>

      <Pressable style={styles.primary} onPress={() => setScanning(true)}>
        <Text style={styles.primaryText}>
          {scanning ? 'Point at the QR code…' : 'Scan QR code'}
        </Text>
      </Pressable>

      {scanning ? (
        <QrScanner
          onFound={(value) => {
            setScanning(false);
            accept(value);
          }}
          onCancel={() => setScanning(false)}
        />
      ) : null}

      <Text style={styles.or}>or paste the link</Text>
      <TextInput
        style={styles.input}
        placeholder="https://100.x.y.z:8791/scan?t=…"
        placeholderTextColor="#8a8f9c"
        value={typed}
        onChangeText={setTyped}
        autoCapitalize="none"
        autoCorrect={false}
      />
      <Pressable style={styles.secondary} onPress={() => accept(typed)}>
        <Text style={styles.secondaryText}>Connect</Text>
      </Pressable>

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      <Text style={styles.note}>
        Your phone needs to be on the same Tailscale network as your PC. Nothing
        is sent anywhere else — this talks to your computer directly.
      </Text>
    </View>
  );
}

/**
 * The camera half, imported lazily.
 *
 * expo-camera pulls in native code that does not exist under Node, and this
 * file is imported by tests that only care about the parsing above.
 */
function QrScanner({
  onFound,
  onCancel,
}: {
  onFound: (value: string) => void;
  onCancel: () => void;
}) {
  const [Camera, setCamera] = useState<React.ComponentType<
    Record<string, unknown>
  > | null>(null);

  React.useEffect(() => {
    let live = true;
    void import('expo-camera').then((mod) => {
      if (live) setCamera(mod.CameraView as never);
    });
    return () => {
      live = false;
    };
  }, []);

  if (!Camera) return <Text style={styles.body}>Starting the camera…</Text>;

  return (
    <View style={styles.scanner}>
      <Camera
        style={StyleSheet.absoluteFill}
        barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
        onBarcodeScanned={({ data }: { data: string }) => onFound(data)}
      />
      <Pressable style={styles.cancel} onPress={onCancel}>
        <Text style={styles.secondaryText}>Cancel</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117', padding: 20, gap: 12 },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  body: { color: '#8a8f9c', lineHeight: 22 },
  warn: {
    color: '#ecc94b',
    borderColor: '#ecc94b',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    lineHeight: 20,
  },
  primary: {
    backgroundColor: '#e53e3e',
    borderRadius: 10,
    padding: 16,
    alignItems: 'center',
  },
  primaryText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  or: { color: '#8a8f9c', textAlign: 'center' },
  input: {
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    padding: 14,
  },
  secondary: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  secondaryText: { color: '#e4e6eb', fontSize: 16 },
  problem: { color: '#e53e3e', lineHeight: 20 },
  note: { color: '#8a8f9c', fontSize: 12, lineHeight: 18, marginTop: 'auto' },
  scanner: { height: 320, borderRadius: 12, overflow: 'hidden' },
  cancel: {
    position: 'absolute',
    bottom: 12,
    alignSelf: 'center',
    backgroundColor: '#0f1117cc',
    borderRadius: 10,
    paddingHorizontal: 20,
    paddingVertical: 10,
  },
});
