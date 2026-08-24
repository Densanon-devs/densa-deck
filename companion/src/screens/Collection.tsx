/**
 * Browsing what you own.
 *
 * Reads come from the phone's own database, never the network, so this screen
 * works identically standing in a shop with no signal. Everything it decides
 * lives in `app-state.ts` and is covered by the Node suite; what is left here
 * is a description of the screen.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import type { CollectionRow, StackRow } from '../lib/store.ts';

interface Props {
  state: AppState;
  onOpenCard?: (stackKey: string) => void;
}

export function CollectionScreen({ state, onOpenCard }: Props) {
  const [rows, setRows] = useState<StackRow[]>([]);
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  const [chosen, setChosen] = useState<string | undefined>();
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setRows(await state.cards(chosen, search || undefined));
    setCollections(await state.collections());
  }, [state, chosen, search]);

  useEffect(() => {
    void load();
  }, [load]);

  const syncNow = useCallback(async () => {
    setBusy(true);
    try {
      await state.sync();
      await load();
    } finally {
      setBusy(false);
    }
  }, [state, load]);

  return (
    <View style={styles.screen}>
      <TextInput
        style={styles.search}
        placeholder="Search your collection"
        placeholderTextColor="#8a8f9c"
        value={search}
        onChangeText={setSearch}
        autoCorrect={false}
      />

      <FlatList
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.chips}
        data={[
          { collection_uid: '', name: 'Everything', cards: 0 } as CollectionRow,
          ...collections,
        ]}
        keyExtractor={(c) => c.collection_uid || 'all'}
        renderItem={({ item }) => {
          const active = (chosen ?? '') === item.collection_uid;
          return (
            <Pressable
              onPress={() => setChosen(item.collection_uid || undefined)}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipText, active && styles.chipTextActive]}>
                {item.name}
                {item.collection_uid ? ` (${item.cards})` : ''}
              </Text>
            </Pressable>
          );
        }}
      />

      <FlatList
        data={rows}
        keyExtractor={(r) => r.stack_key}
        refreshControl={
          <RefreshControl refreshing={busy} onRefresh={syncNow} tintColor="#e4e6eb" />
        }
        ListEmptyComponent={
          <Text style={styles.empty}>
            {search
              ? 'Nothing here matches that.'
              : 'No cards yet. Scan some, or pull down to sync with your PC.'}
          </Text>
        }
        renderItem={({ item }) => (
          <Pressable
            style={styles.row}
            onPress={() => onOpenCard?.(item.stack_key)}
          >
            <Text style={styles.count}>{item.quantity}</Text>
            <View style={styles.grow}>
              <Text style={styles.name}>{item.card_name}</Text>
              {item.finish === 'foil' ? (
                <Text style={styles.foil}>foil</Text>
              ) : null}
            </View>
            {item.price_usd != null ? (
              <Text style={styles.price}>${item.price_usd.toFixed(2)}</Text>
            ) : null}
          </Pressable>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117', padding: 12 },
  search: {
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    padding: 12,
    fontSize: 16,
  },
  chips: { flexGrow: 0, marginVertical: 10 },
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#2d3142',
    marginRight: 8,
  },
  chipActive: { borderColor: '#48bb78' },
  chipText: { color: '#8a8f9c' },
  chipTextActive: { color: '#48bb78', fontWeight: '600' },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#2d3142',
  },
  grow: { flex: 1 },
  count: { color: '#8a8f9c', minWidth: 28, fontVariant: ['tabular-nums'] },
  name: { color: '#e4e6eb', fontSize: 16 },
  foil: { color: '#ecc94b', fontSize: 12 },
  price: { color: '#48bb78', fontVariant: ['tabular-nums'] },
  empty: { color: '#8a8f9c', textAlign: 'center', marginTop: 40, lineHeight: 22 },
});
