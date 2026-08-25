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
import {
  DeckStore,
  addToDeck,
  deckSize,
  formatDecklist,
  parseDecklist,
  removeFromDeck,
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

  const load = useCallback(async () => setRows(await decks.list()), [decks]);
  useEffect(() => {
    void load();
  }, [load]);

  const create = useCallback(async () => {
    const chosen = name.trim() || 'Untitled deck';
    const deck: Deck = {
      deck_id: globalThis.crypto.randomUUID(),
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

      <View style={styles.row}>
        <TextInput
          style={[styles.list, styles.searchBox]}
          value={name}
          onChangeText={setName}
          placeholder="New deck name"
          placeholderTextColor="#8a8f9c"
        />
        <Pressable style={styles.secondary} onPress={create}>
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
  const [search, setSearch] = useState('');
  const [found, setFound] = useState<CatalogueCard[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    void (async () => {
      const found = await decks.get(deckId);
      if (!found) return;
      setDeck(found);
      setText(formatDecklist(found.decklist));
    })();
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
    if (deck) void recheck(deck.decklist);
  }, [deck, recheck]);

  const save = useCallback(async () => {
    const { cards, skipped } = parseDecklist(text);
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
      notes: deck?.notes ?? '',
      updated_at: new Date().toISOString(),
    };
    await decks.save(next);
    setDeck(next);
    await recheck(cards);
  }, [text, deck, deckId, decks, recheck]);

  /**
   * Search every card, not only the ones you own.
   *
   * This needs the PC — the catalogue is 34k cards — and says so when it
   * cannot reach one. Quietly searching the local collection instead would
   * answer "what do I own" while appearing to answer "what exists", so a
   * card you do not have would look like it does not exist.
   */
  const runSearch = useCallback(async () => {
    const term = search.trim();
    if (!term) return;
    setSearching(true);
    setProblem('');
    try {
      const reply = await state.searchCards({ name: term, limit: 25 });
      setFound(reply.cards);
      if (!reply.cards.length) setProblem(`No card matches “${term}”.`);
    } catch (err) {
      setFound([]);
      setProblem(
        `${(err as Error).message}. Searching every card needs your PC — ` +
        'the card database lives there. You can still type a name in by hand.',
      );
    } finally {
      setSearching(false);
    }
  }, [search, state]);

  /** Put a card in the deck whether or not you own it. */
  const add = useCallback(async (name: string) => {
    if (!deck) return;
    const next: Deck = {
      ...deck,
      decklist: addToDeck(deck.decklist, name),
      updated_at: new Date().toISOString(),
    };
    await decks.save(next);
    setDeck(next);
    setText(formatDecklist(next.decklist));
    await recheck(next.decklist);
  }, [deck, decks, recheck]);

  const drop = useCallback(async (name: string) => {
    if (!deck) return;
    const next: Deck = {
      ...deck,
      decklist: removeFromDeck(deck.decklist, name),
      updated_at: new Date().toISOString(),
    };
    await decks.save(next);
    setDeck(next);
    setText(formatDecklist(next.decklist));
    await recheck(next.decklist);
  }, [deck, decks, recheck]);

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

      <Text style={styles.section}>Add a card</Text>
      <View style={styles.row}>
        <TextInput
          style={[styles.list, styles.searchBox]}
          value={search}
          onChangeText={setSearch}
          placeholder="Search every card…"
          placeholderTextColor="#8a8f9c"
          autoCorrect={false}
          onSubmitEditing={runSearch}
          returnKeyType="search"
        />
        <Pressable style={styles.secondary} onPress={runSearch}>
          <Text style={styles.secondaryText}>{searching ? '…' : 'Find'}</Text>
        </Pressable>
      </View>
      {found.map((card) => (
        <Pressable
          key={card.scryfall_id}
          style={styles.result}
          onPress={() => add(card.name)}
        >
          <View style={styles.grow}>
            <Text style={styles.name}>{card.name}</Text>
            <Text style={styles.muted}>
              {card.type_line}
              {card.price_usd != null ? `  ·  $${card.price_usd.toFixed(2)}` : ''}
            </Text>
          </View>
          <Text style={styles.plus}>+</Text>
        </Pressable>
      ))}

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
  analysis: {
    color: '#8a8f9c',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
});
