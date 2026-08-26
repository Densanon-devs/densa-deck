/**
 * Why it says Offline.
 *
 * That one word stood in for a dozen different problems, each with a different
 * fix: the wrong address, a firewall, Tailscale switched off on the phone, a
 * desktop that isn't serving, or — the one that actually happened — Android
 * refusing a plain-HTTP request before it ever left the handset, because a
 * release build blocks cleartext unless the manifest says otherwise.
 *
 * All of them looked identical from the outside. This shows what each address
 * did when asked, which is the difference between guessing and knowing.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import type { EndpointReport } from '../lib/client.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  onClose: () => void;
}

export function ConnectionScreen({ state, onClose }: Props) {
  const [reports, setReports] = useState<EndpointReport[] | null>(null);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);

  const run = useCallback(async () => {
    setBusy(true);
    setProblem('');
    try {
      setReports(await state.diagnose());
      // A probe that answers proves nothing until a real request follows it,
      // so the sync runs too and the banner updates from the result.
      await state.sync();
    } finally {
      setBusy(false);
    }
  }, [state]);

  useEffect(() => {
    void run().catch(reporting('checking the connection', setProblem));
  }, [run]);

  const anyOk = reports?.some((r) => r.ok) ?? false;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Connection</Text>
        <Pressable onPress={onClose}>
          <Text style={styles.close}>Done</Text>
        </Pressable>
      </View>

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {reports === null ? (
        <Text style={styles.muted}>Trying every address…</Text>
      ) : (
        reports.map((report) => (
          <View key={report.label} style={styles.row}>
            <View style={styles.rowHead}>
              <Text style={[styles.label, report.ok && styles.labelOk]}>
                {report.ok ? '✓' : '✕'} {report.label}
              </Text>
              <Text style={styles.url} selectable numberOfLines={1}>
                {report.url || '—'}
              </Text>
            </View>
            <Text style={styles.detail} selectable>
              {report.detail}
            </Text>
          </View>
        ))
      )}

      {reports && !anyOk ? (
        <View style={styles.advice}>
          <Text style={styles.adviceTitle}>Nothing answered</Text>
          <Text style={styles.muted}>
            Things worth checking, in the order they usually turn out to be
            wrong:
          </Text>
          <Text style={styles.muted}>
            1. Densa Deck is open on the PC and phone scanning is switched on
            in Settings.
          </Text>
          <Text style={styles.muted}>
            2. Tailscale is running on this phone, not just on the PC.
          </Text>
          <Text style={styles.muted}>
            3. The PC has moved to a different Wi-Fi address since you paired.
            The tunnel address is the one that survives that — if Tailscale
            answers and Wi-Fi does not, nothing is actually wrong.
          </Text>
          <Text style={styles.muted}>
            4. Windows Firewall is refusing the connection. It only ever asks
            once, and answering “Cancel” that one time is permanent.
          </Text>
        </View>
      ) : null}

      <Pressable
        style={styles.button}
        disabled={busy}
        onPress={() => {
          void run().catch(reporting('checking the connection', setProblem));
        }}
      >
        <Text style={styles.buttonText}>
          {busy ? 'Trying…' : 'Try again'}
        </Text>
      </Pressable>

      <Text style={styles.footnote}>
        Your collection is on this phone either way. Nothing here is lost while
        the PC is out of reach — edits wait and go across when it comes back.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 16, gap: 14 },
  header: { flexDirection: 'row', alignItems: 'center' },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700', flex: 1 },
  close: { color: '#e53e3e', fontSize: 16, fontWeight: '600' },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 20 },
  row: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 6,
  },
  rowHead: { gap: 2 },
  label: { color: '#e53e3e', fontSize: 15, fontWeight: '700' },
  labelOk: { color: '#38a169' },
  url: { color: '#8a8f9c', fontSize: 12 },
  detail: { color: '#e4e6eb', fontSize: 13, lineHeight: 19 },
  advice: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 8,
  },
  adviceTitle: { color: '#ecc94b', fontSize: 15, fontWeight: '700' },
  button: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  buttonText: { color: '#e4e6eb', fontSize: 16 },
  problem: { color: '#e53e3e', fontSize: 13, lineHeight: 19 },
  footnote: { color: '#8a8f9c', fontSize: 12, lineHeight: 18 },
});
