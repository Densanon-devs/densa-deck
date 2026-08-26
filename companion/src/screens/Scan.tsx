/**
 * Scanning cards into a collection.
 *
 * The camera work that matters was learned the hard way on the web version and
 * is preserved here:
 *
 *   * a phone's MAIN camera usually cannot focus close enough to fill the
 *     frame with a card. The telephoto can, because the same card size puts
 *     you further away. On Android there is no way to ask for the telephoto by
 *     name — expo-camera's `selectedLens` is marked iOS-only — but zooming in
 *     gets there, because CameraX switches lenses itself once the zoom passes
 *     the point where the longer one is better. So zoom IS the lens control,
 *     and it is remembered between visits.
 *   * the card does not need to fill the frame. A small sharp card beats a
 *     large blurry one every time.
 *   * a filed card must be impossible to miss, or the same card goes in six
 *     times without anyone noticing.
 *
 * The controls stay behind a button. An earlier version left every option on
 * screen at once and there was no room left for the camera.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AppState, Connection } from '../lib/app-state.ts';
import { AutoScanner, explain } from '../lib/autoscan.ts';
import {
  DEFAULT_CAMERA_SETTINGS,
  stepZoom,
  zoomLabel,
} from '../lib/camera-settings.ts';
import type { CameraSettings } from '../lib/camera-settings.ts';
import { recordCrash } from '../lib/crash.ts';
import {
  RepeatGuard,
  defaultFinish,
  identifyPhoto,
} from '../lib/scanner.ts';
import type { ScanCandidate, ScanResult } from '../lib/scanner.ts';
import { CameraGate, CameraView } from './Camera.tsx';

interface Props {
  state: AppState;
  collectionUid: string;
  collectionName: string;
}

/** How often the loop wakes to ask whether it is time for another picture. */
const TICK_MS = 250;

export function ScanScreen({ state, collectionUid, collectionName }: Props) {
  const [status, setStatus] = useState('Point at a card');
  const [result, setResult] = useState<ScanResult | null>(null);
  const [flash, setFlash] = useState<{ name: string; copy: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<CameraSettings>(
    DEFAULT_CAMERA_SETTINGS,
  );
  const [connection, setConnection] = useState<Connection>('unknown');

  const guard = useRef(new RepeatGuard());
  const scanner = useRef(new AutoScanner());
  const camera = useRef<CameraView | null>(null);
  // The interval's closure would otherwise read whatever `busy` was when the
  // effect ran, and fire a second capture on top of the one in flight.
  const busyRef = useRef(false);

  useEffect(() => {
    void state
      .cameraSettings()
      .then(setSettings)
      .catch((err) => recordCrash(err, 'camera settings', false));
    return state.subscribe((snapshot) => setConnection(snapshot.connection));
  }, [state]);

  const change = useCallback(
    (patch: Partial<CameraSettings>) => {
      setSettings((current) => {
        const next = { ...current, ...patch };
        void state
          .rememberCameraSettings(next)
          .catch((err) => recordCrash(err, 'saving camera settings', false));
        return next;
      });
    },
    [state],
  );

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
      busyRef.current = true;
      setBusy(true);
      setStatus('Reading...');
      try {
        const reply = await identifyPhoto(state.scanClient, base64);
        scanner.current.succeeded();
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
        scanner.current.failed();
        setStatus(recordCrash(err, 'reading the card', false).message);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [state, file],
  );

  const capture = useCallback(async () => {
    const shot = await camera.current?.takePictureAsync({
      base64: true,
      quality: 0.9,
      skipProcessing: false,
    });
    if (!shot?.base64) {
      setStatus('The camera returned an empty picture.');
      return;
    }
    scanner.current.captured(shot.base64);
    await handlePhoto(shot.base64);
  }, [handlePhoto]);

  // The auto loop. Every decision it makes lives in AutoScanner, which is
  // tested in Node; this only carries them out.
  useEffect(() => {
    if (!auto) return;
    scanner.current.reset(Date.now());
    const timer = setInterval(() => {
      const decision = scanner.current.next({
        running: true,
        busy: busyRef.current,
        connection,
        now: Date.now(),
      });
      if (decision.act === 'stop') {
        setAuto(false);
        if (decision.reason !== 'stopped') setStatus(explain(decision.reason));
        return;
      }
      if (decision.act === 'capture') {
        void capture().catch((err) => {
          scanner.current.failed();
          setStatus(recordCrash(err, 'auto scan', false).message);
        });
      }
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [auto, connection, capture]);

  const offline = connection === 'offline' || connection === 'unpaired';

  return (
    <View style={styles.screen}>
      {flash ? (
        <View style={[styles.flash, flash.copy > 1 && styles.flashDupe]}>
          <Text style={styles.flashTick}>ADDED</Text>
          <Text style={styles.flashName}>{flash.name}</Text>
          {flash.copy > 1 ? (
            <Text style={styles.flashMeta}>copy #{flash.copy} of this card</Text>
          ) : null}
        </View>
      ) : null}

      <View style={styles.header}>
        <Text style={styles.target}>Scanning into {collectionName}</Text>
        <Pressable
          style={[styles.chip, auto && styles.chipOn]}
          onPress={() => {
            if (!auto && offline) {
              setStatus(explain('offline'));
              return;
            }
            setStatus(
              auto ? 'Point at a card' : 'Auto scan on — show it a card',
            );
            setAuto((on) => !on);
          }}
        >
          <Text style={[styles.chipText, auto && styles.chipTextOn]}>
            {auto ? 'Auto on' : 'Auto scan'}
          </Text>
        </Pressable>
      </View>

      <View style={styles.cameraBox}>
        <CameraGate purpose="Scanning a card means taking a picture of it. Pictures are read and discarded — none are kept.">
          <CameraView
            ref={camera}
            style={StyleSheet.absoluteFill}
            facing="back"
            zoom={settings.zoom}
            enableTorch={settings.torch}
            autofocus={settings.autofocus}
            animateShutter={false}
          />
          <Pressable
            style={styles.gear}
            onPress={() => setShowSettings((open) => !open)}
          >
            <Text style={styles.gearText}>
              {showSettings ? 'Hide camera' : 'Camera'}
            </Text>
          </Pressable>
          <Pressable
            style={styles.shutter}
            disabled={busy}
            onPress={() => {
              void capture().catch((err) =>
                setStatus(recordCrash(err, 'capture', false).message),
              );
            }}
          >
            <Text style={styles.shutterText}>{busy ? '...' : 'Capture'}</Text>
          </Pressable>
        </CameraGate>
      </View>

      <Text style={styles.status}>{status}</Text>

      {showSettings ? (
        <View style={styles.panel}>
          <View style={styles.settingRow}>
            <Text style={styles.settingName}>Zoom</Text>
            <Pressable
              style={styles.step}
              onPress={() => change({ zoom: stepZoom(settings.zoom, -1) })}
            >
              <Text style={styles.stepText}>-</Text>
            </Pressable>
            <View style={styles.track}>
              <View
                style={[styles.fill, { width: `${settings.zoom * 100}%` }]}
              />
            </View>
            <Pressable
              style={styles.step}
              onPress={() => change({ zoom: stepZoom(settings.zoom, 1) })}
            >
              <Text style={styles.stepText}>+</Text>
            </Pressable>
            <Text style={styles.settingValue}>{zoomLabel(settings.zoom)}</Text>
          </View>

          <View style={styles.settingRow}>
            <Text style={styles.settingName}>Light</Text>
            <Pressable
              style={[styles.toggle, settings.torch && styles.toggleOn]}
              onPress={() => change({ torch: !settings.torch })}
            >
              <Text style={styles.toggleText}>
                {settings.torch ? 'Torch on' : 'Torch off'}
              </Text>
            </Pressable>
          </View>

          <View style={styles.settingRow}>
            <Text style={styles.settingName}>Focus</Text>
            <Pressable
              style={[
                styles.toggle,
                settings.autofocus === 'off' && styles.toggleOn,
              ]}
              onPress={() =>
                change({
                  autofocus: settings.autofocus === 'on' ? 'off' : 'on',
                })
              }
            >
              <Text style={styles.toggleText}>
                {settings.autofocus === 'on' ? 'Auto' : 'Locked'}
              </Text>
            </Pressable>
          </View>

          <Text style={styles.hint}>
            Zoom is the lens control. Android gives no way to ask for the
            telephoto by name, but zooming in makes the phone switch to it —
            and that is the lens that can focus on a card held close. The card
            does not need to fill the frame: a small sharp one beats a large
            blurry one. Lock the focus once it looks right and it will stop
            hunting between cards.
          </Text>
        </View>
      ) : null}

      {result?.candidates?.length ? (
        <ScrollView style={styles.picker}>
          {result.candidates.slice(0, 20).map((candidate, index) => (
            <Pressable
              key={`${candidate.printing_id}-${index}`}
              style={styles.candidate}
              onPress={() => {
                void file(candidate, defaultFinish(candidate, result)).catch(
                  (err) => setStatus(recordCrash(err, 'filing', false).message),
                );
              }}
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
        </ScrollView>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117', padding: 14, gap: 10 },
  header: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  target: { color: '#8a8f9c', fontSize: 13, flex: 1 },
  chip: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 7,
  },
  chipOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  chipText: { color: '#8a8f9c', fontSize: 13, fontWeight: '600' },
  chipTextOn: { color: '#fff' },
  cameraBox: {
    height: 360,
    borderRadius: 12,
    overflow: 'hidden',
    backgroundColor: '#000',
  },
  gear: {
    position: 'absolute',
    top: 10,
    right: 10,
    backgroundColor: '#0f1117cc',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  gearText: { color: '#e4e6eb', fontSize: 12 },
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
  panel: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 12,
  },
  settingRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  settingName: { color: '#8a8f9c', fontSize: 13, width: 46 },
  settingValue: {
    color: '#e4e6eb',
    fontSize: 12,
    width: 42,
    textAlign: 'right',
  },
  step: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 8,
    width: 40,
    height: 36,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stepText: { color: '#e4e6eb', fontSize: 20, lineHeight: 24 },
  track: {
    flex: 1,
    height: 6,
    borderRadius: 3,
    backgroundColor: '#2d3142',
    overflow: 'hidden',
  },
  fill: { height: 6, backgroundColor: '#e53e3e' },
  toggle: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  toggleOn: { backgroundColor: '#2d3142' },
  toggleText: { color: '#e4e6eb', fontSize: 13 },
  hint: { color: '#8a8f9c', fontSize: 12, lineHeight: 18 },
  picker: { maxHeight: 260 },
  candidate: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    marginBottom: 6,
  },
  candidateName: { color: '#e4e6eb', fontSize: 15 },
  candidateMeta: { color: '#8a8f9c', fontSize: 12 },
  none: { padding: 12, alignItems: 'center' },
  flash: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 50,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(56,161,105,0.93)',
    gap: 8,
  },
  flashDupe: { backgroundColor: 'rgba(214,158,46,0.95)' },
  flashTick: { fontSize: 34, color: '#fff', fontWeight: '700', letterSpacing: 2 },
  flashName: { fontSize: 24, color: '#fff', fontWeight: '700' },
  flashMeta: { fontSize: 15, color: '#fff' },
});
