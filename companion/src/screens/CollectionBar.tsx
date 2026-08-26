/**
 * Picking a collection, and making one.
 *
 * Shared by the card list and the scanner because they ask the same question
 * — which shelf are we talking about — and because a "New collection" button
 * that exists on one screen and not the other is the kind of thing you go
 * looking for in the wrong place.
 *
 * Creating is inline rather than behind a dialog. The moment you want a new
 * collection is the moment you are holding the cards that go in it.
 */

import React, { useCallback, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { checkCollectionName } from '../lib/collections.ts';
import type { CollectionRow } from '../lib/store.ts';

interface Props {
  collections: CollectionRow[];
  /** The selected uid. Empty string means the "Everything" pseudo-entry. */
  selected: string;
  onSelect: (uid: string) => void;
  /** Resolves to the new collection's uid. */
  onCreate: (name: string) => Promise<string>;
  /** Whether to offer an "Everything" entry. The scanner must not: a card has
   *  to be filed somewhere in particular. */
  includeEverything?: boolean;
  /** Shown on each chip when the count is worth seeing. */
  showCounts?: boolean;
}

export function CollectionBar({
  collections,
  selected,
  onSelect,
  onCreate,
  includeEverything = false,
  showCounts = true,
}: Props) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);

  const create = useCallback(async () => {
    const verdict = checkCollectionName(name, collections);
    if (!verdict.ok) {
      setProblem(verdict.reason ?? 'That name will not do.');
      return;
    }
    setBusy(true);
    setProblem('');
    try {
      const uid = await onCreate(verdict.name);
      setName('');
      setAdding(false);
      // Selecting it immediately is the whole point: you made it in order to
      // put something in it.
      onSelect(uid);
    } catch (err) {
      setProblem((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [name, collections, onCreate, onSelect]);

  const entries = includeEverything
    ? [{ collection_uid: '', name: 'Everything', cards: 0 } as CollectionRow, ...collections]
    : collections;

  return (
    <View style={styles.wrap}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.chips}
      >
        {entries.map((entry) => {
          const active = selected === entry.collection_uid;
          return (
            <Pressable
              key={entry.collection_uid || 'all'}
              onPress={() => onSelect(entry.collection_uid)}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {entry.name}
                {showCounts && entry.collection_uid ? ` (${entry.cards})` : ''}
              </Text>
            </Pressable>
          );
        })}

        <Pressable
          onPress={() => {
            setProblem('');
            setAdding((open) => !open);
          }}
          style={[styles.chip, styles.chipNew]}
        >
          <Text style={styles.chipNewText}>
            {adding ? 'Cancel' : '+ New collection'}
          </Text>
        </Pressable>
      </ScrollView>

      {adding ? (
        <View style={styles.adder}>
          <TextInput
            style={styles.input}
            placeholder="Trade binder, Commander deck box…"
            placeholderTextColor="#8a8f9c"
            value={name}
            onChangeText={setName}
            autoCorrect={false}
            autoFocus
            onSubmitEditing={() => {
              void create();
            }}
            returnKeyType="done"
          />
          <Pressable
            style={styles.make}
            disabled={busy}
            onPress={() => {
              void create();
            }}
          >
            <Text style={styles.makeText}>{busy ? '…' : 'Make it'}</Text>
          </Pressable>
        </View>
      ) : null}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  chips: { gap: 8, paddingRight: 8 },
  chip: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  chipActive: { borderColor: '#38a169' },
  chipText: { color: '#8a8f9c', fontSize: 13 },
  chipTextActive: { color: '#38a169', fontWeight: '700' },
  chipNew: { borderColor: '#e53e3e', borderStyle: 'dashed' },
  chipNewText: { color: '#e53e3e', fontSize: 13, fontWeight: '600' },
  adder: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  input: {
    flex: 1,
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  make: {
    backgroundColor: '#e53e3e',
    borderRadius: 10,
    paddingHorizontal: 18,
    paddingVertical: 11,
  },
  makeText: { color: '#fff', fontWeight: '700' },
  problem: { color: '#e53e3e', fontSize: 12, lineHeight: 18 },
});
