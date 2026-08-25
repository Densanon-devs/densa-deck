/**
 * What your decks want that you do not own.
 *
 * Derived from local decks and the local mirror, so it answers with no signal
 * — which is the situation it exists for. You look at this standing in a shop.
 *
 * Nothing here is owned. That is stated on the screen rather than left to be
 * inferred, because a list of cards inside a collection app reads as "mine"
 * unless it says otherwise.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import type { DeckStore, WishlistRow } from '../lib/decks.ts';

interface Props {
  state: AppState;
  decks: DeckStore;
}

export function WishlistScreen({ state, decks }: Props) {
  const [rows, setRows] = useState<WishlistRow[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setRows(await state.wishlist(await decks.list()));
  }, [state, decks]);

  useEffect(() => {
    void load();
  }, [load]);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      await state.sync();
      await load();
    } finally {
      setBusy(false);
    }
  }, [state, load]);

  const total = rows.reduce((sum, r) => sum + r.quantity, 0);

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>Wishlist</Text>
      <Text style={styles.muted}>
        {rows.length
          ? `${total} card${total === 1 ? '' : 's'} your decks need that you ` +
            'don’t own. These aren’t part of your collection.'
          : ''}
      </Text>

      <FlatList
        data={rows}
        keyExtractor={(r) => r.card_name}
        refreshControl={
          <RefreshControl refreshing={busy} onRefresh={refresh} tintColor="#e4e6eb" />
        }
        ListEmptyComponent={
          <Text style={styles.empty}>
            Nothing wanted. Every card in your decks is one you own — or you
            haven’t built a deck yet.
          </Text>
        }
        renderItem={({ item }) => (
          <View style={styles.row}>
            <Text style={styles.need}>{item.quantity}</Text>
            <View style={styles.grow}>
              <Text style={styles.name}>{item.card_name}</Text>
              <Text style={styles.muted}>
                {item.wantedBy.map((w) => w.deck_name).join(', ')}
                {item.wantedBy.length > 1 &&
                item.quantityAcrossDecks > item.quantity
                  ? `  ·  ${item.quantityAcrossDecks} if you build them all at once`
                  : ''}
              </Text>
            </View>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117', padding: 14 },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#2d3142',
  },
  grow: { flex: 1 },
  need: { color: '#ecc94b', minWidth: 26, fontWeight: '700', fontSize: 16 },
  name: { color: '#e4e6eb', fontSize: 16 },
  empty: { color: '#8a8f9c', textAlign: 'center', marginTop: 40, lineHeight: 22 },
});
