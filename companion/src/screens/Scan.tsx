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

import type { AppState, Connection, IndexFetch } from '../lib/app-state.ts';
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
import type { TagCandidate } from '../lib/protocol.ts';
import { shrinkForQueue } from '../lib/shrink.ts';
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
  // The verb matters: filing a card and tagging one you already own look
  // identical on a green flash, and they are opposite operations.
  const [flash, setFlash] = useState<
    { name: string; copy: number; verb: string } | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<CameraSettings>(
    DEFAULT_CAMERA_SETTINGS,
  );
  const [connection, setConnection] = useState<Connection>('unknown');
  const [indexFetch, setIndexFetch] = useState<IndexFetch | null>(null);
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  // Starts at the default rather than empty: a card filed in the moment
  // between mounting and the stored target arriving would go nowhere
  // nameable.
  const [target, setTarget] = useState(DEFAULT_COLLECTION_UID);
  /**
   * Further lists every scanned card is tagged into.
   *
   * Separate from `target` because they are different things: a card is
   * FILED in one place and TAGGED into as many lists as you like. One pass
   * over a box is usually several answers at once — these are mine, these
   * are for the Modern deck, these are going in the sale binder — and scan
   * time is the only cheap moment to say so. Afterwards the cards are back
   * in the box and the knowledge is gone.
   */
  const [alsoTag, setAlsoTag] = useState<string[]>([]);
  /** How many photographed cards are waiting for the PC. */
  const [queued, setQueued] = useState(0);
  /**
   * Whether this phone holds the card index yet.
   *
   * Pulled from the PC, never shipped in the build: it changes with every
   * set, and an app that bundled one would be wrong within weeks and only
   * fixable by shipping another app.
   */
  const [index, setIndex] = useState<{ rows: number; ready: boolean }>(
    { rows: 0, ready: false });
  // Read from the snapshot, so a download started here and left behind is
  // still shown on the way back.
  // A percentage only once there is a size to measure against; 1 stands
  // for "started" so the bar is never a stationary zero.
  const pulling = indexFetch?.total
    ? Math.max(1, Math.round((indexFetch.done / indexFetch.total) * 100))
    : indexFetch ? 1 : 0;
  const indexStage = indexFetch
    ? (indexFetch.source === 'scryfall'
      ? `Downloading ${indexFetch.stage} from Scryfall`
      : `Fetching ${indexFetch.stage} from your PC`)
    : '';
  const [draining, setDraining] = useState(false);
  const [problem, setProblem] = useState('');
  // The green flash is gone in under a second. What was filed has to stay on
  // screen afterwards, because a wrong card is not always obvious in the
  // moment and the alternative is finding it weeks later in the collection.
  // Keyed the way the PHONE names a stack. The desktop's row id cannot be
  // used to undo a tag the phone applied locally, and offline there is no
  // desktop row id at all.
  const [tagged, setTagged] = useState<
    { stackKey: string; name: string } | null
  >(null);
  const [lastAdded, setLastAdded] = useState<{
    candidate: ScanCandidate;
    finish: string;
    /**
     * How many of this card went in on this pass.
     *
     * Boxes contain playsets, and saying "four of these" beats
     * photographing the same card four times — which is slower, less
     * reliable, and now actively fought by the repeat guard, which holds a
     * card off for four seconds precisely so a card sitting in frame does
     * not file twice. Without a count, filing a playset means waiting out
     * that hold between every copy.
     */
    copies: number;
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
  /**
   * Whether a scan FILES a new card or TAGS one you already own.
   *
   * The difference is the whole reason this mode exists. Adding is right when
   * you have just bought a box and are entering it. It is wrong when you are
   * walking a pile you already own picking out a bundle to sell — there, a
   * second copy is not a tag, it is a counting error you will not notice for
   * months, and it inflates both what you own and what it is worth.
   *
   * Deliberately not remembered between visits, unlike the target collection.
   * Adding is what the scanner is for nine times out of ten, and coming back
   * to find it silently in the other mode is how a stocktake goes wrong.
   */
  const [mode, setMode] = useState<'add' | 'tag'>('add');
  // You own this printing more than one way — foil and nonfoil, two
  // conditions — and which physical object goes in the bundle is a question
  // only the person holding it can answer.
  const [choosing, setChoosing] = useState<TagCandidate[] | null>(null);

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
    return state.subscribe((snapshot) => {
      setConnection(snapshot.connection);
      setIndexFetch(snapshot.indexFetch ?? null);
    });
  }, [state, loadCollections]);

  const chooseTarget = useCallback(
    (uid: string) => {
      if (!uid) return;
      setTarget(uid);
      // The card is already in whatever it is filed into, so that list
      // drops out of the extras rather than sitting there as a no-op.
      setAlsoTag((tags) => tags.filter((t) => t !== uid));
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
      if (mode === 'tag') {
        const out = await state.tagIntoGroup(
          candidate.printing_id, target, finish,
        );
        // Three outcomes, and a scanner that showed the same thing for all
        // three would be lying about two of them.
        if (out.candidates?.length) {
          setChoosing(out.candidates);
          setResult(null);
          setStatus('You own this one more than one way — which copy?');
          return;
        }
        if (!out.owned) {
          // NOT an error, and NOT a reason to add it. "This card is not in
          // your collection" is real information when you are picking a
          // bundle out of a pile.
          setResult(null);
          setStatus(`${candidate.name} isn't in your collection — nothing tagged.`);
          return;
        }
        setFlash({
          name: candidate.name,
          copy,
          verb: out.tagged ? 'TAGGED' : 'ALREADY IN',
        });
        setTagged(out.stack_key
          ? { stackKey: out.stack_key, name: candidate.name }
          : null);
        setResult(null);
        setTimeout(() => setFlash(null), 950);
        return;
      }
      await state.addCard({
        printing_id: candidate.printing_id,
        card_name: candidate.name,
        finish,
        collection_uid: target,
        also_collection_uids: alsoTag,
      });
      setFlash({
        name: candidate.name,
        copy,
        verb: alsoTag.length ? `ADDED +${alsoTag.length}` : 'ADDED',
      });
      setLastAdded({ candidate, finish, copies: 1 });
      setResult(null);
      setTimeout(() => setFlash(null), 950);
    },
    [state, target, mode, alsoTag],
  );

  /** Answer "you own this two ways" by naming the stack. */
  const chooseStack = useCallback(
    async (candidate: TagCandidate) => {
      setChoosing(null);
      try {
        const out = await state.tagStack(candidate.stack_key ?? '', target);
        setFlash({
          name: candidate.card_name,
          copy: 1,
          verb: out.tagged ? 'TAGGED' : 'ALREADY IN',
        });
        setTagged({ stackKey: candidate.stack_key ?? '',
                    name: candidate.card_name });
        setTimeout(() => setFlash(null), 950);
      } catch (err) {
        setProblem(recordCrash(err, 'tagging it', false).message);
      }
    },
    [state, target],
  );

  /** Take the last tag back off. The card itself is untouched. */
  const undoTag = useCallback(async () => {
    if (!tagged) return;
    setProblem('');
    try {
      await state.untagStack(tagged.stackKey, target);
      guard.current.reset();
      setStatus(`Took ${tagged.name} back out of the group`);
      setTagged(null);
    } catch (err) {
      setProblem(recordCrash(err, 'undoing', false).message);
    }
  }, [tagged, state, target]);

  /**
   * One more of the card just filed.
   *
   * Deliberately NOT through the repeat guard. The guard exists to catch a
   * card the camera saw twice; this is a person saying "there are four of
   * these", which is the opposite — an answer, not an accident.
   */
  const addAnother = useCallback(async () => {
    if (!lastAdded) return;
    setProblem('');
    try {
      await state.addCard({
        printing_id: lastAdded.candidate.printing_id,
        card_name: lastAdded.candidate.name,
        finish: lastAdded.finish,
        collection_uid: target,
        also_collection_uids: alsoTag,
      });
      setLastAdded((last) =>
        last ? { ...last, copies: last.copies + 1 } : last);
      setStatus(`${lastAdded.candidate.name} ×${lastAdded.copies + 1}`);
    } catch (err) {
      setProblem(recordCrash(err, 'adding another', false).message);
    }
  }, [lastAdded, state, target, alsoTag]);

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
      setStatus(lastAdded.copies > 1
        ? `${lastAdded.candidate.name} ×${lastAdded.copies - 1}`
        : `Took ${lastAdded.candidate.name} back out`);
      // Down one, not gone. Undoing the fourth of a playset should leave
      // three and the buttons still there, rather than clearing the row and
      // stranding the other three with nothing to press.
      setLastAdded((last) =>
        last && last.copies > 1 ? { ...last, copies: last.copies - 1 } : null);
    } catch (err) {
      setProblem(recordCrash(err, 'undoing', false).message);
    }
  }, [lastAdded, state, target]);

  const handlePhoto = useCallback(
    async (base64: string) => {
      busyRef.current = true;
      setBusy(true);
      setStatus('Reading...');
      // Local first, PC second.
      //
      // The phone can place a card by itself now, and doing that before
      // asking the PC means a scan never waits on a network round trip to
      // succeed — which is the difference between a box that files at the
      // speed of the camera and one that files at the speed of the wifi.
      //
      // The PC is still better: it has the fuzzy name matcher and the whole
      // catalogue, so anything the phone cannot place EXACTLY still goes to
      // it. This is a fast path, not a replacement.
      try {
        const local = await state.identifyOffline(base64);
        if (local) {
          const decision = guard.current.consider(
            local.printing.name, Date.now());
          if (!decision.file) {
            setStatus('Same card still in frame');
            return;
          }
          await state.addCard({
            printing_id: local.printing.printing_id,
            card_name: local.printing.name,
            finish: local.foilHint ? 'foil' : 'nonfoil',
            collection_uid: target,
            also_collection_uids: alsoTag,
          });
          setFlash({
            name: local.printing.name, copy: decision.copy, verb: 'ADDED',
          });
          setTimeout(() => setFlash(null), 950);
          setStatus('Added — next card');
          return;
        }
      } catch {
        // The recogniser or the index let us down. The PC is the answer to
        // that, and it is the next thing tried.
      }

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

        // On a phone with no PC there is nobody to keep it FOR.
        //
        // Queueing here promised "it files itself when you are back in
        // range" to somebody who has no range to come back to: the queue
        // would never drain, the photo would sit for ever, and a box
        // scanned in bad light would quietly become four hundred stored
        // pictures. If this phone could not read the card, nothing else
        // is going to — so say so, and let them take another go at it
        // while the card is still in their hand.
        if (state.soloForever) {
          setStatus('Could not read that one. Try more light, fill more of '
                    + 'the frame, or type the name in from the Cards tab.');
          return;
        }

        // There IS a PC, just not right now. The card in your hand is
        // still real, so the picture is kept for it rather than discarded.
        try {
          // Shrunk before storing, never before sending: the live path
          // hands the PC everything it could have had.
          await state.queueScan(await shrinkForQueue(base64), target, alsoTag);
          setQueued(await state.queuedScans());
          setFlash({ name: 'Saved for later', copy: 1, verb: 'QUEUED' });
          setTimeout(() => setFlash(null), 950);
          setStatus('No PC — kept the picture. It files itself when you are '
                    + 'back in range.');
        } catch {
          // Queueing is the fallback; if IT fails, say the real thing.
          setStatus(recordCrash(err, 'reading the card', false).message);
        }
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [state, file, target, alsoTag],
  );

  /**
   * Send the queue to the PC.
   *
   * Only what it is CERTAIN of gets filed. Anything less waits for a human,
   * exactly as it would have live — more so, since nobody was watching when
   * it went in.
   */
  const drain = useCallback(async () => {
    if (draining) return;
    setDraining(true);
    try {
      const out = await state.drainScans();
      setQueued(await state.queuedScans());
      if (out.filed || out.undecided || out.failed) {
        const parts = [];
        if (out.filed) parts.push(`filed ${out.filed}`);
        if (out.undecided) parts.push(`${out.undecided} need a decision`);
        if (out.failed) parts.push(`${out.failed} unreadable`);
        setStatus(`Caught up — ${parts.join(', ')}.`);
      }
    } catch (err) {
      setProblem(recordCrash(err, 'filing the queue', false).message);
    } finally {
      setDraining(false);
    }
  }, [state, draining]);

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
        // With the index in hand the phone identifies cards itself, so
        // losing the PC is no longer a reason to stop the loop.
        offlineCapable: index.ready,
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
  }, [auto, connection, capture, index.ready]);

  const offline = connection === 'offline' || connection === 'unpaired';

  // Back in range with a queue: work through it without being asked. The
  // whole point is that scanning offline costs nothing extra later.
  //
  // Declared after `offline` on purpose — it is the condition, and reading
  // a binding from further down the render body is the kind of thing that
  // works until somebody reorders two lines.
  useEffect(() => {
    if (offline || queued === 0 || draining) return;
    void drain();
    // `drain` is deliberately absent: its identity changes whenever
    // `draining` flips, and depending on it would restart the drain it
    // just finished.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offline, queued]);

  // What is already waiting, on the way in.
  useEffect(() => {
    void state.queuedScans().then(setQueued).catch(() => {});
    void state.catalogueReady().then(setIndex).catch(() => {});
  }, [state]);

  /**
   * Fetch the index while the PC is there, so scanning works when it is not.
   *
   * Resumable: the walk is keyed on the last printing id, so wandering out
   * of range mid-pull costs the current page, not the whole download.
   */
  /**
   * Start the download, or join the one already going.
   *
   * The progress lives in the app snapshot rather than here, so leaving
   * this screen no longer throws away a download in flight — which cost a
   * third of a 74 MB pull the first time somebody changed tabs.
   */
  const pullIndex = useCallback(async () => {
    setProblem('');
    try {
      const out = await state.startIndexFetch();
      setIndex(await state.catalogueReady());
      setStatus(out.source === 'scryfall'
        ? 'Card index downloaded. Scanning now works with no PC at all.'
        : 'Card index fetched from your PC.');
    } catch (err) {
      setProblem(recordCrash(err, 'fetching the card index', false).message);
    }
  }, [state]);
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
          <Text style={styles.flashTick}>{flash.verb}</Text>
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
      {/*
        What a scan DOES. Two words rather than a settings toggle, because
        getting it wrong is silent in both directions: filing when you meant
        to tag inflates what you own, and tagging when you meant to file loses
        cards you have just bought.
      */}
      <View style={styles.modeRow}>
        <Pressable
          style={[styles.mode, mode === 'add' && styles.modeOn]}
          onPress={() => setMode('add')}
        >
          <Text style={[styles.modeText, mode === 'add' && styles.modeTextOn]}>
            Add cards
          </Text>
        </Pressable>
        <Pressable
          style={[styles.mode, mode === 'tag' && styles.modeOn]}
          onPress={() => {
            setMode('tag');
            setStatus('Tagging what you already own — nothing is added.');
          }}
        >
          <Text style={[styles.modeText, mode === 'tag' && styles.modeTextOn]}>
            Tag what I own
          </Text>
        </Pressable>
      </View>
      <Text style={styles.modeHint}>
        {mode === 'tag'
          ? 'Scan cards you already own to put them in a group — a bundle to ' +
            'sell, or a pile to give away. Nothing is added to your ' +
            'collection and nothing is removed.'
          : 'Scan cards to file them into your collection.'}
      </Text>

      <View style={styles.header}>
        <Text style={styles.target}>
          {mode === 'tag' ? 'Tagging into' : 'Scanning into'} {targetName}
        </Text>
        <Pressable
          style={[styles.chip, auto && styles.chipOn]}
          onPress={() => {
            if (!auto && offline && !index.ready) {
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

      {/*
        Tagging never moves a card or counts it twice — the lists just
        mention it. Offered only when filing: tag mode already targets one
        group, and a second tagging control there would be two answers to
        the same question.
      */}
      {mode === 'add' && shelves.length > 1 ? (
        <View style={styles.alsoRow}>
          <Text style={styles.alsoLabel}>Also tag</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={styles.alsoChips}>
              {shelves
                .filter((c) => c.collection_uid !== target)
                .map((c) => {
                  const on = alsoTag.includes(c.collection_uid);
                  return (
                    <Pressable
                      key={c.collection_uid}
                      style={[styles.chip, on && styles.chipOn]}
                      onPress={() =>
                        setAlsoTag((tags) =>
                          on
                            ? tags.filter((t) => t !== c.collection_uid)
                            : [...tags, c.collection_uid],
                        )
                      }
                    >
                      <Text style={[styles.chipText, on && styles.chipTextOn]}>
                        {c.name}
                      </Text>
                    </Pressable>
                  );
                })}
            </View>
          </ScrollView>
        </View>
      ) : null}

      {/*
        The index, and whether scanning will work away from the PC.

        Said before it matters rather than after: finding out in a garage
        that the phone cannot identify anything is finding out too late.
      */}
      {/*
        Shown while a fetch is running even once the rows look sufficient:
        the bar is the only thing saying the download is still going, and
        hiding it the moment the index passes for ready is what made a pull
        look like it had died on a tab switch.
      */}
      {!index.ready || pulling > 0 ? (
        <Pressable
          style={styles.queueBar}
          disabled={pulling > 0}
          onPress={() => void pullIndex()}
        >
          <Text style={styles.queueText}>
            {pulling > 0
              ? `${indexStage || 'Fetching the card index'}… ${pulling}%`
              : index.rows > 0
                ? 'Card index half-fetched — scanning needs all of it'
                // No PC is no longer a reason not to offer this. It comes
                // from the desktop when there is one and from Scryfall when
                // there is not, so the button works either way and only the
                // wait differs.
                : 'Get the card index once, then scanning works offline '
                  + 'forever.'}
          </Text>
          <Text style={styles.queueAction}>
            {pulling > 0 ? '' : 'Get it'}
          </Text>
        </Pressable>
      ) : null}

      {/*
        The queue, said out loud.

        A pile of unfiled cards that only exists in a database is the same
        as losing them — you have to know it is there to trust scanning out
        of range at all, and to know the box is not finished yet.
      */}
      {queued > 0 ? (
        <Pressable
          style={styles.queueBar}
          disabled={draining || (offline && !state.soloForever)}
          onPress={() => {
            // With no PC these can never be read, so the only thing left
            // to do with them is let them go.
            if (state.soloForever) {
              void state.discardQueuedScans()
                .then(() => state.queuedScans().then(setQueued))
                .catch(() => {});
              return;
            }
            void drain();
          }}
        >
          <Text style={styles.queueText}>
            {state.soloForever
              // No "waiting for your PC" on a phone that has none — the
              // wait would never end.
              ? `${queued} picture${queued === 1 ? '' : 's'} from before that `
                + 'only a PC could read'
              : `${queued} card${queued === 1 ? '' : 's'} waiting for your PC`}
          </Text>
          <Text style={styles.queueAction}>
            {draining
              ? 'Filing…'
              : state.soloForever
                ? 'Discard'
                : offline ? 'Out of range' : 'File them now'}
          </Text>
        </Pressable>
      ) : null}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {/*
        You own this printing more than one way. Which physical object goes in
        the bundle is a question only the person holding it can answer, and
        guessing tags the wrong card — a foil and a nonfoil are different
        objects worth different money.
      */}
      {choosing ? (
        <View style={styles.tagPicker}>
          <Text style={styles.tagPickerTitle}>Which copy?</Text>
          {choosing.map((option) => (
            <Pressable
              key={option.item_id}
              style={styles.tagPickerRow}
              onPress={() => void chooseStack(option)}
            >
              <Text style={styles.tagPickerText}>
                {option.finish} · {option.condition}
                {option.location ? ` · ${option.location}` : ''}
              </Text>
              <Text style={styles.modeHint}>{option.quantity} owned</Text>
            </Pressable>
          ))}
          <Pressable style={styles.tagPickerRow} onPress={() => setChoosing(null)}>
            <Text style={styles.modeHint}>Skip this one</Text>
          </Pressable>
        </View>
      ) : null}

      {tagged && mode === 'tag' ? (
        <View style={styles.undoRow}>
          <Text style={styles.undoText} numberOfLines={1}>
            Tagged {tagged.name}
          </Text>
          <Pressable style={styles.undoButton} onPress={() => void undoTag()}>
            <Text style={styles.undoButtonText}>Wrong? Undo</Text>
          </Pressable>
        </View>
      ) : null}

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
            {lastAdded.copies > 1 ? `${lastAdded.copies}× ` : ''}
            {lastAdded.candidate.name} (
            {lastAdded.candidate.set_code.toUpperCase()}{' '}
            #{lastAdded.candidate.collector_number})
          </Text>
          {/*
            The playset button. A box of cards is mostly duplicates, and one
            tap per extra copy beats waiting out the four-second repeat hold
            with the card held in front of the lens.
          */}
          <Pressable
            style={styles.copyButton}
            onPress={() => {
              void addAnother();
            }}
          >
            <Text style={styles.copyButtonText}>+1 more</Text>
          </Pressable>
          <Pressable
            style={styles.undoButton}
            onPress={() => {
              void undoLast();
            }}
          >
            <Text style={styles.undoButtonText}>
              {lastAdded.copies > 1 ? '−1' : 'Undo'}
            </Text>
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
  alsoRow: { gap: 6, marginTop: 8 },
  copyButton: {
    borderColor: '#38a169',
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  copyButtonText: { color: '#68d391', fontSize: 13, fontWeight: '600' },
  queueBar: {
    alignItems: 'center',
    backgroundColor: '#1d2433',
    borderColor: '#2f6f9f',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  // `flex: 1` on the label and no shrink on the action. Without it a long
  // message cannot give ground, so it runs underneath the action text
  // rather than wrapping — which is what "those two parts are
  // overlapping" looks like on a narrow phone.
  queueText: { color: '#e4e6eb', flex: 1, fontSize: 13, marginRight: 10 },
  queueAction: {
    color: '#7db8e8',
    flexShrink: 0,
    fontSize: 13,
    fontWeight: '600',
  },
  alsoLabel: { color: '#8a8f9c', fontSize: 12 },
  alsoChips: { flexDirection: 'row', gap: 6 },
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
  modeRow: { flexDirection: 'row', gap: 8 },
  mode: {
    flex: 1,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: 'center',
  },
  // Amber rather than the usual green, because this mode does something
  // genuinely different from what the screen normally does, and a scanner
  // that looks the same in both is one you will use in the wrong one.
  modeOn: { backgroundColor: '#b7791f', borderColor: '#b7791f' },
  modeText: { color: '#c9ced9', fontSize: 14 },
  modeTextOn: { color: '#ffffff', fontWeight: '700' },
  modeHint: { color: '#8a8f9c', fontSize: 12, lineHeight: 17 },
  tagPicker: {
    borderColor: '#b7791f',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 4,
  },
  tagPickerTitle: { color: '#e4e6eb', fontSize: 15, fontWeight: '700' },
  tagPickerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: '#2d3142',
  },
  tagPickerText: { color: '#e4e6eb', fontSize: 14 },
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
