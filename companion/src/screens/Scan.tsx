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
 * The controls are on screen rather than behind a button. They were behind
 * one, and what came back was "not seeing zoom options" — a control nobody
 * finds is a control that does not exist. Two compact rows, not the wall of
 * options the web version had.
 *
 * Which collection is being scanned into is picked here too, and remembered.
 * A scanning session is one shelf at a time, and a target that reset whenever
 * the tab changed would quietly scatter half a box into the wrong place.
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
  ZOOM_DEADZONE,
  stepZoom,
  zoomAt,
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
import { DEFAULT_COLLECTION_UID } from '../lib/store.ts';
import type { CollectionRow } from '../lib/store.ts';
import { CameraGate, CameraView } from './Camera.tsx';
import { CollectionBar } from './CollectionBar.tsx';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
}

/** How often the loop wakes to ask whether it is time for another picture. */
const TICK_MS = 250;

export function ScanScreen({ state }: Props) {
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
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  // Starts at the default rather than empty: a card filed in the moment
  // between mounting and the stored target arriving would go nowhere
  // nameable.
  const [target, setTarget] = useState(DEFAULT_COLLECTION_UID);
  const [problem, setProblem] = useState('');
  // The green flash is gone in under a second. What was filed has to stay on
  // screen afterwards, because a wrong card is not always obvious in the
  // moment and the alternative is finding it weeks later in the collection.
  const [lastAdded, setLastAdded] = useState<{
    candidate: ScanCandidate;
    finish: string;
  } | null>(null);

  const guard = useRef(new RepeatGuard());
  const scanner = useRef(new AutoScanner());
  const camera = useRef<CameraView | null>(null);
  // The interval's closure would otherwise read whatever `busy` was when the
  // effect ran, and fire a second capture on top of the one in flight.
  const busyRef = useRef(false);
  // Measured rather than assumed: a tap only means a zoom level if the width
  // it landed on is the real one.
  const [trackWidth, setTrackWidth] = useState(0);

  const loadCollections = useCallback(async () => {
    setCollections(await state.collections());
  }, [state]);

  useEffect(() => {
    void state
      .cameraSettings()
      .then(setSettings)
      .catch((err) => recordCrash(err, 'camera settings', false));
    void state.scanTarget().then(setTarget).catch(reporting('scan target', setProblem));
    void loadCollections().catch(reporting('your collections', setProblem));
    return state.subscribe((snapshot) => setConnection(snapshot.connection));
  }, [state, loadCollections]);

  const chooseTarget = useCallback(
    (uid: string) => {
      if (!uid) return;
      setTarget(uid);
      void state
        .rememberScanTarget(uid)
        .catch(reporting('remembering where to scan', setProblem));
    },
    [state],
  );

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
        collection_uid: target,
      });
      setFlash({ name: candidate.name, copy });
      setLastAdded({ candidate, finish });
      setResult(null);
      setTimeout(() => setFlash(null), 950);
    },
    [state, target],
  );

  /** Put back a card that should not have gone in. */
  const undoLast = useCallback(async () => {
    if (!lastAdded) return;
    setProblem('');
    try {
      await state.addCard({
        printing_id: lastAdded.candidate.printing_id,
        card_name: lastAdded.candidate.name,
        finish: lastAdded.finish,
        collection_uid: target,
        quantity: -1,
      });
      // The repeat guard held this card off for four seconds so it would not
      // go in twice. Having just taken it out, that hold is wrong: the next
      // frame is probably the same card being scanned again on purpose.
      guard.current.reset();
      setStatus(`Took ${lastAdded.candidate.name} back out`);
      setLastAdded(null);
    } catch (err) {
      setProblem(recordCrash(err, 'undoing', false).message);
    }
  }, [lastAdded, state, target]);

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
        // "Could not read that one" is true and useless. What the desktop
        // actually got off the card is the whole diagnosis: no text at all
        // means the picture was the problem, text with the wrong name means
        // the read was, and a name it could not find means the catalogue is.
        const read = (reply.capture?.text ?? '').replace(/\s+/g, ' ').trim();
        setStatus(
          reply.candidates?.length
            ? 'Which printing is this?'
            : reply.capture?.card_detected === false
              ? 'No card found in the picture. Fill more of the frame, or ' +
                'zoom in so the phone uses the other lens.'
              : read
                ? `Read "${read.slice(0, 70)}" but matched nothing.`
                : 'Nothing legible in that picture. Try more light, or lock ' +
                  'the focus once it looks sharp.',
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
  // A phone that has never synced has no collection rows yet, and a picker
  // with nothing in it but "New collection" suggests the default one does not
  // exist. It always does.
  const shelves = collections.some(
    (c) => c.collection_uid === DEFAULT_COLLECTION_UID,
  )
    ? collections
    : [
        {
          collection_uid: DEFAULT_COLLECTION_UID,
          name: 'Main Collection',
          cards: 0,
        } as CollectionRow,
        ...collections,
      ];
  const targetName =
    shelves.find((c) => c.collection_uid === target)?.name ?? 'Main Collection';

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

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
      <View style={styles.header}>
        <Text style={styles.target}>Scanning into {targetName}</Text>
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

      <CollectionBar
        collections={shelves}
        selected={target}
        onSelect={chooseTarget}
        onCreate={async (name) => {
          const uid = await state.newCollection(name);
          await loadCollections();
          return uid;
        }}
        showCounts={false}
      />
      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

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

      {lastAdded ? (
        <View style={styles.undoRow}>
          <Text style={styles.undoText} numberOfLines={1}>
            Filed {lastAdded.candidate.name} ({lastAdded.candidate.set_code.toUpperCase()}{' '}
            #{lastAdded.candidate.collector_number})
          </Text>
          <Pressable
            style={styles.undoButton}
            onPress={() => {
              void undoLast();
            }}
          >
            <Text style={styles.undoButtonText}>Wrong? Undo</Text>
          </Pressable>
        </View>
      ) : null}

      {/*
        Always on screen, not behind a button. It was behind one, and the
        answer that came back was "not seeing zoom options" — a control nobody
        finds is a control that does not exist. Two rows is the compromise:
        present, but not the wall of options the web version had.
      */}
      <View style={styles.panel}>
        <View style={styles.settingRow}>
          <Text style={styles.settingName}>Zoom</Text>
          <Pressable
            style={styles.step}
            onPress={() => change({ zoom: stepZoom(settings.zoom, -1) })}
          >
            <Text style={styles.stepText}>-</Text>
          </Pressable>
          <Pressable
            style={styles.track}
            onLayout={(event) => setTrackWidth(event.nativeEvent.layout.width)}
            onPress={(event) => {
              if (trackWidth <= 0) return;
              change({
                zoom: zoomAt(event.nativeEvent.locationX / trackWidth),
              });
            }}
          >
            <View style={[styles.fill, { width: `${settings.zoom * 100}%` }]} />
            {/* Where the lens actually starts responding. */}
            <View
              style={[styles.deadzone, { width: `${ZOOM_DEADZONE * 100}%` }]}
            />
          </Pressable>
          <Pressable
            style={styles.step}
            onPress={() => change({ zoom: stepZoom(settings.zoom, 1) })}
          >
            <Text style={styles.stepText}>+</Text>
          </Pressable>
          <Text style={styles.settingValue}>{zoomLabel(settings.zoom)}</Text>
        </View>

        <View style={styles.settingRow}>
          <Pressable
            style={[styles.toggle, settings.torch && styles.toggleOn]}
            onPress={() => change({ torch: !settings.torch })}
          >
            <Text style={styles.toggleText}>
              {settings.torch ? 'Torch on' : 'Torch off'}
            </Text>
          </Pressable>
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
              {settings.autofocus === 'on' ? 'Focus: auto' : 'Focus: locked'}
            </Text>
          </Pressable>
          <Pressable
            style={styles.toggle}
            onPress={() => setShowSettings((open) => !open)}
          >
            <Text style={styles.toggleText}>{showSettings ? 'Hide' : 'Help'}</Text>
          </Pressable>
        </View>
      </View>

      {showSettings ? (
        <View style={styles.panel}>

          <Text style={styles.hint}>
            Zoom is the lens control. Android gives no way to ask for the
            telephoto by name, but zooming in makes the phone switch to it —
            and that is the lens that can focus on a card held close.
          </Text>
          <Text style={styles.hint}>
            The shaded part of the bar does nothing. Anything below it asks
            for less than 1x, which the camera rounds back up to 1x, so the
            picture cannot change there. + jumps straight over it and tapping
            the bar snaps past it.
          </Text>
          <Text style={styles.hint}>
            The card does not need to fill the frame: a small sharp one beats
            a large blurry one. Lock the focus once it looks right and it will
            stop hunting between cards.
          </Text>
        </View>
      ) : null}

      {result?.candidates?.length ? (
        <View style={styles.picker}>
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
        </View>
      ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  scroll: { flex: 1 },
  content: { padding: 14, gap: 10, paddingBottom: 40 },
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
    height: 22,
    borderRadius: 4,
    backgroundColor: '#2d3142',
    overflow: 'hidden',
    justifyContent: 'center',
  },
  fill: {
    position: 'absolute',
    left: 0,
    height: 22,
    backgroundColor: '#e53e3e',
  },
  deadzone: {
    position: 'absolute',
    left: 0,
    height: 22,
    borderRightWidth: 1,
    borderRightColor: '#8a8f9c',
    backgroundColor: 'rgba(15,17,23,0.45)',
  },
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
  problem: { color: '#e53e3e', fontSize: 12, lineHeight: 18 },
  undoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 9,
  },
  undoText: { color: '#8a8f9c', fontSize: 12, flex: 1 },
  undoButton: {
    borderColor: '#e53e3e',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  undoButtonText: { color: '#e53e3e', fontSize: 12, fontWeight: '700' },
  picker: { gap: 0 },
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
