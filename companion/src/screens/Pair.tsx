/**
 * Pairing with the desktop.
 *
 * Scan the QR code the desktop shows, or type the address by hand when a
 * camera is not cooperating. The token lives in the URL because that is the
 * only form that survives being saved and reopened later.
 */

import React, { useRef, useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { parsePairingUrl } from '../lib/client.ts';
import type { Pairing } from '../lib/client.ts';
import { recordCrash } from '../lib/crash.ts';
import { checkHost } from '../lib/hosts.ts';
import { VERSION } from '../lib/version.ts';
import { CameraGate, CameraView } from './Camera.tsx';

interface Props {
  /** May be async: whatever it throws is shown rather than discarded. */
  onPaired: (pairing: Pairing) => void | Promise<void>;
  /**
   * Carry on with no PC at all.
   *
   * Not a "skip" and not "later". The phone IS the collection — scanning,
   * filing, grouping, decks and the wishlist are all its own — and a
   * desktop adds analysis on top. An app that could not be opened without
   * one would be calling its main job an accessory.
   */
  onStandalone?: () => void | Promise<void>;
  /** Rendered when the desktop has revoked this phone. */
  reason?: string;
}

export function PairScreen({ onPaired, onStandalone, reason }: Props) {
  const [typed, setTyped] = useState('');
  const [problem, setProblem] = useState('');
  const [scanning, setScanning] = useState(false);
  const [progress, setProgress] = useState('');

  const accept = async (raw: string) => {
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
    // A QR code is a thing anyone can hold in front of a camera. This token
    // grants read and write access to a whole collection, so it goes nowhere
    // that is not the tailnet, the local network, or this machine.
    const verdict = checkHost(pairing.baseUrl);
    if (!verdict.allowed) {
      setProblem(verdict.reason ?? 'That address is not one this app will use.');
      return;
    }

    setProblem('');
    setProgress(`Connecting to ${pairing.baseUrl}…`);
    try {
      // Nothing awaited this before. A release build reports an unhandled
      // rejection nowhere, so a failure here read as the button doing
      // absolutely nothing — which is exactly how it looked.
      await onPaired(pairing);
    } catch (err) {
      setProgress('');
      setProblem(recordCrash(err, 'pairing', false).message);
    }
  };

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>Connect to your PC</Text>
      {reason ? <Text style={styles.warn}>{reason}</Text> : null}

      <Text style={styles.body}>
        Open Densa Deck on your computer, go to Settings, and turn on
        “Scan from your phone”. Then scan the QR code it shows.
      </Text>

      <Pressable
        style={styles.primary}
        onPress={() => {
          setProblem('');
          setProgress('');
          setScanning(true);
        }}
      >
        <Text style={styles.primaryText}>
          {scanning ? 'Point at the QR code…' : 'Scan QR code'}
        </Text>
      </Pressable>

      {scanning ? (
        <QrScanner
          onFound={(value) => {
            setScanning(false);
            // Saying a code was read, before anything is done with it,
            // separates "the camera never saw it" from "what happened next
            // went wrong". Without that line both look the same.
            setProgress('Read a code — connecting…');
            void accept(value);
          }}
          onCancel={() => setScanning(false)}
        />
      ) : null}

      {progress ? <Text style={styles.progress}>{progress}</Text> : null}
      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

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
      <Pressable
        style={styles.secondary}
        onPress={() => {
          void accept(typed);
        }}
      >
        <Text style={styles.secondaryText}>Connect</Text>
      </Pressable>

      <Text style={styles.note}>
        Your phone needs to be on the same Tailscale network as your PC. Nothing
        is sent anywhere else — this talks to your computer directly.
      </Text>

      {/*
        The other way in. Scanning, filing, grouping, decks and the
        wishlist are the phone's own work; a PC adds analysis on top of
        them. Refusing to open without one would be calling the app's main
        job an accessory — and it is a decision, not a "skip", so it says
        what you get rather than what you are putting off.
      */}
      {onStandalone ? (
        <>
          <Pressable
            style={styles.alone}
            onPress={() => {
              void onStandalone();
            }}
          >
            <Text style={styles.aloneText}>Use without a PC</Text>
          </Pressable>
          <Text style={styles.note}>
            Your collection, decks and groups all live on this phone. A PC
            adds deck analysis and suggestions later — you can connect one
            whenever you like.
          </Text>
        </>
      ) : null}

      <Text style={styles.version}>Densa Deck companion {VERSION}</Text>
    </View>
  );
}

/**
 * The camera half.
 *
 * `CameraView` comes from `./Camera.tsx`, which owns the import and the
 * permission grant. It used to be fetched lazily into `useState`, and that is
 * what closed the app when this button was pressed. See `./Camera.tsx`.
 */
function QrScanner({
  onFound,
  onCancel,
}: {
  onFound: (value: string) => void;
  onCancel: () => void;
}) {
  // The camera reports a barcode on every frame it can see one, which is
  // several times a second. Without a latch the first good frame is followed
  // by a dozen more, all re-entering pairing while it is already unmounting.
  const done = useRef(false);

  return (
    <View style={styles.scanner}>
      <CameraGate purpose="Densa Deck reads the QR code your PC is showing. Nothing is recorded and no picture is kept.">
        <CameraView
          style={StyleSheet.absoluteFill}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
          onBarcodeScanned={({ data }: { data: string }) => {
            if (done.current || !data) return;
            done.current = true;
            onFound(data);
          }}
        />
      </CameraGate>
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
  progress: { color: '#ecc94b', lineHeight: 20 },
  note: { color: '#8a8f9c', fontSize: 12, lineHeight: 18, marginTop: 'auto' },
  alone: {
    alignItems: 'center',
    borderColor: '#2b3040',
    borderRadius: 10,
    borderWidth: 1,
    marginTop: 18,
    paddingVertical: 12,
  },
  aloneText: { color: '#e4e6eb', fontSize: 15, fontWeight: '600' },
  version: { color: '#4a4f5c', fontSize: 11 },
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
