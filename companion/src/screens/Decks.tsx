/**
 * Decks: build them anywhere, analyse them when the PC is awake.
 *
 * The split is deliberate. Editing a list and working out what you still need
 * both happen on the phone, because those are the things you do standing in a
 * shop. Analysis goes to the PC, because it needs the card catalogue and the
 * combo database, and there is no honest offline answer.
 *
 * A deck is shown two ways and the pictures come first. A decklist as text is
 * what you send someone; a wall of card faces is what you actually think
 * about when you are deciding what to cut, because you recognise a card by
 * its art long before you have read its name.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import { artSource } from '../lib/images.ts';
import { uuid } from '../lib/uuid.ts';
import { CardBrowser } from './CardBrowser.tsx';
import { reporting } from './report.ts';
import {
  DeckStore,
  deckColorIdentity,
  type DeckRecord,
  addToDeck,
  carryPrintings,
  copiesOf,
  costToFinish,
  deckSize,
  deckValue,
  deckWarnings,
  entryKey,
  formatDecklist,
  parseDecklist,
  printingLabel,
  pricesFromSlots,
  removeFromDeck,
  mergeCounts,
  shortfall,
} from '../lib/decks.ts';
import type { Deck, DeckEntry, ShortfallRow, SlotFacts } from '../lib/decks.ts';

interface Props {
  state: AppState;
  decks: DeckStore;
  deckId: string;
  onBack: () => void;
}

/** The decks you have, and a way to start another. */
export function DeckListScreen({
  decks,
  onOpen,
}: {
  decks: DeckStore;
  onOpen: (deckId: string) => void;
}) {
  const [rows, setRows] = useState<Deck[]>([]);
  const [name, setName] = useState('');
  const [problem, setProblem] = useState('');
  /**
   * The deck being renamed or deleted, and what it is being renamed to.
   *
   * Long-press rather than a row of buttons on every deck: the common action
   * is opening one, and two more targets beside it on a phone-width row is
   * how you delete a deck you meant to open.
   */
  const [acting, setActing] = useState<Deck | null>(null);
  const [renameTo, setRenameTo] = useState('');
  // Deleting a deck is irreversible, so it takes a second press rather than a
  // first one. The row stays on screen throughout — a confirm that replaces
  // what it is asking about leaves you agreeing to something you can no
  // longer see.
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const load = useCallback(async () => setRows(await decks.list()), [decks]);
  useEffect(() => {
    void load().catch(reporting('your decks', setProblem));
  }, [load]);

  const create = useCallback(async () => {
    const chosen = name.trim() || 'Untitled deck';
    const deck: Deck = {
      deck_id: uuid(),
      name: chosen,
      format: '',
      decklist: [],
      notes: '',
      updated_at: new Date().toISOString(),
    };
    await decks.save(deck);
    setName('');
    await load();
    onOpen(deck.deck_id);
  }, [name, decks, load, onOpen]);

  /** A new name for a deck. Blank is refused rather than silently ignored. */
  const rename = useCallback(
    async (deck: Deck) => {
      const chosen = renameTo.trim();
      if (!chosen) {
        setProblem('A deck needs a name.');
        return;
      }
      await decks.save({ ...deck, name: chosen,
                         updated_at: new Date().toISOString() });
      setActing(null);
      await load();
    },
    [renameTo, decks, load],
  );

  /**
   * Delete a deck. The cards are untouched.
   *
   * Worth saying on screen, because "delete" next to a list of cards reads as
   * if the cards go too — a deck is a list of names, and what you own is a
   * separate question the collection answers.
   */
  const remove = useCallback(
    async (deck: Deck) => {
      await decks.remove(deck.deck_id);
      setActing(null);
      setConfirmingDelete(false);
      await load();
    },
    [decks, load],
  );

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Decks</Text>
      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      <View style={styles.row}>
        <TextInput
          style={[styles.list, styles.searchBox]}
          value={name}
          onChangeText={setName}
          placeholder="New deck name"
          placeholderTextColor="#8a8f9c"
        />
        <Pressable
          style={styles.secondary}
          onPress={() => {
            setProblem('');
            void create().catch(reporting('making the deck', setProblem));
          }}
        >
          <Text style={styles.secondaryText}>Create</Text>
        </Pressable>
      </View>

      {rows.length === 0 ? (
        <Text style={styles.muted}>
          No decks yet. Make one above, then search for cards to put in it —
          you don’t have to own them.
        </Text>
      ) : (
        rows.map((deck) => (
          <View key={deck.deck_id}>
            <Pressable
              style={styles.result}
              onPress={() => onOpen(deck.deck_id)}
              onLongPress={() => {
                setActing(acting?.deck_id === deck.deck_id ? null : deck);
                setRenameTo(deck.name);
                setConfirmingDelete(false);
                setProblem('');
              }}
            >
              <View style={styles.grow}>
                <Text style={styles.name}>{deck.name}</Text>
                <Text style={styles.muted}>
                  {deckSize(deck.decklist)} cards · hold to rename or delete
                </Text>
              </View>
              <Text style={styles.plus}>›</Text>
            </Pressable>

            {acting?.deck_id === deck.deck_id ? (
              <View style={styles.deckActions}>
                <TextInput
                  style={[styles.list, styles.searchBox]}
                  value={renameTo}
                  onChangeText={setRenameTo}
                  placeholder="Deck name"
                  placeholderTextColor="#8a8f9c"
                  autoFocus
                />
                <View style={styles.row}>
                  <Pressable
                    style={[styles.secondary, styles.grow]}
                    onPress={() => {
                      setProblem('');
                      void rename(deck).catch(
                        reporting('renaming the deck', setProblem),
                      );
                    }}
                  >
                    <Text style={styles.secondaryText}>Rename</Text>
                  </Pressable>
                  <Pressable
                    style={[styles.secondary, styles.grow]}
                    onPress={() => {
                      setActing(null);
                      setConfirmingDelete(false);
                    }}
                  >
                    <Text style={styles.secondaryText}>Cancel</Text>
                  </Pressable>
                </View>
                <Pressable
                  style={[styles.secondary, styles.danger]}
                  onPress={() => {
                    setProblem('');
                    if (!confirmingDelete) {
                      setConfirmingDelete(true);
                      return;
                    }
                    void remove(deck).catch(
                      reporting('deleting the deck', setProblem),
                    );
                  }}
                >
                  <Text style={styles.dangerText}>
                    {confirmingDelete
                      ? `Really delete ${deck.name}? This cannot be undone`
                      : 'Delete this deck'}
                  </Text>
                </Pressable>
                <Text style={styles.muted}>
                  Deleting a deck never touches the cards. A deck is a list of
                  names; what you own is a separate thing.
                </Text>
              </View>
            ) : null}
          </View>
        ))
      )}
    </ScrollView>
  );
}

/**
 * A section heading that folds what is under it.
 *
 * A commander deck is a hundred cards on a phone, and everything below the
 * grid — what you still need, the PC actions, the analysis — sat behind a
 * very long scroll past something you had already looked at.
 *
 * The arrow and the count are both on the header so a folded section still
 * says how much is inside; a fold that hides the fact that anything is there
 * is just a missing feature.
 */
function Folding({
  title,
  count,
  open,
  onToggle,
  children,
}: {
  title: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <Pressable
        style={styles.foldHead}
        onPress={onToggle}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
      >
        <Text style={styles.foldArrow}>{open ? '▾' : '▸'}</Text>
        <Text style={[styles.section, styles.grow]}>{title}</Text>
        {count !== undefined ? (
          <Text style={styles.foldCount}>{count}</Text>
        ) : null}
      </Pressable>
      {open ? children : null}
    </>
  );
}

export function DeckScreen({ state, decks, deckId, onBack }: Props) {
  const [deck, setDeck] = useState<Deck | null>(null);
  const [text, setText] = useState('');
  const [missing, setMissing] = useState<ShortfallRow[]>([]);
  const [analysis, setAnalysis] = useState<string>('');
  const [thinking, setThinking] = useState(false);
  // What the PC said when it took the deck. Kept on screen rather than as a
  // flash: "saved" that vanishes is indistinguishable from nothing happening.
  const [savedToPc, setSavedToPc] = useState('');
  const [problem, setProblem] = useState('');
  const [browsing, setBrowsing] = useState(false);
  // Which half the grid and the +/- act on. The text box always shows
  // both, because that is what a decklist IS.
  const [zone, setZone] = useState<'main' | 'side'>('main');
  /**
   * Pictures or words.
   *
   * Pictures by default: a decklist as text is the form you SEND, and a wall
   * of card faces is the form you think in. Text is one tap away and is still
   * the only way to paste a list in or out, so nothing is lost by not leading
   * with it.
   */
  const [view, setView] = useState<'visual' | 'text'>('visual');
  // Bumped when the page nears its bottom. The browser owns no scroller of
  // its own — two nested ones is why the grid could not scroll at all — so
  // the page watches and nudges it to fetch the next sixty.
  const [nearEnd, setNearEnd] = useState(0);
  /**
   * What each slot in this deck looks like and costs.
   *
   * Keyed by slot, not by card name, because two slots of the same card at
   * two printings are two different pictures and two different prices — which
   * is the entire reason a slot can name a printing.
   */
  const [slots, setSlots] = useState<Record<string, SlotFacts>>({});
  /**
   * How this deck has done, and whether a result is mid-write.
   *
   * On the phone rather than only on the PC because this is where you are
   * when the game ends — standing at a table with the deck in your hand,
   * which is the moment the result is known and the only moment anyone will
   * reliably record it.
   */
  const [record, setRecord] = useState<DeckRecord | null>(null);
  const [logging, setLogging] = useState('');
  /**
   * Which sections are folded away.
   *
   * A commander deck is a hundred cards on a phone screen, and everything
   * underneath it — what you still need, the PC actions, the analysis — was
   * a very long scroll past a grid you had already looked at. Folded state
   * starts open for the deck and closed for nothing, so the screen behaves
   * as it did until somebody folds something.
   */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const fold = useCallback(
    (key: string) => setFolded((f) => ({ ...f, [key]: !f[key] })),
    [],
  );

  useEffect(() => {
    void (async () => {
      const found = await decks.get(deckId);
      if (!found) return;
      setDeck(found);
      setText(formatDecklist(found.decklist, found.sideboard));
      setRecord(await decks.recordFor(deckId));
    })().catch(reporting('opening the deck', setProblem));
  }, [decks, deckId]);

  /**
   * Log a result.
   *
   * Written HERE first and queued for the desktop second, so the record is
   * right with no signal at all — which is the normal case in a game shop.
   * The event waits in the outbox until something is reachable.
   */
  const logGame = useCallback(
    async (result: 'win' | 'loss' | 'draw') => {
      setLogging(result);
      setProblem('');
      try {
        await state.logGame(deckId, result);
        setRecord(await decks.recordFor(deckId));
      } catch (err) {
        setProblem(
          `${(err as Error).message}. The result is only saved on this phone ` +
            'until your PC is reachable.',
        );
      } finally {
        setLogging('');
      }
    },
    [state, decks, deckId],
  );

  /**
   * The colours this deck may play.
   *
   * Derived from the commander through the slot facts, which are cached — so
   * the lock keeps working after the desktop goes away, which is exactly
   * when someone is standing at a table adding cards.
   */
  const identity = useMemo(
    () => deckColorIdentity(deck?.commander ?? [], slots, deck?.format ?? ''),
    [deck, slots],
  );

  /** What you still need, from the phone's own mirror. Works with no signal. */
  const recheck = useCallback(
    async (decklist: DeckEntry[]) => {
      const owned = await state.cards();
      setMissing(shortfall(decklist, owned));
    },
    [state],
  );

  useEffect(() => {
    if (deck) {
      void recheck(mergeCounts(deck.decklist, deck.sideboard)).catch(
        reporting('checking what you own', setProblem),
      );
    }
  }, [deck, recheck]);

  /**
   * Every slot in the deck, both halves, in one list.
   *
   * Memoised because the effect below keys off it and a fresh array every
   * render would ask the desktop about the whole deck on every render.
   */
  const allSlots = useMemo(
    () => (deck ? mergeCounts(deck.decklist, deck.sideboard) : []),
    [deck],
  );

  /**
   * Which cards these slots ARE, ignoring how many of each.
   *
   * The effect below depends on this rather than on the deck, because a
   * picture and a price are facts about a slot and not about its quantity.
   * Keyed on the deck itself, tapping a tile to add a fourth copy would send
   * the entire hundred-card list to the desktop again — once per tap, over a
   * tailnet, for an answer that cannot have changed.
   */
  const slotSignature = useMemo(
    () => allSlots.map((entry) => entryKey(entry)).join('\n'),
    [allSlots],
  );

  /**
   * The art and the prices.
   *
   * Never allowed to fail loudly: this is decoration and money, and neither
   * is worth taking the deck away over. What the phone knows on its own —
   * every card you own — arrives with no network at all.
   */
  useEffect(() => {
    if (!allSlots.length) {
      setSlots({});
      return;
    }
    void state
      .deckSlots(allSlots)
      .then(setSlots)
      // Keeps whatever was already resolved rather than blanking the grid: a
      // desktop that went to sleep should not take the pictures with it.
      .catch(() => {});
    // allSlots is deliberately absent: it changes identity on every edit,
    // and what this depends on is WHICH cards, not how many.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotSignature, state]);

  const save = useCallback(async () => {
    const { cards, sideboard, skipped } = parseDecklist(text);
    setProblem(
      skipped.length
        ? `Couldn't read ${skipped.length} line${skipped.length === 1 ? '' : 's'}: ` +
          `${skipped.slice(0, 3).join(', ')}`
        : '',
    );
    const next: Deck = {
      deck_id: deckId,
      name: deck?.name ?? 'Untitled deck',
      format: deck?.format ?? '',
      // The text box carries a set and a number; it cannot carry a Scryfall
      // id. Without putting them back, one hand-edit would quietly demote
      // every exact slot in the deck to set-and-number only, the shortfall
      // would change, and nothing on screen would say why.
      decklist: carryPrintings(cards, deck?.decklist),
      sideboard: carryPrintings(sideboard, deck?.sideboard),
      notes: deck?.notes ?? '',
      updated_at: new Date().toISOString(),
    };
    await decks.save(next);
    setDeck(next);
    // The board counts toward what you still need — those cards get
    // bought and carried like any other.
    await recheck(mergeCounts(next.decklist, next.sideboard));
  }, [text, deck, deckId, decks, recheck]);

  /**
   * Add or remove, in whichever half is selected.
   *
   * One function rather than two pairs, because the only difference between
   * putting a card in the deck and putting it in the board is which list it
   * lands in — and writing that twice is how the two drift apart.
   *
   * Takes a card rather than a name so a printing can come with it. A bare
   * name still means "any printing", which is what it always meant.
   */
  const change = useCallback(
    async (card: string | DeckEntry, delta: 1 | -1) => {
      if (!deck) return;
      const edit = delta > 0 ? addToDeck : removeFromDeck;
      const next: Deck =
        zone === 'side'
          ? {
              ...deck,
              sideboard: edit(deck.sideboard ?? [], card),
              updated_at: new Date().toISOString(),
            }
          : {
              ...deck,
              decklist: edit(deck.decklist, card),
              updated_at: new Date().toISOString(),
            };
      await decks.save(next);
      setDeck(next);
      setText(formatDecklist(next.decklist, next.sideboard));
      await recheck(mergeCounts(next.decklist, next.sideboard));
    },
    [deck, decks, recheck, zone],
  );

  const add = useCallback(
    (card: string | DeckEntry) => change(card, 1),
    [change],
  );
  const drop = useCallback(
    (card: string | DeckEntry) => change(card, -1),
    [change],
  );

  /**
   * Hand the deck to the PC.
   *
   * The other half of copying one down, and the half that was missing. A deck
   * built standing in a shop lived here and nowhere else — the one place it
   * is least useful afterwards. On the PC it gets a version, static analysis,
   * and everything else the desktop keeps.
   *
   * Same id, so saving twice is a new VERSION rather than a second deck with
   * the same name.
   */
  const saveToPc = useCallback(async () => {
    if (!deck) return;
    setSavedToPc('');
    setProblem('');
    try {
      const said = await state.saveDeckToDesktop(
        deck.deck_id,
        deck.name,
        formatDecklist(deck.decklist, deck.sideboard),
        deck.format,
      );
      const broke = said.combos_broken?.length ?? 0;
      setSavedToPc(
        `Saved to your PC${
          said.version_number ? ` as version ${said.version_number}` : ''
        }.` + (broke ? ` ${broke} combo line${broke === 1 ? '' : 's'} broke.` : ''),
      );
    } catch (err) {
      setProblem(
        `${(err as Error).message}. Saving puts the deck on your PC, so it ` +
          'only works when your PC is reachable.',
      );
    }
  }, [deck, state]);

  const analyse = useCallback(async () => {
    if (!deck) return;
    setThinking(true);
    setAnalysis('');
    try {
      const result = await state.analyze(
        formatDecklist(deck.decklist, deck.sideboard),
        deck.name,
      );
      setAnalysis(JSON.stringify(result, null, 2));
    } catch (err) {
      // Said plainly rather than dressed up: the analysis genuinely cannot be
      // done here, and pretending otherwise would be worse than saying so.
      setAnalysis('');
      setProblem(
        `${(err as Error).message}. Analysis runs on your PC — it needs the ` +
          'card database, so it only works when your PC is reachable.',
      );
    } finally {
      setThinking(false);
    }
  }, [deck, state]);

  /** The half you are looking at, which is the half the tabs and +/- act on. */
  const showing: DeckEntry[] = (zone === 'side' ? deck?.sideboard : deck?.decklist) ?? [];

  /**
   * What the deck is worth, and what finishing it would cost.
   *
   * Worth having only now that slots can name printings. Before, every copy
   * of a card was priced at one representative printing, so the deck holding
   * the $50 full-art and the deck holding the $16 common were the same
   * number — an estimate wearing a dollar sign.
   */
  const money = useMemo(() => {
    if (!deck) return null;
    const prices = pricesFromSlots(allSlots, slots);
    return {
      worth: deckValue(allSlots, prices),
      toFinish: costToFinish(missing, prices),
    };
  }, [deck, allSlots, slots, missing]);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      scrollEventThrottle={200}
      onScroll={(event) => {
        const { layoutMeasurement, contentOffset, contentSize } = event.nativeEvent;
        // Within a screen and a half of the bottom. Far enough ahead that the
        // next page has usually arrived before you reach it, which is what
        // makes it feel like there was never a page at all.
        const remaining =
          contentSize.height - (contentOffset.y + layoutMeasurement.height);
        if (remaining < layoutMeasurement.height * 1.5) {
          setNearEnd((n) => n + 1);
        }
      }}
    >
      <Pressable onPress={onBack}>
        <Text style={styles.back}>‹ Decks</Text>
      </Pressable>

      <Text style={styles.title}>{deck?.name ?? 'Deck'}</Text>
      <Text style={styles.muted}>
        {deck ? `${deckSize(deck.decklist)} cards` : ''}
      </Text>
      {money && (money.worth.usd > 0 || money.worth.unpriced > 0) ? (
        <Text style={styles.muted}>
          Worth about ${money.worth.usd.toFixed(2)}
          {money.worth.unpriced
            ? ` — ${money.worth.unpriced} card${
                money.worth.unpriced === 1 ? '' : 's'
              } couldn’t be priced`
            : ''}
          {money.toFinish.usd > 0
            ? `  ·  $${money.toFinish.usd.toFixed(2)} still to buy`
            : ''}
        </Text>
      ) : null}

      {/*
        The record, and the three buttons that change it. Directly under the
        deck's name because that is the question someone has about a deck
        they are holding: has it been any good.
      */}
      <View style={styles.recordRow}>
        <Text style={styles.recordText}>
          {record && record.games
            ? `${record.record}` +
              (record.win_rate !== null
                ? `  ·  ${Math.round(record.win_rate * 100)}%`
                : '')
            : 'no games yet'}
        </Text>
        {(['win', 'loss', 'draw'] as const).map((result) => (
          <Pressable
            key={result}
            style={[styles.logBtn, logging === result && styles.logBtnBusy]}
            disabled={logging !== ''}
            onPress={() => void logGame(result)}
          >
            <Text style={styles.logBtnText}>
              {result === 'win' ? 'Win' : result === 'loss' ? 'Loss' : 'Draw'}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* Over the line, not blocked at it. Half of deckbuilding is holding
          a pile that is not legal yet. */}
      {deck
        ? deckWarnings(deck.decklist, deck.sideboard, deck.format).map((w) => (
            <Text key={w.text} style={styles.overLimit}>
              {w.text}
            </Text>
          ))
        : null}

      {/*
        Which half you are editing. Not a mode buried in a menu: adding to
        the wrong one is silent, and you would find out at the table.
      */}
      <View style={styles.zoneRow}>
        <Pressable
          style={[styles.zone, zone === 'main' && styles.zoneOn]}
          onPress={() => setZone('main')}
        >
          <Text style={[styles.zoneText, zone === 'main' && styles.zoneTextOn]}>
            Deck ({deckSize(deck?.decklist ?? [])})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.zone, zone === 'side' && styles.zoneOn]}
          onPress={() => setZone('side')}
        >
          <Text style={[styles.zoneText, zone === 'side' && styles.zoneTextOn]}>
            Sideboard ({deckSize(deck?.sideboard ?? [])})
          </Text>
        </Pressable>
      </View>

      {/* Pictures or words. Same control as the one directly above, because
          it does the same kind of thing — switches what you are looking at
          rather than changing anything. */}
      <View style={styles.zoneRow}>
        <Pressable
          style={[styles.zone, view === 'visual' && styles.zoneOn]}
          onPress={() => setView('visual')}
        >
          <Text style={[styles.zoneText, view === 'visual' && styles.zoneTextOn]}>
            Visual
          </Text>
        </Pressable>
        <Pressable
          style={[styles.zone, view === 'text' && styles.zoneOn]}
          onPress={() => setView('text')}
        >
          <Text style={[styles.zoneText, view === 'text' && styles.zoneTextOn]}>
            Written
          </Text>
        </Pressable>
      </View>

      {view === 'visual' ? (
        <>
          {showing.length === 0 ? (
            <Text style={styles.muted}>
              {zone === 'side'
                ? 'Nothing in the sideboard yet.'
                : 'No cards yet. Browse below, or switch to Written and paste a list.'}
            </Text>
          ) : (
            <Text style={styles.muted}>
              Tap a card for one more, hold it for one fewer.
            </Text>
          )}
          {/*
            Laid out, NOT scrolled. There is one scroller on this screen and
            it belongs to the page — a nested vertical ScrollView never gets
            the gesture, and every attempt at one on this screen produced a
            grid that showed two rows and refused to move.
          */}
          <View style={styles.grid}>
            {showing.map((entry) => {
              const facts = slots[entryKey(entry)];
              const label = printingLabel(entry) || printingLabel(facts ?? {});
              return (
                <Pressable
                  key={entryKey(entry)}
                  style={styles.tile}
                  onPress={() => void add(entry)}
                  onLongPress={() => void drop(entry)}
                >
                  <Image
                    source={artSource(facts?.printing_id ?? '', 'small')}
                    style={styles.tileArt}
                    resizeMode="contain"
                  />
                  <View style={styles.badge}>
                    <Text style={styles.badgeText}>{entry.qty}</Text>
                  </View>
                  <Text style={styles.tileName} numberOfLines={2}>
                    {entry.name}
                  </Text>
                  {/*
                    Which card this actually is.

                    A slot that named a printing shows it, and the picture
                    above is that exact card. A slot that did not says so
                    rather than letting the picture imply a choice nobody
                    made — it is the right card, shown in one of its
                    printings, and pretending otherwise is what the whole
                    printing change exists to stop.
                  */}
                  <Text
                    style={
                      entry.printing_id || entry.set_code
                        ? styles.exactPrinting
                        : styles.anyPrinting
                    }
                    numberOfLines={1}
                  >
                    {entry.printing_id || entry.set_code
                      ? label || 'this printing'
                      : `any printing${label ? ` · showing ${label}` : ''}`}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </>
      ) : (
        <>
          <TextInput
            style={styles.list}
            value={text}
            onChangeText={setText}
            multiline
            autoCorrect={false}
            autoCapitalize="none"
            placeholder={'4 Lightning Bolt\n1 Sol Ring (CMM) 410'}
            placeholderTextColor="#8a8f9c"
          />
          <Text style={styles.muted}>
            A line with a set and number — 1 Sol Ring (CMM) 410 — means that
            exact printing. A bare name means any of them.
          </Text>
          <Pressable style={styles.primary} onPress={save}>
            <Text style={styles.primaryText}>Save deck</Text>
          </Pressable>
        </>
      )}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      <View style={styles.row}>
        <Text style={[styles.section, styles.grow]}>
          {zone === 'side' ? 'Add to the sideboard' : 'Add to the deck'}
        </Text>
        <Pressable
          style={styles.secondary}
          onPress={() => setBrowsing((open) => !open)}
        >
          <Text style={styles.secondaryText}>
            {browsing ? 'Close browser' : 'Browse cards'}
          </Text>
        </Pressable>
      </View>

      {/*
        A grid with filters, not a text box. Deckbuilding is a browsing job:
        you know you want a two-mana red removal spell and not which one, and
        a search that needs the answer first is the wrong shape for the
        question.
      */}
      {browsing && identity ? (
        <Text style={styles.muted}>
          {identity.size
            ? `Locked to ${[...identity].join('')} — your commander's colours.`
            : 'Locked to colourless — your commander has no colours.'}
        </Text>
      ) : null}
      {browsing ? (
        <View style={styles.browser}>
          <CardBrowser
            state={state}
            identity={identity}
            onPick={(card, printing) =>
              add(
                printing
                  ? {
                      name: card.name,
                      qty: 1,
                      printing_id: printing.printing_id,
                      set_code: printing.set_code,
                      collector_number: printing.collector_number,
                    }
                  : card.name,
              )
            }
            onUnpick={(card) => drop(card.name)}
            previewOnTap
            nearEnd={nearEnd}
            onClose={() => setBrowsing(false)}
            countFor={(card) => copiesOf(showing, card.name)}
            countForPrinting={(printingId) =>
              showing
                .filter((e) => e.printing_id === printingId)
                .reduce((sum, e) => sum + e.qty, 0)
            }
          />
        </View>
      ) : null}

      <Folding
        title="On your PC"
        open={!folded.pc}
        onToggle={() => fold('pc')}
      >
        <Pressable style={styles.secondary} onPress={() => void saveToPc()}>
          <Text style={styles.secondaryText}>Save this deck to my PC</Text>
        </Pressable>
        <Text style={styles.muted}>
          Puts it where the versions and the deep analysis live. Saving again
          makes a new version rather than a second deck.
        </Text>
        {savedToPc ? <Text style={styles.good}>{savedToPc}</Text> : null}
      </Folding>

      <Folding
        title="Analysis"
        open={!folded.analysis}
        onToggle={() => fold('analysis')}
      >
        <Pressable style={styles.secondary} onPress={analyse} disabled={thinking}>
          <Text style={styles.secondaryText}>
            {thinking ? 'Your PC is thinking…' : 'Analyse on my PC'}
          </Text>
        </Pressable>
        {thinking ? <ActivityIndicator color="#48bb78" /> : null}
        {analysis ? <Text style={styles.analysis}>{analysis}</Text> : null}
      </Folding>

      {/*
        Last, because it is the part you act on AFTER the deck is settled —
        and because it is a list of cards you do NOT have, which is the wrong
        thing to meet first when you open a deck you are proud of.
      */}
      <Folding
        title="Still needed — on your wishlist"
        count={missing.length || undefined}
        open={!folded.missing}
        onToggle={() => fold('missing')}
      >
        {missing.length === 0 ? (
          <Text style={styles.good}>You own every card in this deck.</Text>
        ) : (
          <>
            <Text style={styles.muted}>
              These aren’t counted as owned. They’re what this deck would cost
              to finish.
            </Text>
            {missing.map((row) => (
              <Pressable key={entryKey(row)} style={styles.row}
                         onLongPress={() => void drop(row)}>
                <Text style={styles.short}>{row.short}</Text>
                <View style={styles.grow}>
                  <Text style={styles.name}>{row.name}</Text>
                  {printingLabel(row) ? (
                    <Text style={styles.exactPrinting}>{printingLabel(row)}</Text>
                  ) : null}
                </View>
                <Text style={styles.muted}>
                  have {row.have} of {row.need}
                </Text>
              </Pressable>
            ))}
          </>
        )}
      </Folding>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 16, gap: 10, paddingBottom: 60 },
  back: { color: '#8a8f9c', fontSize: 16 },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13 },
  list: {
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    padding: 12,
    minHeight: 160,
    textAlignVertical: 'top',
    fontSize: 15,
  },
  primary: {
    backgroundColor: '#e53e3e',
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  primaryText: { color: '#fff', fontWeight: '600' },
  secondary: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  secondaryText: { color: '#e4e6eb' },
  section: {
    color: '#8a8f9c',
    textTransform: 'uppercase',
    fontSize: 12,
    letterSpacing: 1,
    marginTop: 14,
  },
  good: { color: '#48bb78' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 6 },
  short: { color: '#e53e3e', minWidth: 24, fontWeight: '700' },
  searchBox: { minHeight: 0, flex: 1, paddingVertical: 10 },
  result: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#2d3142',
  },
  grow: { flex: 1 },
  plus: { color: '#48bb78', fontSize: 22, fontWeight: '700', paddingLeft: 8 },
  name: { color: '#e4e6eb', flex: 1 },
  problem: { color: '#ecc94b', lineHeight: 20 },
  browser: { marginBottom: 8 },
  foldHead: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: 8,
    marginTop: 10,
    paddingVertical: 4,
  },
  foldArrow: { color: '#8a8f9c', fontSize: 13, width: 12 },
  foldCount: {
    backgroundColor: '#2d3142',
    borderRadius: 9,
    color: '#e4e6eb',
    fontSize: 12,
    overflow: 'hidden',
    paddingHorizontal: 7,
    paddingVertical: 1,
  },
  recordRow: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 8,
  },
  recordText: {
    color: '#68d391',
    fontSize: 14,
    fontVariant: ['tabular-nums'],
    marginRight: 4,
  },
  logBtn: {
    borderColor: '#4a5568',
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  // Pressed state matters more here than anywhere else in the app: this is
  // used one-handed, standing up, and a button that gives no sign it was hit
  // gets hit twice.
  logBtnBusy: { borderColor: '#68d391', opacity: 0.6 },
  logBtnText: { color: '#e4e6eb', fontSize: 14 },
  overLimit: { color: '#ecc94b', fontSize: 12, lineHeight: 18 },
  deckActions: {
    gap: 8,
    paddingVertical: 10,
    paddingHorizontal: 4,
    borderBottomWidth: 1,
    borderBottomColor: '#2d3142',
  },
  danger: { borderColor: '#e53e3e' },
  dangerText: { color: '#e53e3e', fontWeight: '600', textAlign: 'center' },
  zoneRow: { flexDirection: 'row', gap: 8 },
  zone: {
    flex: 1,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    paddingVertical: 10,
    alignItems: 'center',
  },
  zoneOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  zoneText: { color: '#c9ced9', fontSize: 14 },
  zoneTextOn: { color: '#ffffff', fontWeight: '700' },
  // The same grid the browser uses, deliberately: the deck and the search
  // results are the same kind of thing looked at from two directions, and two
  // slightly different card grids on one screen reads as a bug.
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tile: { width: '31%' },
  tileArt: {
    width: '100%',
    aspectRatio: 745 / 1040,
    borderRadius: 6,
    backgroundColor: '#1a1d27',
  },
  tileName: { color: '#c9ced9', fontSize: 11, marginTop: 3 },
  exactPrinting: { color: '#68d391', fontSize: 10 },
  anyPrinting: { color: '#6b7280', fontSize: 10 },
  badge: {
    position: 'absolute',
    top: 4,
    right: 4,
    minWidth: 22,
    borderRadius: 11,
    backgroundColor: '#38a169',
    alignItems: 'center',
    paddingVertical: 2,
    paddingHorizontal: 6,
  },
  badgeText: { color: '#fff', fontSize: 12, fontWeight: '700' },
  analysis: {
    color: '#8a8f9c',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
});
