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

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  Vibration,
  View,
} from 'react-native';

import type { AppState, Connection, IndexFetch } from '../lib/app-state.ts';
import { AutoScanner, explain } from '../lib/autoscan.ts';
import { artSource } from '../lib/images.ts';
import { orderPrintings, pickerCount } from '../lib/printing-picker.ts';
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
import { describeStage } from '../lib/index-source.ts';
import { asScanResult } from '../lib/scan-miss.ts';
import { NAME_SHORTLIST } from '../lib/identify.ts';
import { describeLocalMiss } from '../lib/scan-miss.ts';
import { whileBusy } from '../lib/busy.ts';
import { BuzzGuard } from '../lib/buzz-policy.ts';
import { FrameGuide } from './FrameGuide.tsx';
import { SetSymbol } from './SetSymbol.tsx';
import { rarityColour } from '../lib/set-symbol.ts';
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
  /**
   * The green confirmation, and WHICH printing it was.
   *
   * The name alone is not enough. Several earlier passes filed the
   * right card in the wrong version, and nothing on screen would have
   * shown it: the flash said "ADDED · Royal Assassin" for a card that
   * exists twenty-nine times. The set symbol, the code and the number
   * are the difference, and this is the one moment the card is still
   * in your hand to check them against.
   */
  const [flash, setFlash] = useState<
    {
      name: string;
      /** Which copy of this card this is -- duplicate detection. */
      copy: number;
      /** So the flash can show the card rather than describe it. */
      printingId?: string;
      /**
       * How many went in at once, which is a different question.
       *
       * `copy` says "this is the third time you have scanned this
       * card"; this says "ten of them were just filed". Reusing one
       * for the other would have the flash announce copy #10 of a
       * card you own ten of for the first time.
       */
      added?: number;
      verb: string;
      setCode?: string;
      number?: string;
      rarity?: string;
    } | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [auto, setAuto] = useState(false);
  /**
   * Whether the cards being scanned are prerelease promos.
   *
   * A prerelease promo prints its original set code and number, and the
   * catalogue files it somewhere else -- MKM 0200 on the card, pmkm
   * 200s in the index. The only thing separating it from the ordinary
   * foil is the holofoil date stamp in the art, which is a picture, not
   * text, so no amount of OCR will ever settle it. The person holding
   * the card can see it in a moment.
   *
   * Deliberately NOT remembered between sessions, unlike the target
   * collection. A sticky promo flag would silently file an ordinary box
   * into the promo set weeks later, and the cost of being wrong that
   * way is much higher than one tap at the start of a stack.
   */
  const [promo, setPromo] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<CameraSettings>(
    DEFAULT_CAMERA_SETTINGS,
  );
  const [connection, setConnection] = useState<Connection>('unknown');
  const [indexFetch, setIndexFetch] = useState<IndexFetch | null>(null);
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  /**
   * What each set code means.
   *
   * "GRN #184" is not an answer to "which of these seven am I holding",
   * especially for an old card nobody has the codes memorised for. The
   * name, the year and the set's own symbol are what people actually
   * recognise a printing by.
   *
   * Empty until an index fetch has filled it, so every row falls back
   * to the bare code rather than showing a blank.
   */
  const [sets, setSets] = useState<Record<string, {
    name: string; year: number; iconUri: string;
  }>>({});
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
  const [index, setIndex] = useState<{
    rows: number; ready: boolean; scanReady: boolean;
  }>(
    { rows: 0, ready: false, scanReady: false });
  // Read from the snapshot, so a download started here and left behind is
  // still shown on the way back.
  // A percentage only once there is a size to measure against; 1 stands
  // for "started" so the bar is never a stationary zero.
  const pulling = indexFetch?.total
    ? Math.max(1, Math.round((indexFetch.done / indexFetch.total) * 100))
    : indexFetch ? 1 : 0;
  // The source is not known until a round trip decides it, and claiming
  // one before then would name the wrong place half the time --
  // `describeStage` holds that back until it is known.
  const indexStage = indexFetch
    ? describeStage(indexFetch.stage, indexFetch.source)
    : '';
  /*
    Typing a card instead of photographing it.

    Two of the scanner's limits are structural rather than fixable: a
    basic land has eight hundred printings and no name lookup can
    choose between them, and a card printed before 2014 carries no set
    code at all. Fighting the camera over those wastes an evening.

    It ends in the same picker a scan does, so there is one way to
    choose a printing and one way to file it.
  */
  const [typed, setTyped] = useState('');
  /*
    How many of the card you are about to tap.

    Thirty-seven basics go into a deck and nobody scans them one at a
    time; the honest way in for those is to say Island, say ten, and
    tap the printing once. It applies to a scanned card too -- a
    playset out of a box is four taps otherwise.

    Deliberately NOT sticky. It resets to one after every card,
    because a quantity left at ten by the last card is how you end up
    owning ten of the next one.
  */
  const [howMany, setHowMany] = useState(1);
  /*
    Which set, when the list is longer than a list should be.

    A typed Island offers 671 printings. Sorting them newest-first
    makes the top of the list reasonable and leaves the other 631
    unreachable, so this filters on set name or code before the sort.
  */
  const [setFilter, setSetFilter] = useState('');
  const [suggestions, setSuggestions] = useState<Array<{
    name: string; printings: number;
  }>>([]);

  useEffect(() => {
    const term = typed.trim();
    if (term.length < 2) {
      setSuggestions([]);
      return;
    }
    let live = true;
    // Debounced: every keystroke is a LIKE over a hundred thousand
    // rows, and nobody needs an answer to "so" on the way to "sol".
    const timer = setTimeout(() => {
      void state.searchCardNames(term)
        .then((rows) => { if (live) setSuggestions(rows); })
        .catch(() => { if (live) setSuggestions([]); });
    }, 200);
    return () => { live = false; clearTimeout(timer); };
  }, [typed, state]);

  /** Show every printing of a typed name in the usual picker. */
  const chooseTyped = useCallback(async (name: string) => {
    setProblem('');
    try {
      const rows = await state.printingsOf(name);
      if (!rows.length) {
        setStatus(`${name} is not in this phone's index.`);
        return;
      }
      setTyped('');
      setSuggestions([]);
      setResult(asScanResult({
        identity: { name, setCode: '', collectorNumber: '', foilHint: false },
        candidates: rows,
        autoAddable: false,
        reason: '',
      }));
      setStatus(`${name} — which printing?`);
    } catch (err) {
      setProblem(recordCrash(err, 'looking that card up', false).message);
    }
  }, [state]);

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
  /**
   * Whether this frame is worth feeling.
   *
   * Separate from the repeat guard because they answer different
   * questions: that one decides whether to FILE a card, this one decides
   * whether to say anything about having seen one. A card that cannot be
   * placed is never filed and is still worth a buzz.
   */
  const buzzer = useRef(new BuzzGuard());
  /** A short tap. The VIBRATE permission has always been in the build. */
  const buzz = useCallback((sighting: Parameters<BuzzGuard['consider']>[0]) => {
    if (!buzzer.current.consider(sighting, Date.now())) return;
    // Wrapped: a device with no motor, or one that refuses, must not
    // take a scan down with it.
    try {
      Vibration.vibrate(25);
    } catch {
      // Nothing to say. A missing buzz is not worth a message.
    }
  }, []);
  const scanner = useRef(new AutoScanner());
  const camera = useRef<CameraView | null>(null);
  // Where the pick list starts, and the scroller that has to reach it.
  // A question rendered below a full-height camera box is a question
  // nobody sees: the screen looks like it simply stopped.
  const scroller = useRef<ScrollView | null>(null);
  const pickerY = useRef(0);
  // The interval's closure would otherwise read whatever `busy` was when the
  // effect ran, and fire a second capture on top of the one in flight.
  const busyRef = useRef(false);
  /**
   * Whether a choice is waiting on screen.
   *
   * Auto scan has to stop for it. Carrying on would photograph the next
   * frame over the top of the question, replace the list mid-tap, and
   * file whatever the following card happened to be -- and the pile is
   * still sitting under the camera while someone reads seven printings.
   *
   * A ref because the loop's closure is installed once and would
   * otherwise read whatever this was when auto scan was switched on.
   */
  const pickingRef = useRef(false);
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
    async (candidate: ScanCandidate, finish: string, copy = 1,
           added = 1) => {
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
          printingId: candidate.printing_id,
          setCode: candidate.set_code,
          number: candidate.collector_number,
          rarity: candidate.rarity,
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
        quantity: added,
      });
      setFlash({
        name: candidate.name,
        copy,
        added,
        verb: alsoTag.length ? `ADDED +${alsoTag.length}` : 'ADDED',
        printingId: candidate.printing_id,
        setCode: candidate.set_code,
        number: candidate.collector_number,
        rarity: candidate.rarity,
      });
      // What Undo takes back out: all of them, not one.
      setLastAdded({ candidate, finish, copies: added });
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
    // BOTH, because the two readers want different things: the on-device
    // recogniser opens a file off the disk, and the PC is sent the bytes
    // over the wire. Passing one where the other was wanted is the bug
    // that made offline scanning fail from the day it shipped.
    async ({ uri, base64 }: { uri: string; base64: string }) => {
      // Raised and lowered by `whileBusy`, NOT by this body.
      //
      // It used to be set here and cleared in a `finally` attached to the
      // second of the two try blocks below, so every early return from
      // the first one left it stuck on. A stuck busy flag looks exactly
      // like work still in progress, so auto scan filed one card and then
      // waited for ever, in silence.
      await whileBusy(busyRef, async () => {
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
        // Whether the recogniser saw ANY text decides between "no card
        // in frame" and "a card I could not place" -- opposite news, and
        // both come back null from identifyOffline. Declared out here
        // because the branches that need it are past the end of this
        // block.
        // The TEXT, not just whether there was any.
        //
        // "Saw a card but could not place it" is true and useless: it
        // cannot distinguish a footer the recogniser never read from one
        // it read and could not match, and those want opposite fixes —
        // better light versus a missing printing. The PC path has always
        // reported what it got off the card; this is the same thing for
        // a phone with no PC, which is now most of them.
        let readText = '';
        // Why it failed, from the matcher rather than guessed at from
        // the text. The screen cannot tell "no footer in the picture"
        // from "a footer that matched nothing", and inventing an answer
        // produced a message that blamed the index for a key it had
        // never looked up.
        let missReason = '';
        // The printings it found but could not choose between. Thrown
        // away until now, which is why a status saying "needs a tap"
        // had nothing to tap.
        let missOffer: ScanResult | null = null;
        try {
          const local = await state.identifyOffline(
            uri,
            ({ text, result }) => {
              readText = text;
              missReason = result?.reason ?? '';
              missOffer = result && result.candidates.length
                ? asScanResult(result)
                : null;
            },
            { preferPromo: promo });
          if (local) {
            // Proof the machinery works. Without this, a paired phone
            // placing cards locally never cleared failures, so three
            // scattered PC hiccups across a long session would stop the
            // loop mid-box.
            scanner.current.succeeded();
            buzz({ kind: 'card', name: local.printing.name });
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
              name: local.printing.name,
              copy: decision.copy,
              printingId: local.printing.printing_id,
              verb: 'ADDED',
              setCode: local.printing.set_code,
              number: local.printing.collector_number,
              rarity: (local.printing as { rarity?: string }).rarity,
            });
            setTimeout(() => setFlash(null), 950);
            setStatus('Added — next card');
            return;
          }
        } catch (err) {
          // The recogniser or the index let us down. The PC is the answer to
          // that, and it is the next thing tried.
          //
          // Recorded rather than swallowed. A bare `catch {}` here is what
          // hid a broken local scanner behind a working PC for the whole
          // life of the feature: every phone silently fell through, and
          // nothing anywhere said why.
          recordCrash(err, 'reading the card on this phone', false);
        }

        try {
          const reply = await identifyPhoto(state.scanClient, base64);
          scanner.current.succeeded();
          const top = reply.candidates?.[0];

          if (reply.auto_addable && top) {
            buzz({ kind: 'card', name: top.name });
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
          // The desktop answers "was there a card" directly, so this
          // does not have to be inferred from whether the match landed.
          buzz(reply.capture?.card_detected === false
            ? { kind: 'nothing' }
            : { kind: 'unreadable' });
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
          // NOT counted as a failure here. What this catch means depends
          // entirely on which branch below it lands in, and only one of
          // them is a round trip that failed. Counting first and undoing
          // it later works only as long as nobody adds a branch that
          // returns in between -- which is exactly how the busy flag
          // leaked two versions ago.

          // On a phone with no PC there is nobody to keep it FOR.
          //
          // Queueing here promised "it files itself when you are back in
          // range" to somebody who has no range to come back to: the queue
          // would never drain, the photo would sit for ever, and a box
          // scanned in bad light would quietly become four hundred stored
          // pictures. If this phone could not read the card, nothing else
          // is going to — so say so, and let them take another go at it
          // while the card is still in their hand.
          // Why it failed decides what to say. Getting that wrong sends
          // somebody off to fix their lighting when the app simply has
          // nothing to match against — which cost a tester a whole session.
          if (!index.scanReady) {
            setStatus(index.rows > 0
              ? 'The card index is only part-downloaded, so nothing can be '
                + 'matched yet. Tap "Get it" above to finish it.'
              : 'No card index on this phone yet — scanning has nothing to '
                + 'match against. Tap "Get it" above once, then this works '
                + 'anywhere.');
            return;
          }

          if (state.soloForever) {
            // Not a failure. There is no PC, so there was no round trip
            // to fail -- the photo simply had no card the phone could
            // place, which is what the gap between two cards looks like.
            // Counting these stopped auto scan after about three
            // seconds of reaching for the next card.
            scanner.current.missed();
            // Pass or fail, a card being THERE is worth feeling: it means
            // stop moving your hand. An empty frame is not.
            buzz(readText ? { kind: 'unreadable' } : { kind: 'nothing' });
            // A list to tap beats a sentence about a list. The picker
            // is the same one the desktop's ambiguous reads have always
            // used; only the phone had no way to reach it.
            if (missOffer) setResult(missOffer);
            setStatus(describeLocalMiss(readText, missReason));
            return;
          }

          // There IS a PC, just not right now. The card in your hand is
          // still real, so the picture is kept for it rather than discarded.
          //
          // This is the one branch where a round trip genuinely failed,
          // so it is the one that counts against the limit: three of
          // these in a row means the desktop has stopped answering and
          // hammering it once a second helps nobody.
          scanner.current.failed();
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
        }
      }, setBusy);
    },
    // `index` belongs here. Without it this callback is memoised with the
    // FIRST render's value — an empty index, before the lookup that fills
    // it has resolved — so every failure reported "no card index on this
    // phone" on a phone holding all 105,000 cards. A stale closure that
    // says the opposite of the truth.
    [state, file, target, alsoTag, index, promo],
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
      // Auto scan photographs the frame every second or so and most of
      // those pictures hold nothing. Clicking on every one of them was a
      // constant rattle that reported nothing; the buzz replaces it, on
      // the frames where a card was actually seen.
      shutterSound: false,
    });
    if (!shot?.base64) {
      setStatus('The camera returned an empty picture.');
      return;
    }
    if (!shot.uri) {
      setStatus('The camera did not save the picture anywhere to read it '
                + 'from.');
      return;
    }
    // Frame-sameness is judged on the BYTES: two shots of a frozen camera
    // are identical data at different paths, so the path cannot detect it.
    scanner.current.captured(shot.base64);
    await handlePhoto({ uri: shot.uri, base64: shot.base64 });
  }, [handlePhoto]);

  // Asking a question means showing it. Scrolled to the TOP of the
  // list rather than the bottom of the page, because seven printings
  // are taller than the screen and `scrollToEnd` would land on "None
  // of these".
  /**
   * The printings on offer, filtered and ordered.
   *
   * Newest first because the card in somebody's hand is far more
   * often recent than not, and a set box in front of that because no
   * ordering rescues 671 Islands -- sorting them makes the top of
   * the list reasonable and leaves the other 631 unreachable.
   */
  const matching = useMemo(
    () => orderPrintings(result?.candidates ?? [], setFilter, sets),
    [result, setFilter, sets]);

  /*
    A new card on screen starts from one, and from no filter.

    Filing clears `result`, so this is also what puts the quantity
    back after a batch of ten goes in. A ten left over from the last
    card is how you come to own ten of the next one.
  */
  useEffect(() => {
    setHowMany(1);
    setSetFilter('');
  }, [result]);

  useEffect(() => {
    if (!result?.candidates?.length) return;
    const at = pickerY.current;
    const timer = setTimeout(
      () => scroller.current?.scrollTo({ y: Math.max(0, at - 8),
                                         animated: true }),
      // One frame, so the list has been laid out and `pickerY` is real.
      80,
    );
    return () => clearTimeout(timer);
  }, [result]);

  // Both pickers, mirrored into a ref the interval can read.
  useEffect(() => {
    pickingRef.current = !!result?.candidates?.length || !!choosing?.length;
  }, [result, choosing]);

  // The auto loop. Every decision it makes lives in AutoScanner, which is
  // tested in Node; this only carries them out.
  useEffect(() => {
    if (!auto) return;
    scanner.current.reset(Date.now());
    const timer = setInterval(() => {
      const decision = scanner.current.next({
        running: true,
        // A question on screen counts as busy: the loop must not
        // answer it by taking another picture.
        busy: busyRef.current || pickingRef.current,
        connection,
        now: Date.now(),
        // With the index in hand the phone identifies cards itself, so
        // losing the PC is no longer a reason to stop the loop.
        offlineCapable: index.scanReady,
      });
      if (decision.act === 'stop') {
        setAuto(false);
        if (decision.reason !== 'stopped') {
          setStatus(explain(decision.reason, state.soloForever));
        }
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
  }, [auto, connection, capture, index.scanReady]);

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
    void state.setDirectory().then(setSets).catch(() => {});
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
          {/*
            The card that just went in, not a description of it.

            The set symbol and number below answer "which printing"
            for someone who knows the codes. The picture answers it
            for everyone, while the card is still in your hand --
            which is the only moment the answer is any use.
          */}
          {flash.printingId ? (
            <Image
              style={styles.flashArt}
              source={artSource(flash.printingId, 'small')}
              resizeMode="contain"
              accessibilityLabel={flash.name}
            />
          ) : null}
          <Text style={styles.flashName}>{flash.name}</Text>
          {/*
            Which printing, while the card is still in your hand. The
            symbol is the fastest check of the three -- you can match a
            shape against the card without reading anything.
          */}
          {flash.setCode ? (
            <View style={styles.flashPrinting}>
              {sets[flash.setCode.toLowerCase()]?.iconUri ? (
                <SetSymbol
                  uri={sets[flash.setCode.toLowerCase()]?.iconUri ?? ''}
                  size={22}
                  colour={rarityColour(flash.rarity ?? '')}
                />
              ) : null}
              <Text style={styles.flashMeta}>
                {sets[flash.setCode.toLowerCase()]?.name
                  || flash.setCode.toUpperCase()}
                {flash.number ? ` · #${flash.number}` : ''}
              </Text>
            </View>
          ) : null}
          {(flash.added ?? 1) > 1 ? (
            <Text style={styles.flashCount}>{'×'}{flash.added}</Text>
          ) : null}
          {flash.copy > 1 ? (
            <Text style={styles.flashMeta}>copy #{flash.copy} of this card</Text>
          ) : null}
        </View>
      ) : null}

      <ScrollView
        ref={scroller}
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
            if (!auto && offline && !index.scanReady) {
              setStatus(explain('offline', state.soloForever));
              return;
            }
            setStatus(
              auto ? 'Point at a card' : 'Auto scan on — show it a card',
            );
            buzzer.current.reset();
            setAuto((on) => !on);
          }}
        >
          <Text style={[styles.chipText, auto && styles.chipTextOn]}>
            {auto ? 'Auto on' : 'Auto scan'}
          </Text>
        </Pressable>
      </View>

      {/*
        Next to the scan target rather than buried in settings, because
        it has to be visible while a stack is going through: it changes
        which printing every card lands on, and a forgotten toggle is a
        box filed wrong.
      */}
      <View style={styles.header}>
        <Pressable
          style={[styles.chip, promo && styles.chipOn]}
          onPress={() => {
            setPromo((on) => {
              setStatus(on
                ? 'Back to ordinary printings.'
                : 'Prerelease promos — the date-stamped ones.');
              return !on;
            });
          }}
        >
          <Text style={[styles.chipText, promo && styles.chipTextOn]}>
            {promo ? 'Prerelease promo' : 'Ordinary printing'}
          </Text>
        </Pressable>
        <Text style={styles.promoHint}>
          {promo
            ? 'Filing as the stamped printing.'
            : 'Turn on for date-stamped cards.'}
        </Text>
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
              // The percentage lives on the right now, so it is not said
              // twice in one row.
              ? (indexStage || 'Fetching the card index')
              : index.rows > 0
                ? 'Card index half-fetched — scanning needs all of it'
                // No PC is no longer a reason not to offer this. It comes
                // from the desktop when there is one and from Scryfall when
                // there is not, so the button works either way and only the
                // wait differs.
                : 'Get the card index once, then scanning works offline '
                  + 'forever.'}
          </Text>
          {/*
            An actual button, not a word at the end of a sentence. It read
            as a label, which is why it was not obvious it could be
            pressed — and once pressed it went quiet for a second or two,
            so it got pressed again.
          */}
          {pulling > 0 ? (
            <Text style={styles.queueAction}>{`${pulling}%`}</Text>
          ) : (
            <View style={styles.queueButton}>
              <Text style={styles.queueButtonText}>Get it</Text>
            </View>
          )}
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
          <FrameGuide busy={busy} />
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

      {/*
        The way in that does not use the camera. Under the controls
        rather than above the preview: scanning is the main event and
        this is the fallback for what scanning cannot do.
      */}
      <View style={styles.typedBox}>
        <TextInput
          style={styles.typedInput}
          value={typed}
          onChangeText={setTyped}
          placeholder="Or type a card name"
          placeholderTextColor="#6b7079"
          autoCorrect={false}
          autoCapitalize="words"
          returnKeyType="search"
        />
        {suggestions.map((row) => (
          <Pressable
            key={row.name}
            style={styles.typedHit}
            onPress={() => void chooseTyped(row.name)}
          >
            <Text style={styles.typedName}>{row.name}</Text>
            <Text style={styles.typedCount}>
              {row.printings === 1
                ? '1 printing'
                : `${row.printings} printings`}
            </Text>
          </Pressable>
        ))}
        {typed.trim().length >= 2 && !suggestions.length ? (
          <Text style={styles.typedNone}>
            Nothing in the index matches that.
          </Text>
        ) : null}
      </View>

      {result?.candidates?.length ? (
        <View
          style={styles.picker}
          onLayout={(e) => { pickerY.current = e.nativeEvent.layout.y; }}
        >
          {/*
            How many, before which one.

            One tap files one copy, which is right for a box of
            singles and wrong for the ten Islands a deck wants. The
            number applies to whichever printing is tapped next and
            then goes back to one.
          */}
          <View style={styles.qtyRow}>
            <Text style={styles.qtyLabel}>How many</Text>
            <Pressable
              style={styles.qtyBtn}
              onPress={() => setHowMany((n) => Math.max(1, n - 1))}
            >
              <Text style={styles.qtyBtnText}>-</Text>
            </Pressable>
            <Text style={styles.qtyValue}>{howMany}</Text>
            <Pressable
              style={styles.qtyBtn}
              onPress={() => setHowMany((n) => Math.min(99, n + 1))}
            >
              <Text style={styles.qtyBtnText}>+</Text>
            </Pressable>
            {howMany !== 1 ? (
              <Pressable style={styles.qtyBtn}
                         onPress={() => setHowMany(1)}>
                <Text style={styles.qtyBtnText}>reset</Text>
              </Pressable>
            ) : null}
          </View>

          {/*
            The set box, for the lists no amount of sorting rescues.

            Only when there are more printings than the picker can
            show: below that the list IS the answer and a filter over
            it is one more thing to read.
          */}
          {result.candidates.length > NAME_SHORTLIST ? (
            <>
              <TextInput
                style={styles.typedInput}
                value={setFilter}
                onChangeText={setSetFilter}
                placeholder="Filter by set — name or code"
                placeholderTextColor="#6b7079"
                autoCorrect={false}
                autoCapitalize="none"
              />
              <Text style={styles.pickerCount}>
                {pickerCount(result.candidates.length, matching.length,
                             NAME_SHORTLIST)}
              </Text>
            </>
          ) : null}

          {/*
            Newest first. A 29-printing list spanning 1993 to 2024 is
            only browsable in an order, and the card in somebody's hand
            is far more often recent than not. Sets the phone has no
            date for sink rather than jumping to the top.
          */}
          {matching
            .slice(0, NAME_SHORTLIST)
            .map((candidate, index) => (
            <Pressable
              key={`${candidate.printing_id}-${index}`}
              style={styles.candidate}
              onPress={() => {
                void file(candidate, defaultFinish(candidate, result),
                          howMany).catch(
                  (err) => setStatus(recordCrash(err, 'filing', false).message),
                );
              }}
            >
              <View style={styles.candidateRow}>
                {/*
                  The card itself.

                  Every row in this list is the same card name, so the
                  name is the one thing that cannot tell them apart.
                  For a basic land it is ALL they have in common --
                  nobody knows an Island as BLB #280, they know it as
                  the one with the lighthouse -- which makes the
                  picture the identifier and the text the footnote.

                  `small` rather than `art_crop`: 12 KB against 93,
                  and on a land the art is most of the card anyway.
                  Forty rows of the larger one is 3.7 MB to choose a
                  fifty-cent card.
                */}
                <Image
                  style={styles.candidateArt}
                  source={artSource(candidate.printing_id, 'small')}
                  resizeMode="cover"
                  accessibilityLabel={`${candidate.name}, `
                    + `${candidate.set_code.toUpperCase()} `
                    + `${candidate.collector_number}`}
                />
                {/*
                  The set symbol. Scryfall serves these as SVG and
                  hotlinks are the rule for their art, so it is fetched
                  rather than bundled — a thousand sets would be a
                  thousand files that go stale every time one ships.

                  Its own box whether or not a symbol loads, so the
                  names below stay in a straight line instead of
                  shuffling left as icons arrive.
                */}
                <View style={styles.symbol}>
                  {sets[candidate.set_code.toLowerCase()]?.iconUri ? (
                    <SetSymbol
                      uri={sets[candidate.set_code.toLowerCase()]?.iconUri
                        ?? ''}
                      // On a real card the symbol's colour IS its
                      // rarity, so the list shows what the card shows.
                      colour={rarityColour(candidate.rarity ?? '')}
                    />
                  ) : null}
                </View>
                <View style={styles.candidateText}>
                  <Text style={styles.candidateName}>{candidate.name}</Text>
                  <Text style={styles.candidateMeta}>
                    {sets[candidate.set_code.toLowerCase()]?.name
                      || candidate.set_code.toUpperCase()}
                    {' · #'}{candidate.collector_number}
                    {sets[candidate.set_code.toLowerCase()]?.year
                      ? ` · ${sets[candidate.set_code.toLowerCase()]?.year}`
                      : ''}
                  </Text>
                  {/*
                    The code as well as the name. It is what is printed
                    on the card, so it is the thing you can check.
                  */}
                  <Text style={styles.candidateCode}>
                    {candidate.set_code.toUpperCase()}
                    {candidate.rarity ? ` · ${candidate.rarity}` : ''}
                  </Text>
                </View>
              </View>
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
  queueButton: {
    backgroundColor: '#2f6f9f',
    borderRadius: 8,
    flexShrink: 0,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  queueButtonText: { color: '#fff', fontSize: 13, fontWeight: '700' },
  queueAction: {
    color: '#7db8e8',
    flexShrink: 0,
    fontSize: 13,
    fontWeight: '600',
  },
  alsoLabel: { color: '#8a8f9c', fontSize: 12 },
  alsoChips: { flexDirection: 'row', gap: 6 },
  candidateArt: {
    // A Magic card is 63x88. Anything else crops the art off.
    aspectRatio: 63 / 88,
    backgroundColor: '#11151d',
    borderRadius: 4,
    width: 54,
  },
  flashArt: {
    aspectRatio: 63 / 88,
    borderRadius: 6,
    marginVertical: 6,
    width: 104,
  },
  qtyRow: { alignItems: 'center', flexDirection: 'row', gap: 8,
            paddingBottom: 4 },
  qtyLabel: { color: '#8a8f9c', flex: 1, fontSize: 13 },
  qtyBtn: {
    borderColor: '#2f6f9f',
    borderRadius: 8,
    borderWidth: 1,
    minWidth: 38,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  qtyBtnText: { color: '#8ec5ff', fontSize: 15, textAlign: 'center' },
  qtyValue: {
    color: '#e4e6eb', fontSize: 17, fontWeight: '700',
    minWidth: 28, textAlign: 'center',
  },
  pickerCount: { color: '#8a8f9c', fontSize: 12, paddingBottom: 2 },
  flashCount: { color: '#68d391', fontSize: 26, fontWeight: '800' },
  typedBox: { gap: 6, marginTop: 10 },
  typedInput: {
    backgroundColor: '#141924',
    borderColor: '#242b3a',
    borderRadius: 10,
    borderWidth: 1,
    color: '#e4e6eb',
    fontSize: 15,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  typedHit: {
    alignItems: 'center',
    borderColor: '#242b3a',
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    paddingVertical: 9,
  },
  typedName: { color: '#e4e6eb', flexShrink: 1, fontSize: 14 },
  typedCount: { color: '#8a8f9c', fontSize: 12 },
  typedNone: { color: '#8a8f9c', fontSize: 13 },
  chip: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 7,
  },
  chipOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  chipText: { color: '#8a8f9c', fontSize: 13, fontWeight: '600' },
  promoHint: { color: '#6b7079', flexShrink: 1, fontSize: 12,
    textAlign: 'right' },
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
  // Bottom RIGHT, not bottom centre.
  //
  // Centred, it sat directly over the one part of the card the scanner
  // reads — the collector number and set code along the bottom left — so
  // framing the thing being matched meant hiding it behind the button.
  // A tester's photo showed a perfectly placed card with its footer
  // underneath the word "Capture".
  shutter: {
    position: 'absolute',
    bottom: 12,
    right: 12,
    backgroundColor: '#e53e3ecc',
    borderRadius: 999,
    paddingHorizontal: 20,
    paddingVertical: 11,
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
  candidateRow: { alignItems: 'center', flexDirection: 'row', gap: 12 },
  symbol: { alignItems: 'center', height: 26, justifyContent: 'center',
    width: 26 },
  candidateText: { flex: 1 },
  candidateCode: { color: '#6b7079', fontSize: 11, letterSpacing: 0.5,
    marginTop: 1 },
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
  flashPrinting: { alignItems: 'center', flexDirection: 'row', gap: 8,
    marginTop: 6 },
  flashMeta: { fontSize: 15, color: '#fff' },
});
