/**
 * Decks: build them anywhere, analyse them when the PC is awake.
 *
 * The split is deliberate. Editing a list and working out what you still need
 * both happen on the phone, because those are the things you do standing in a
 * shop. Analysis goes to the PC, because it needs the card catalogue and the
 * combo database, and there is no honest offline answer.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import { uuid } from '../lib/uuid.ts';
import { CardBrowser } from './CardBrowser.tsx';
import { reporting } from './report.ts';
import {
  DeckStore,
  addToDeck,
  deckSize,
  deckWarnings,
  formatDecklist,
  parseDecklist,
  removeFromDeck,
  mergeCounts,
  shortfall,
} from '../lib/decks.ts';
import type { Deck } from '../lib/decks.ts';
import type { CatalogueCard } from '../lib/protocol.ts';

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
      decklist: {},
      notes: '',
      updated_at: new Date().toISOString(),
    };
    await decks.save(deck);
    setName('');
    await load();
    onOpen(deck.deck_id);
  }, [name, decks, load, onOpen]);

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
          <Pressable
            key={deck.deck_id}
            style={styles.result}
            onPress={() => onOpen(deck.deck_id)}
          >
            <View style={styles.grow}>
              <Text style={styles.name}>{deck.name}</Text>
              <Text style={styles.muted}>{deckSize(deck.decklist)} cards</Text>
            </View>
            <Text style={styles.plus}>›</Text>
          </Pressable>
        ))
      )}
    </ScrollView>
  );
}

export function DeckScreen({ state, decks, deckId, onBack }: Props) {
  const [deck, setDeck] = useState<Deck | null>(null);
  const [text, setText] = useState('');
  const [missing, setMissing] = useState<
    Array<{ name: string; need: number; have: number; short: number }>
  >([]);
  const [analysis, setAnalysis] = useState<string>('');
  const [thinking, setThinking] = useState(false);
  const [problem, setProblem] = useState('');
  const [browsing, setBrowsing] = useState(false);
  // Which half the grid and the +/- act on. The text box always shows
  // both, because that is what a decklist IS.
  const [zone, setZone] = useState<'main' | 'side'>('main');

  useEffect(() => {
    void (async () => {
      const found = await decks.get(deckId);
      if (!found) return;
      setDeck(found);
      setText(formatDecklist(found.decklist, found.sideboard));
    })().catch(reporting('opening the deck', setProblem));
  }, [decks, deckId]);

  /** What you still need, from the phone's own mirror. Works with no signal. */
  const recheck = useCallback(
    async (decklist: Record<string, number>) => {
      const owned = await state.cards();
      setMissing(shortfall(decklist, owned));
    },
    [state],
  );

  useEffect(() => {
    if (deck) void recheck(deck.decklist).catch(reporting('checking what you own', setProblem));
  }, [deck, recheck]);

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
      decklist: cards,
      sideboard,
      notes: deck?.notes ?? '',
      updated_at: new Date().toISOString(),
    };
    await decks.save(next);
    setDeck(next);
    // The board counts toward what you still need — those cards get
    // bought and carried like any other.
    await recheck(mergeCounts(cards, sideboard));
  }, [text, deck, deckId, decks, recheck]);

  /**
   * Add or remove, in whichever half is selected.
   *
   * One function rather than two pairs, because the only difference between
   * putting a card in the deck and putting it in the board is which map it
   * lands in — and writing that twice is how the two drift apart.
   */
  const change = useCallback(
    async (name: string, delta: 1 | -1) => {
      if (!deck) return;
      const edit = delta > 0 ? addToDeck : removeFromDeck;
      const next: Deck =
        zone === 'side'
          ? {
              ...deck,
              sideboard: edit(deck.sideboard ?? {}, name),
              updated_at: new Date().toISOString(),
            }
          : {
              ...deck,
              decklist: edit(deck.decklist, name),
              updated_at: new Date().toISOString(),
            };
      await decks.save(next);
      setDeck(next);
      setText(formatDecklist(next.decklist, next.sideboard));
      await recheck(mergeCounts(next.decklist, next.sideboard));
    },
    [deck, decks, recheck, zone],
  );

  const add = useCallback((name: string) => change(name, 1), [change]);
  const drop = useCallback((name: string) => change(name, -1), [change]);

  const analyse = useCallback(async () => {
    if (!deck) return;
    setThinking(true);
    setAnalysis('');
    try {
      const result = await state.analyze(formatDecklist(deck.decklist), deck.name);
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

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Pressable onPress={onBack}>
        <Text style={styles.back}>‹ Decks</Text>
      </Pressable>

      <Text style={styles.title}>{deck?.name ?? 'Deck'}</Text>
      <Text style={styles.muted}>
        {deck ? `${deckSize(deck.decklist)} cards` : ''}
      </Text>

      <TextInput
        style={styles.list}
        value={text}
        onChangeText={setText}
        multiline
        autoCorrect={false}
        autoCapitalize="none"
        placeholder={'4 Lightning Bolt\n1 Sol Ring'}
        placeholderTextColor="#8a8f9c"
      />
      <Pressable style={styles.primary} onPress={save}>
        <Text style={styles.primaryText}>Save deck</Text>
      </Pressable>

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {/*
        Which half you are editing. Not a mode buried in a menu: adding to
        the wrong one is silent, and you would find out at the table.
      */}
      {/* Over the line, not blocked at it. Half of deckbuilding is holding
          a pile that is not legal yet. */}
      {deck
        ? deckWarnings(deck.decklist, deck.sideboard, deck.format).map((w) => (
            <Text key={w.text} style={styles.overLimit}>
              {w.text}
            </Text>
          ))
        : null}

      <View style={styles.zoneRow}>
        <Pressable
          style={[styles.zone, zone === 'main' && styles.zoneOn]}
          onPress={() => setZone('main')}
        >
          <Text style={[styles.zoneText, zone === 'main' && styles.zoneTextOn]}>
            Deck ({deckSize(deck?.decklist ?? {})})
          </Text>
        </Pressable>
        <Pressable
          style={[styles.zone, zone === 'side' && styles.zoneOn]}
          onPress={() => setZone('side')}
        >
          <Text style={[styles.zoneText, zone === 'side' && styles.zoneTextOn]}>
            Sideboard ({deckSize(deck?.sideboard ?? {})})
          </Text>
        </Pressable>
      </View>

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
      {browsing ? (
        <View style={styles.browser}>
          <CardBrowser
            state={state}
            onPick={(card) => add(card.name)}
            onClose={() => setBrowsing(false)}
            countFor={(card) =>
              (zone === 'side' ? deck?.sideboard : deck?.decklist)?.[
                card.name
              ] ?? 0
            }
          />
        </View>
      ) : null}

      <Text style={styles.section}>Still needed — on your wishlist</Text>
      {missing.length === 0 ? (
        <Text style={styles.good}>You own every card in this deck.</Text>
      ) : (
        <>
          <Text style={styles.muted}>
            These aren’t counted as owned. They’re what this deck would cost
            to finish.
          </Text>
          {missing.map((row) => (
            <Pressable key={row.name} style={styles.row}
                       onLongPress={() => drop(row.name)}>
              <Text style={styles.short}>{row.short}</Text>
              <Text style={styles.name}>{row.name}</Text>
              <Text style={styles.muted}>
                have {row.have} of {row.need}
              </Text>
            </Pressable>
          ))}
        </>
      )}

      <Text style={styles.section}>Analysis</Text>
      <Pressable style={styles.secondary} onPress={analyse} disabled={thinking}>
        <Text style={styles.secondaryText}>
          {thinking ? 'Your PC is thinking…' : 'Analyse on my PC'}
        </Text>
      </Pressable>
      {thinking ? <ActivityIndicator color="#48bb78" /> : null}
      {analysis ? <Text style={styles.analysis}>{analysis}</Text> : null}
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
  browser: { height: 560 },
  overLimit: { color: '#ecc94b', fontSize: 12, lineHeight: 18 },
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
  analysis: {
    color: '#8a8f9c',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
});
