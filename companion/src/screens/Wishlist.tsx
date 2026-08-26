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
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import type { DeckStore, WishlistRow } from '../lib/decks.ts';
import type { CatalogueCard } from '../lib/protocol.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  decks: DeckStore;
}

export function WishlistScreen({ state, decks }: Props) {
  const [rows, setRows] = useState<WishlistRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [search, setSearch] = useState('');
  const [found, setFound] = useState<CatalogueCard[]>([]);
  const [searching, setSearching] = useState(false);

  /**
   * Every card in Magic, not only the ones you own.
   *
   * A wishlist about cards you already have would be a strange thing. This
   * goes to the desktop because the catalogue is 34,000 cards and the phone
   * mirrors the collection, so it needs the PC — and says so when it cannot
   * reach it, rather than quietly finding nothing.
   */
  const lookUp = useCallback(async () => {
    const term = search.trim();
    if (term.length < 2) {
      setFound([]);
      return;
    }
    setSearching(true);
    setProblem('');
    try {
      const reply = await state.searchCards({ name: term, limit: 25 });
      setFound(reply.cards ?? []);
    } finally {
      setSearching(false);
    }
  }, [search, state]);

  const want = useCallback(
    async (name: string) => {
      await state.wishlistAdd(name, 1);
      setSearch('');
      setFound([]);
      await load();
    },
    [state],
  );


  const load = useCallback(async () => {
    setRows(await state.wishlist(await decks.list()));
  }, [state, decks]);

  useEffect(() => {
    void load().catch(reporting('your wishlist', setProblem));
  }, [load]);

  const refresh = useCallback(async () => {
    setBusy(true);
    setProblem('');
    try {
      await state.sync();
      await load();
    } catch (err) {
      reporting('syncing', setProblem)(err);
    } finally {
      setBusy(false);
    }
  }, [state, load]);

  const total = rows.reduce((sum, r) => sum + r.quantity, 0);

  return (
    <View style={styles.screen}>
      {problem ? <Text style={styles.problem}>{problem}</Text> : null}
      <Text style={styles.title}>Wishlist</Text>
      <Text style={styles.muted}>
        {rows.length
          ? `${total} card${total === 1 ? '' : 's'} your decks need that you ` +
            'don’t own. These aren’t part of your collection.'
          : ''}
      </Text>

      <View style={styles.finder}>
        <TextInput
          style={styles.search}
          placeholder="Search every card in Magic…"
          placeholderTextColor="#8a8f9c"
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={() => {
            void lookUp().catch(reporting('searching', setProblem));
          }}
          returnKeyType="search"
          autoCorrect={false}
        />
        <Pressable
          style={styles.find}
          onPress={() => {
            void lookUp().catch(reporting('searching', setProblem));
          }}
        >
          <Text style={styles.findText}>{searching ? '…' : 'Find'}</Text>
        </Pressable>
      </View>

      {found.length ? (
        <View style={styles.results}>
          {found.slice(0, 12).map((card) => (
            <Pressable
              key={card.scryfall_id ?? card.name}
              style={styles.result}
              onPress={() => {
                void want(card.name).catch(reporting('adding it', setProblem));
              }}
            >
              <View style={styles.grow}>
                <Text style={styles.name}>{card.name}</Text>
                <Text style={styles.muted}>{card.type_line}</Text>
              </View>
              <Text style={styles.add}>Want it</Text>
            </Pressable>
          ))}
        </View>
      ) : null}

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
  problem: { color: '#e53e3e', fontSize: 13, lineHeight: 19 },
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
  finder: { flexDirection: 'row', gap: 8, alignItems: 'center', marginTop: 10 },
  search: {
    flex: 1,
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  find: {
    backgroundColor: '#e53e3e',
    borderRadius: 10,
    paddingHorizontal: 18,
    paddingVertical: 11,
  },
  findText: { color: '#fff', fontWeight: '700' },
  results: { gap: 6, marginTop: 8 },
  result: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  add: { color: '#e53e3e', fontSize: 13, fontWeight: '700' },
});
