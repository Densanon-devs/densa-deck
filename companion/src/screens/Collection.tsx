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
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import { artQueue, artSource, cardImageUrl } from '../lib/images.ts';
import { ArtWarmer } from './ArtWarmer.tsx';
import { CollectionBar } from './CollectionBar.tsx';
import { reporting } from './report.ts';
import { DEFAULT_COLLECTION_UID } from '../lib/store.ts';
import type { CollectionRow, StackRow } from '../lib/store.ts';
import type { GroupManifest, GroupReview } from '../lib/protocol.ts';

interface Props {
  state: AppState;
  /** The whole row, not a key: the card screen needs the printing and the
   *  finish to show the right art and the right price. */
  onOpenCard?: (stack: StackRow) => void;
}

export function CollectionScreen({ state, onOpenCard }: Props) {
  const [rows, setRows] = useState<StackRow[]>([]);
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  const [chosen, setChosen] = useState<string | undefined>();
  /**
   * What the PC says is in the selected group, and the manifest for it.
   *
   * The bundle workflow was taggable from here and reviewable only on the PC,
   * which is most of the point gone: you tag a thousand cards standing over
   * the box and then have to walk to a desk to find out what you tagged or
   * what it is worth.
   *
   * Counted on the PC because that is where the prices are and where the
   * membership maths lives. The phone mirrors what you own, not what every
   * list claims.
   */
  const [review, setReview] = useState<GroupReview | null>(null);
  const [manifest, setManifest] = useState<GroupManifest | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  // Which row has its quantity controls out. One at a time: a list of
  // hundreds of rows each carrying three buttons is unusable.
  const [open, setOpen] = useState('');
  const [caching, setCaching] = useState('');
  // A true delete is never one tap. Viewing a filtered list and tapping
  // "Remove" destroyed the card instead of taking it out of the list,
  // which is precisely what a filter must not be able to do.
  const [confirmDelete, setConfirmDelete] = useState('');
  const [warming, setWarming] = useState<string[]>([]);

  /** Ask the PC what is in this group. Nothing here changes anything. */
  const reviewGroup = useCallback(
    async (uid: string) => {
      setReviewing(true);
      setManifest(null);
      setProblem('');
      try {
        setReview(await state.reviewGroup(uid));
      } catch (err) {
        setReview(null);
        setProblem(
          `${(err as Error).message}. The group total is counted on your PC, ` +
            'so it needs your PC to be reachable.',
        );
      } finally {
        setReviewing(false);
      }
    },
    [state],
  );

  /**
   * The manifest, as text to copy.
   *
   * Decklist form rather than CSV: on a phone this gets pasted into a message
   * to whoever is buying, and a page of comma-separated columns is not that.
   * The CSV is a file, and files belong on the desktop.
   */
  const exportGroup = useCallback(
    async (uid: string) => {
      setProblem('');
      try {
        setManifest(await state.exportGroup(uid, 'decklist'));
      } catch (err) {
        setProblem((err as Error).message);
      }
    },
    [state],
  );

  const load = useCallback(async () => {
    setRows(await state.cards(chosen, search || undefined));
    setCollections(await state.collections());
  }, [state, chosen, search]);

  useEffect(() => {
    void load().catch(reporting('your collection', setProblem));
  }, [load]);

  /**
   * Change how many of a stack you own.
   *
   * A scan that filed the wrong card had no undo at all — the only way out
   * was the desktop, which is exactly where you are not standing. Quantities
   * are deltas all the way down to the sync log, so this is the same
   * operation as scanning, with the sign flipped.
   */
  const adjust = useCallback(
    async (row: StackRow, delta: number) => {
      if (delta === 0) return;
      setProblem('');
      try {
        await state.addCard({
          printing_id: row.printing_id,
          card_name: row.card_name,
          oracle_id: row.oracle_id,
          finish: row.finish,
          condition: row.condition,
          collection_uid: row.collection_uid,
          quantity: delta,
        });
        // A stack that reaches zero stops existing, so its controls should
        // not stay open over the row that took its place.
        if (row.quantity + delta <= 0) setOpen('');
        await load();
      } catch (err) {
        reporting('changing how many you own', setProblem)(err);
      }
    },
    [state, load],
  );

  /**
   * Pull the collection's art onto the phone ahead of time.
   *
   * Art seen once is already cached by the phone. This is for the card you
   * have NOT opened — because the moment you want to flip through your
   * collection is usually the moment you have no signal, in a shop, holding
   * something you might trade for.
   */
  const cacheArt = useCallback(async () => {
    setProblem('');
    const all = await state.cards();
    const queue = artQueue(all.map((row) => row.printing_id));
    if (!queue.length) {
      setCaching('No art to fetch.');
      return;
    }
    setCaching(`Saving art 0/${queue.length}`);
    setWarming(queue);
  }, [state]);

  /**
   * Take a card out of the list being viewed, leaving the card alone.
   *
   * The thing people mean when they are looking at "Test scans" and want a
   * card gone from it. Deleting the card is a separate action, and it is the
   * one that used to happen by accident.
   */
  const takeOutOfList = useCallback(
    async (row: StackRow) => {
      if (!chosen || chosen === DEFAULT_COLLECTION_UID) return;
      if (row.collection_uid === chosen) return;
      setProblem('');
      await state.setListMembership(row, chosen, false);
      setOpen('');
      await load();
    },
    [state, chosen, load],
  );

  const syncNow = useCallback(async () => {
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

  return (
    <View style={styles.screen}>
      {problem ? <Text style={styles.problem}>{problem}</Text> : null}
      {/* The search, the art button and the collection chips are the list's
          header rather than a block above it — stacked they cannot scroll
          and they squeeze the list itself. */}
      <FlatList
        data={rows}
        ListHeaderComponent={
          <>
      <TextInput
            style={styles.search}
            placeholder="Search your collection"
            placeholderTextColor="#8a8f9c"
            value={search}
            onChangeText={setSearch}
            autoCorrect={false}
          />

          <ArtWarmer
            queue={warming}
            onProgress={(done, total) => setCaching(`Saving art ${done}/${total}`)}
            onDone={(failed) => {
              setWarming([]);
              setCaching(
                failed
                  ? `${failed} could not be fetched. Try again with a better connection.`
                  : 'All card art is on this phone.',
              );
            }}
          />

          <View style={styles.artRow}>
            <Pressable
              style={styles.artButton}
              onPress={() => {
                void cacheArt().catch(reporting('saving the art', setProblem));
              }}
            >
              <Text style={styles.artButtonText}>
                {caching ? 'Download art' : 'Download all card art'}
              </Text>
            </Pressable>
            {caching ? <Text style={styles.artStatus}>{caching}</Text> : null}
          </View>

          <CollectionBar
            collections={collections}
            selected={chosen ?? ''}
            onSelect={(uid) => {
              setChosen(uid || undefined);
              // A review belongs to the group it was asked about. Left on
              // screen after the selection changes it is a number about
              // something else.
              setReview(null);
              setManifest(null);
            }}
            onCreate={async (name) => {
              const uid = await state.newCollection(name);
              await load();
              return uid;
            }}
            includeEverything
            onDelete={async (uid) => {
              await state.deleteCollection(uid);
              await load();
            }}
          />

          {/*
            What is in this group, and what it is worth. Only for a named
            group: "everything I own" already has its own total above, and
            asking the PC to price the whole collection to repeat it would be
            a slow way to say nothing.
          */}
          {chosen && chosen !== DEFAULT_COLLECTION_UID ? (
            <View style={styles.groupBox}>
              <View style={styles.groupRow}>
                <Pressable
                  style={[styles.groupBtn, styles.grow]}
                  onPress={() => void reviewGroup(chosen)}
                >
                  <Text style={styles.groupBtnText}>
                    {reviewing ? 'Asking your PC…' : "What's in this group?"}
                  </Text>
                </Pressable>
                <Pressable
                  style={[styles.groupBtn, styles.grow]}
                  onPress={() => void exportGroup(chosen)}
                >
                  <Text style={styles.groupBtnText}>Manifest</Text>
                </Pressable>
              </View>

              {review ? (
                <>
                  <Text style={styles.groupTotal}>
                    {review.copies} card{review.copies === 1 ? '' : 's'} ·{' '}
                    ${review.value_usd.toFixed(2)}
                  </Text>
                  {review.unpriced_stacks ? (
                    <Text style={styles.muted}>
                      {review.unpriced_stacks} stacks had no price and are not
                      in that total.
                    </Text>
                  ) : null}
                  {review.wanted_elsewhere.length ? (
                    <Text style={styles.groupWarn}>
                      {review.wanted_elsewhere.length} of these are in decks of
                      yours:{' '}
                      {review.wanted_elsewhere
                        .slice(0, 5)
                        .map((w) => w.card_name)
                        .join(', ')}
                      {review.wanted_elsewhere.length > 5 ? '…' : ''}
                    </Text>
                  ) : null}
                </>
              ) : null}

              {manifest ? (
                <>
                  <Text style={styles.muted}>
                    {manifest.copies} cards — hold to copy and send it to
                    whoever is buying.
                  </Text>
                  <Text style={styles.manifest} selectable>
                    {manifest.text}
                  </Text>
                </>
              ) : null}
            </View>
          ) : null}

        </>
        }
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
          <View>
            <Pressable
              style={styles.row}
              onPress={() =>
                setOpen((current) =>
                  current === item.stack_key ? '' : item.stack_key,
                )
              }
            >
              <Text style={styles.count}>{item.quantity}</Text>
              {/* The art you already fetched shows here with no request at
                  all — the phone's image cache answers it. */}
              {cardImageUrl(item.printing_id, 'small') ? (
                <Image
                  source={artSource(item.printing_id, 'small')}
                  style={styles.thumb}
                  resizeMode="cover"
                />
              ) : null}
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

            {open === item.stack_key ? (
              <View style={styles.editRow}>
                <Pressable
                  style={styles.editButton}
                  onPress={() => void adjust(item, -1)}
                >
                  <Text style={styles.editText}>-1</Text>
                </Pressable>
                <Pressable
                  style={styles.editButton}
                  onPress={() => void adjust(item, 1)}
                >
                  <Text style={styles.editText}>+1</Text>
                </Pressable>
                <View style={styles.grow} />
                {/*
                  Taking a card out of the list you are looking at is the
                  thing people mean, and it is reversible. Destroying it is a
                  different action and now says so.
                */}
                {/*
                  Only when membership is what puts it here. A card FILED in
                  this collection is at home in it, and removing a membership
                  row it does not have would be a button that silently does
                  nothing — worse than not offering it.
                */}
                {chosen &&
                chosen !== DEFAULT_COLLECTION_UID &&
                item.collection_uid !== chosen ? (
                  <Pressable
                    style={styles.editButton}
                    onPress={() =>
                      void takeOutOfList(item).catch(
                        reporting('taking it out of the list', setProblem),
                      )
                    }
                  >
                    <Text style={styles.editText}>Take out of list</Text>
                  </Pressable>
                ) : null}

                {confirmDelete === item.stack_key ? (
                  <>
                    <Pressable
                      style={[styles.editButton, styles.removeButton]}
                      onPress={() => {
                        setConfirmDelete('');
                        void adjust(item, -item.quantity);
                      }}
                    >
                      <Text style={styles.removeText}>
                        Yes, delete {item.quantity > 1 ? `all ${item.quantity}` : 'it'}
                      </Text>
                    </Pressable>
                    <Pressable
                      style={styles.editButton}
                      onPress={() => setConfirmDelete('')}
                    >
                      <Text style={styles.editText}>Keep</Text>
                    </Pressable>
                  </>
                ) : (
                  <Pressable
                    style={[styles.editButton, styles.removeButton]}
                    onPress={() => setConfirmDelete(item.stack_key)}
                  >
                    <Text style={styles.removeText}>Delete card</Text>
                  </Pressable>
                )}
                <Pressable
                  style={styles.editButton}
                  onPress={() => onOpenCard?.(item)}
                >
                  {/* "Lists" as well as "View", because the list ticks live
                      on that screen and nobody found them behind a word that
                      only promises a picture. */}
                  <Text style={styles.editText}>View · Lists</Text>
                </Pressable>
              </View>
            ) : null}
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  groupBox: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 8,
    marginTop: 8,
  },
  groupRow: { flexDirection: 'row', gap: 8 },
  groupBtn: {
    borderColor: '#38a169',
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 10,
    alignItems: 'center',
  },
  groupBtnText: { color: '#68d391', fontSize: 13, fontWeight: '600' },
  groupTotal: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  groupWarn: { color: '#ecc94b', fontSize: 13, lineHeight: 19 },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  manifest: {
    color: '#c9ced9',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
  problem: { color: '#e53e3e', fontSize: 13, lineHeight: 19, paddingBottom: 6 },
  artRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingBottom: 4 },
  artButton: {
    borderColor: '#e53e3e',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 9,
  },
  artButtonText: { color: '#e53e3e', fontSize: 13, fontWeight: '600' },
  artStatus: { color: '#8a8f9c', fontSize: 12, flex: 1 },
  thumb: { width: 34, height: 47, borderRadius: 3, backgroundColor: '#1a1d27' },
  editRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingBottom: 10,
    paddingHorizontal: 4,
  },
  editButton: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 18,
    paddingVertical: 9,
  },
  editText: { color: '#e4e6eb', fontSize: 15, fontWeight: '700' },
  removeButton: { borderColor: '#e53e3e' },
  removeText: { color: '#e53e3e', fontSize: 13, fontWeight: '600' },
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
