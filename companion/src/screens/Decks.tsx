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
  deckSize,
  formatDecklist,
  parseDecklist,
  shortfall,
} from '../lib/decks.ts';
import type { Deck } from '../lib/decks.ts';

interface Props {
  state: AppState;
  decks: DeckStore;
  deckId: string;
  onBack: () => void;
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

      <Text style={styles.section}>Still needed</Text>
      {missing.length === 0 ? (
        <Text style={styles.good}>You own every card in this deck.</Text>
      ) : (
        missing.map((row) => (
          <View key={row.name} style={styles.row}>
            <Text style={styles.short}>{row.short}</Text>
            <Text style={styles.name}>{row.name}</Text>
            <Text style={styles.muted}>
              have {row.have} of {row.need}
            </Text>
          </View>
        ))
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
  name: { color: '#e4e6eb', flex: 1 },
  problem: { color: '#ecc94b', lineHeight: 20 },
  analysis: {
    color: '#8a8f9c',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
});
