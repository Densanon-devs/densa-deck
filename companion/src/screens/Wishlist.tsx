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
import { entryKey, printingLabel } from '../lib/decks.ts';
import type { DeckStore, WishlistRow } from '../lib/decks.ts';
import type { CatalogueCard } from '../lib/protocol.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  decks: DeckStore;
}

export function WishlistScreen({ state, decks }: Props) {
  // Which row is mid-purchase, so the button can say so and cannot be
  // pressed twice — buying the same card twice is a real cost, not a
  // cosmetic one.
  const [buying, setBuying] = useState('');
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


  const load = useCallback(async () => {
    setRows(await state.wishlist(await decks.list()));
  }, [state, decks]);

  const want = useCallback(
    async (name: string) => {
      await state.wishlistAdd(name, 1);
      setSearch('');
      setFound([]);
      await load();
    },
    // Same reason as `bought`: `load` moves with the decks, and a row shows
    // how many copies yours want. Pinned to [state] this redrew the list
    // from the decks as they were when the screen opened.
    [state, load],
  );

  useEffect(() => {
    void load().catch(reporting('your wishlist', setProblem));
  }, [load]);

  /** Pull from the PC, then redraw the rows. */
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

  /**
   * You bought it.
   *
   * Files the card AND takes it off every list that wanted it, in one call,
   * because the two halves belong together: filing it without clearing the
   * list leaves you shopping for a card already in your bag, which is the
   * exact confusion the wishlist exists to prevent.
   *
   * Needs a PRINTING, because a copy of a card is a copy of some printing and
   * that is how the collection is keyed. A row that named one supplies it; a
   * row that did not gets a representative resolved first — the same
   * resolution the deck screen uses for art and prices.
   */
  const bought = useCallback(
    async (row: WishlistRow) => {
      setBuying(row.card_name);
      setProblem('');
      try {
        let printingId = (row.printing_id || '').trim();
        if (!printingId) {
          const slots = await state.deckSlots([
            {
              name: row.card_name,
              qty: 1,
              set_code: row.set_code,
              collector_number: row.collector_number,
            },
          ]);
          printingId = Object.values(slots)[0]?.printing_id ?? '';
        }
        if (!printingId) {
          setProblem(
            `Couldn't work out which printing of ${row.card_name} to file. ` +
              'Scan it instead — the scanner reads the exact one.',
          );
          return;
        }
        await state.acquireFromWishlist(printingId, row.card_name, 1);
        await refresh();
      } catch (err) {
        setProblem(
          `${(err as Error).message}. Filing a card you bought happens on ` +
            'your PC, so it needs your PC to be reachable.',
        );
      } finally {
        setBuying('');
      }
    },
    // `refresh` genuinely belongs here. It changes whenever the decks do —
    // it reloads the rows, and a wishlist row carries how many copies your
    // decks want. Pinned to [state] alone, filing a card would redraw the
    // list from whichever decks existed when this screen first rendered.
    [state, refresh],
  );


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

      {/*
        Everything above the list is the list's HEADER, not a block stacked
        on top of it.
        
        Stacked, it cannot scroll and it squeezes the list: twelve search
        results would leave the wishlist itself a couple of rows tall. One
        scroller, and the header scrolls with it.
      */}
      <FlatList
        data={rows}
        ListHeaderComponent={
          <>
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

        </>
        }
        // Keyed by SLOT, not by name. Two decks wanting two printings of one
        // card are two rows and two purchases, and keying on the name alone
        // would collide them — which in a FlatList means one row silently
        // disappears.
        keyExtractor={(r) => entryKey(r)}
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
              {/* Which printing to buy, when the deck asked for one. Without
                  it the list says "Sol Ring" and you come home with the wrong
                  one — the whole reason a slot can name a printing. */}
              {printingLabel(item) ? (
                <Text style={styles.printing}>{printingLabel(item)}</Text>
              ) : null}
              <Text style={styles.muted}>
                {item.wantedBy.map((w) => w.deck_name).join(', ')}
                {item.wantedBy.length > 1 &&
                item.quantityAcrossDecks > item.quantity
                  ? `  ·  ${item.quantityAcrossDecks} if you build them all at once`
                  : ''}
              </Text>
            </View>
            {/*
              The button that belongs on this screen. You are holding the
              wishlist in a shop; the moment you buy one is the moment it
              should stop being on the list.
            */}
            <Pressable
              style={styles.bought}
              disabled={buying === item.card_name}
              onPress={() => void bought(item)}
            >
              <Text style={styles.boughtText}>
                {buying === item.card_name ? 'Filing…' : 'Got it'}
              </Text>
            </Pressable>
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
  printing: { color: '#68d391', fontSize: 12 },
  bought: {
    borderColor: '#38a169',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  boughtText: { color: '#68d391', fontSize: 13, fontWeight: '600' },
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
