/**
 * Scanning cards into a collection.
 *
 * The camera work that matters was learned the hard way on the web version and
 * is preserved here:
 *
 *   * a phone's MAIN camera usually cannot focus close enough to fill the
 *     frame with a card. The telephoto can, because the same card size puts
 *     you further away. Which lens is which is not discoverable, so the choice
 *     is the user's — and it is remembered.
 *   * the card does not need to fill the frame. A small sharp card beats a
 *     large blurry one every time.
 *   * a filed card must be impossible to miss, or the same card goes in six
 *     times without anyone noticing.
 */

import React, { useCallback, useRef, useState } from 'react';
import {
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import {
  RepeatGuard,
  defaultFinish,
  identifyPhoto,
} from '../lib/scanner.ts';
import type { ScanCandidate, ScanResult } from '../lib/scanner.ts';
import { recordCrash } from '../lib/crash.ts';
import { CameraGate, CameraView } from './Camera.tsx';

interface Props {
  state: AppState;
  collectionUid: string;
  collectionName: string;
}

export function ScanScreen({ state, collectionUid, collectionName }: Props) {
  const [status, setStatus] = useState('Point at a card');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [flash, setFlash] = useState<{ name: string; copy: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const guard = useRef(new RepeatGuard());

  const file = useCallback(
    async (candidate: ScanCandidate, finish: string, copy = 1) => {
      await state.addCard({
        printing_id: candidate.printing_id,
        card_name: candidate.name,
        finish,
        collection_uid: collectionUid,
      });
      setFlash({ name: candidate.name, copy });
      setResult(null);
      setTimeout(() => setFlash(null), 950);
    },
    [state, collectionUid],
  );

  const handlePhoto = useCallback(
    async (base64: string) => {
      setBusy(true);
      setStatus('Reading…');
      try {
        const reply = await identifyPhoto(state.scanClient, base64);
        const top = reply.candidates?.[0];

        if (reply.auto_addable && top) {
          const decision = guard.current.consider(top.name, Date.now());
          if (decision.file) {
            await file(top, defaultFinish(top, reply), decision.copy);
            setStatus('Added — next card');
          } else {
            setStatus('Same card still in frame');
          }
          return;
        }

        // Anything less than certain waits for a tap. A wrong card filed
        // silently is worse than no card, because you will not know to look
        // for it.
        setResult(reply);
        setStatus(
          reply.candidates?.length
            ? 'Which printing is this?'
            : 'Could not read that one',
        );
      } catch (err) {
        setStatus((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [state, file],
  );

  return (
    <View style={styles.screen}>
      {flash ? (
        <View style={[styles.flash, flash.copy > 1 && styles.flashDupe]}>
          <Text style={styles.flashTick}>✓</Text>
          <Text style={styles.flashName}>{flash.name}</Text>
          {flash.copy > 1 ? (
            <Text style={styles.flashMeta}>copy #{flash.copy} of this card</Text>
          ) : null}
        </View>
      ) : null}

      <Text style={styles.target}>Scanning into {collectionName}</Text>
      <CameraPane onPhoto={handlePhoto} busy={busy} />
      <Text style={styles.status}>{status}</Text>
      <Text style={styles.hint}>
        If the picture looks soft, switch lens — the main camera often can’t
        focus this close. The card doesn’t need to fill the frame.
      </Text>

      {result?.candidates?.length ? (
        <View style={styles.picker}>
          {result.candidates.slice(0, 20).map((candidate, index) => (
            <Pressable
              key={`${candidate.printing_id}-${index}`}
              style={styles.candidate}
              onPress={() => file(candidate, defaultFinish(candidate, result))}
            >
              <Text style={styles.candidateName}>{candidate.name}</Text>
              <Text style={styles.candidateMeta}>
                {candidate.set_code.toUpperCase()} #{candidate.collector_number}
              </Text>
            </Pressable>
          ))}
          <Pressable style={styles.none} onPress={() => setResult(null)}>
            <Text style={styles.candidateMeta}>None of these</Text>
          </Pressable>
        </View>
      ) : null}
    </View>
  );
}

/**
 * The camera and the shutter.
 *
 * `CameraView` and the permission grant both live in `./Camera.tsx`. This used
 * to load the module into `useState`, which is the same fault that closed the
 * app from the pairing screen.
 */
function CameraPane({
  onPhoto,
  busy,
}: {
  onPhoto: (base64: string) => void;
  busy: boolean;
}) {
  const ref = useRef<CameraView | null>(null);
  const [problem, setProblem] = useState('');

  const capture = useCallback(async () => {
    setProblem('');
    try {
      const shot = await ref.current?.takePictureAsync({
        base64: true,
        quality: 0.9,
        skipProcessing: false,
      });
      if (shot?.base64) onPhoto(shot.base64);
      else setProblem('The camera returned an empty picture. Try again.');
    } catch (err) {
      // Rejecting here used to go nowhere: an unhandled rejection, a shutter
      // that appeared to do nothing, and no way to tell what happened.
      setProblem(recordCrash(err, 'capture', false).message);
    }
  }, [onPhoto]);

  return (
    <View style={styles.cameraBox}>
      <CameraGate purpose="Scanning a card means taking a picture of it. Pictures are read and discarded — none are kept.">
        <CameraView
          ref={ref}
          style={StyleSheet.absoluteFill}
          facing="back"
        />
        <Pressable
          style={styles.shutter}
          disabled={busy}
          onPress={() => {
            void capture();
          }}
        >
          <Text style={styles.shutterText}>{busy ? '…' : 'Capture'}</Text>
        </Pressable>
      </CameraGate>
      {problem ? <Text style={styles.problem}>{problem}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117', padding: 14, gap: 10 },
  target: { color: '#8a8f9c', fontSize: 13 },
  cameraBox: {
    height: 380,
    borderRadius: 12,
    overflow: 'hidden',
    backgroundColor: '#000',
  },
  shutter: {
    position: 'absolute',
    bottom: 14,
    alignSelf: 'center',
    backgroundColor: '#e53e3ecc',
    borderRadius: 999,
    paddingHorizontal: 28,
    paddingVertical: 12,
  },
  shutterText: { color: '#fff', fontWeight: '700' },
  status: { color: '#e4e6eb' },
  hint: { color: '#8a8f9c', fontSize: 12, lineHeight: 18 },
  picker: { gap: 6 },
  candidate: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  candidateName: { color: '#e4e6eb', fontSize: 15 },
  candidateMeta: { color: '#8a8f9c', fontSize: 12 },
  none: { padding: 12, alignItems: 'center' },
  flash: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    zIndex: 50,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(56,161,105,0.93)',
    gap: 8,
  },
  flashDupe: { backgroundColor: 'rgba(214,158,46,0.95)' },
  flashTick: { fontSize: 48, color: '#fff' },
  flashName: { fontSize: 24, color: '#fff', fontWeight: '700' },
  flashMeta: { fontSize: 15, color: '#fff' },
  problem: {
    position: 'absolute',
    bottom: 0, left: 0, right: 0,
    backgroundColor: '#0f1117dd',
    color: '#e53e3e',
    padding: 8,
    fontSize: 12,
  },
});
