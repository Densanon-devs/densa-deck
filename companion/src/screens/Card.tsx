/**
 * One card: what it looks like and what it does.
 *
 * The art is a Scryfall URL, never a file served from us: images are
 * hotlinked and never rehosted, which is why this is the one screen that
 * reaches past the desktop to the open internet. Once fetched it stays in the
 * phone's own image cache, which is what Scryfall's guidelines ask clients to
 * do — keeping what you already have is the opposite of rehosting it.
 *
 * Which means it degrades in two independent ways, and they are different:
 *
 *   * **No route to the PC** — the rules text cannot be fetched, but the art
 *     still loads and what you own is already on the phone. Most of the
 *     screen still works.
 *   * **No internet** — the art will not load, but the rules text may still
 *     arrive over the tailnet. Also most of the screen.
 *
 * So neither failure is treated as the screen failing. What is known is shown,
 * and what is missing says so.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import {
  artSource,
  cardImageUrl,
  checkArtReachable,
  scryfallPageUrl,
} from '../lib/images.ts';
import type { CardDetail, CardFace } from '../lib/protocol.ts';
import type { CollectionRow, StackRow } from '../lib/store.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  stack: StackRow;
  onClose: () => void;
}

/**
 * A price series, drawn as bars.
 *
 * Bars rather than a line because React Native has no SVG here and a
 * polyline would mean pulling in a charting library for forty pixels of
 * chrome. Heights are scaled to the range rather than to zero: what somebody
 * wants from this is the SHAPE — has it been climbing — and a $0.20 card
 * plotted from zero is a flat line whatever it did.
 */
function PriceBars({ points }: {
  points: Array<{ captured_on: string; price_usd: number | null }>;
}) {
  const values = points
    .map((p) => Number(p.price_usd))
    .filter((v) => Number.isFinite(v));
  if (values.length < 2) return null;

  const low = Math.min(...values);
  const high = Math.max(...values);
  // A flat series is a real answer, so a zero range becomes half-height
  // rather than a division by zero.
  const span = high - low || 1;
  // Pulled out rather than indexed inline: the length check above does not
  // narrow an indexed read for the compiler, and `?? low` is a truthful
  // fallback for a series that cannot be empty here anyway.
  const first = values[0] ?? low;
  const last = values[values.length - 1] ?? high;
  const move = last - first;
  const tone = move > 0 ? '#68d391' : move < 0 ? '#fc8181' : '#8a8f9c';

  return (
    <View style={styles.sparkWrap}>
      <View style={styles.spark}>
        {values.map((v, i) => (
          <View
            key={i}
            style={[
              styles.sparkBar,
              {
                backgroundColor: tone,
                height: Math.max(2, ((v - low) / span) * 34 + 2),
              },
            ]}
          />
        ))}
      </View>
      <Text style={styles.sparkLabel}>
        ${last.toFixed(2)}
        <Text style={{ color: tone }}>
          {'  '}{move >= 0 ? '+' : ''}{move.toFixed(2)}
        </Text>
        <Text style={styles.muted}>
          {'  '}over {values.length} readings · low ${low.toFixed(2)} ·
          high ${high.toFixed(2)}
        </Text>
      </Text>
    </View>
  );
}

/** Mana costs arrive as "{2}{U}{U}"; braces are noise on a phone. */
function readableCost(cost: string): string {
  return (cost || '').replace(/[{}]/g, ' ').replace(/\s+/g, ' ').trim();
}

export function CardScreen({ state, stack, onClose }: Props) {
  const [detail, setDetail] = useState<CardDetail | null>(null);
  const [problem, setProblem] = useState('');
  /**
   * The price series, and whether it came from the phone's own copy.
   *
   * Loaded beside the card rather than on demand: it is two dozen numbers,
   * and a second tap to see whether a card is climbing is a tap most people
   * will not make.
   */
  const [history, setHistory] = useState<{
    points: Array<{ captured_on: string; price_usd: number | null }>;
    scope: string;
    cached: boolean;
  }>({ points: [], scope: 'printing', cached: false });
  const [artFailed, setArtFailed] = useState('');
  const [busy, setBusy] = useState(true);
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  const [lists, setLists] = useState<string[]>([]);

  const loadLists = useCallback(async () => {
    setCollections(await state.collections());
    setLists(await state.listsFor(stack.stack_key));
  }, [state, stack.stack_key]);

  useEffect(() => {
    void loadLists().catch(reporting('the lists', setProblem));
  }, [loadLists]);

  useEffect(() => {
    // Never fatal and never reported: a card with no price history is the
    // normal case, and a red line about it would be noise on a screen whose
    // job is showing you a card.
    void (async () => {
      setHistory(await state.priceHistory(stack.printing_id ?? '',
                                          stack.card_name ?? ''));
    })().catch(() => undefined);
  }, [state, stack.printing_id, stack.card_name]);

  /**
   * Put this card in a list, or take it out.
   *
   * Ticking one never unticks another — collections are filters, so a card
   * can be in a set you are completing AND a deck AND last weekend's
   * seventy-five. Unticking never removes the card.
   */
  const toggleList = useCallback(
    async (uid: string, member: boolean) => {
      await state.setListMembership(stack, uid, member);
      await loadLists();
    },
    [state, stack, loadLists],
  );

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setDetail(await state.cardDetail(stack.printing_id, stack.card_name));
      setProblem('');
    } finally {
      setBusy(false);
    }
  }, [state, stack.printing_id, stack.card_name]);

  useEffect(() => {
    void load().catch(reporting('the card details', setProblem));
  }, [load]);

  const art = cardImageUrl(stack.printing_id, 'normal');
  const page = scryfallPageUrl(stack.printing_id);
  const faces = detail?.faces ?? [];

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title} numberOfLines={2}>
          {detail?.card_name || stack.card_name}
        </Text>
        <Pressable onPress={onClose}>
          <Text style={styles.close}>Done</Text>
        </Pressable>
      </View>

      {collections.length ? (
        <View style={styles.panel}>
          <Text style={styles.faceName}>In these lists</Text>
          <Text style={styles.muted}>
            A card can be in as many as you like. Adding it to one never takes
            it out of another, and removing it never removes the card.
          </Text>
          {collections.map((collection) => {
            const inIt = lists.includes(collection.collection_uid);
            return (
              <Pressable
                key={collection.collection_uid}
                style={[styles.listRow, inIt && styles.listRowOn]}
                onPress={() => {
                  void toggleList(collection.collection_uid, !inIt).catch(
                    reporting('changing the lists', setProblem),
                  );
                }}
              >
                <Text style={[styles.listTick, inIt && styles.listTickOn]}>
                  {inIt ? '✓' : '•'}
                </Text>
                <Text style={styles.listName}>{collection.name}</Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}

      {art && !artFailed ? (
        <Image
          source={artSource(stack.printing_id, 'normal')}
          style={styles.art}
          resizeMode="contain"
          // The real error, not a guess at it. "Needs internet" was a
          // reasonable assumption that turned out to explain nothing.
          onError={(event) =>
            setArtFailed(
              event?.nativeEvent?.error
                ? String(event.nativeEvent.error)
                : 'the image did not load',
            )
          }
        />
      ) : (
        <View style={[styles.art, styles.artMissing]}>
          <Text style={styles.muted}>
            {art ? `Art did not load: ${artFailed}` : 'No art for this printing.'}
          </Text>
          {art ? (
            <Pressable
              style={styles.retry}
              onPress={() => {
                setArtFailed('');
                void checkArtReachable()
                  .then((r) => {
                    if (!r.ok) setProblem(r.detail);
                  })
                  .catch(reporting('checking Scryfall', setProblem));
              }}
            >
              <Text style={styles.retryText}>Try again</Text>
            </Pressable>
          ) : null}
        </View>
      )}

      <View style={styles.ownedRow}>
        <Text style={styles.owned}>
          You own {stack.quantity}
          {stack.finish === 'foil' ? ' foil' : ''}
        </Text>
        {stack.price_usd != null ? (
          <Text style={styles.price}>${stack.price_usd.toFixed(2)} each</Text>
        ) : null}
      </View>

      {/*
        What it has been worth. Two points or more, because one point is a
        price rather than a history and drawing it would imply a steadiness
        nobody measured.
      */}
      {history.points.length >= 2 ? (
        <>
          <PriceBars points={history.points} />
          <Text style={styles.muted}>
            {history.scope === 'card'
              ? 'Cheapest copy each day.'
              : 'This printing, each day your PC was open.'}
            {history.cached ? ' From this phone — your PC is not reachable.' : ''}
          </Text>
        </>
      ) : null}

      {busy && !detail ? (
        <ActivityIndicator color="#8a8f9c" />
      ) : null}

      {detail?.unknown_card ? (
        <Text style={styles.muted}>
          This card is not in the catalogue on your PC, so there is no rules
          text to show. The art and what you own are right either way.
        </Text>
      ) : null}

      {detail && !detail.unknown_card ? (
        <View style={styles.panel}>
          <Text style={styles.typeLine}>
            {detail.type_line}
            {readableCost(detail.mana_cost ?? '')
              ? `   ${readableCost(detail.mana_cost ?? '')}`
              : ''}
          </Text>

          {detail.oracle_text ? (
            <Text style={styles.oracle}>{detail.oracle_text}</Text>
          ) : null}

          {detail.power || detail.toughness ? (
            <Text style={styles.stat}>
              {detail.power}/{detail.toughness}
            </Text>
          ) : null}
          {detail.loyalty ? (
            <Text style={styles.stat}>Loyalty {detail.loyalty}</Text>
          ) : null}

          <Text style={styles.muted}>
            {[detail.set_code?.toUpperCase(), detail.rarity].filter(Boolean).join(' · ')}
          </Text>
        </View>
      ) : null}

      {/* Both halves of a split or transforming card. Showing only the front
          of one is telling you half the card. */}
      {faces.length > 1
        ? faces.map((face: CardFace, index: number) => (
            <View key={`${face.name}-${index}`} style={styles.panel}>
              <Text style={styles.faceName}>{face.name}</Text>
              <Text style={styles.typeLine}>
                {face.type_line}
                {readableCost(face.mana_cost) ? `   ${readableCost(face.mana_cost)}` : ''}
              </Text>
              {face.oracle_text ? (
                <Text style={styles.oracle}>{face.oracle_text}</Text>
              ) : null}
              {face.power || face.toughness ? (
                <Text style={styles.stat}>
                  {face.power}/{face.toughness}
                </Text>
              ) : null}
            </View>
          ))
        : null}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {page ? (
        <Pressable
          style={styles.link}
          onPress={() => {
            void Linking.openURL(page).catch(
              reporting('opening Scryfall', setProblem),
            );
          }}
        >
          <Text style={styles.linkText}>Rulings and printings on Scryfall</Text>
        </Pressable>
      ) : null}

      <Text style={styles.credit}>
        Card images and data from Scryfall. Not affiliated with Wizards of the
        Coast.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 16, gap: 12, paddingBottom: 40 },
  header: { flexDirection: 'row', alignItems: 'flex-start', gap: 12 },
  title: { color: '#e4e6eb', fontSize: 20, fontWeight: '700', flex: 1 },
  close: { color: '#e53e3e', fontSize: 16, fontWeight: '600', paddingTop: 2 },
  art: {
    width: '100%',
    aspectRatio: 745 / 1040, // a Magic card, so it never letterboxes
    borderRadius: 14,
    backgroundColor: '#000',
  },
  artMissing: {
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
    backgroundColor: '#1a1d27',
  },
  ownedRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  owned: { color: '#38a169', fontSize: 16, fontWeight: '700', flex: 1 },
  price: { color: '#8a8f9c', fontSize: 14 },
  sparkWrap: { gap: 4, marginTop: 10 },
  spark: {
    alignItems: 'flex-end',
    flexDirection: 'row',
    gap: 2,
    height: 38,
  },
  sparkBar: { borderRadius: 1, flex: 1, minWidth: 2 },
  sparkLabel: { color: '#e4e6eb', fontSize: 13 },
  panel: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 8,
  },
  faceName: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  typeLine: { color: '#e4e6eb', fontSize: 15 },
  oracle: { color: '#c9ced9', fontSize: 14, lineHeight: 21 },
  stat: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  problem: { color: '#e53e3e', fontSize: 13, lineHeight: 19 },
  listRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  listRowOn: { borderColor: '#38a169' },
  listTick: { color: '#8a8f9c', fontSize: 15, width: 16 },
  listTickOn: { color: '#38a169', fontWeight: '700' },
  listName: { color: '#e4e6eb', fontSize: 15 },
  link: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 13,
    alignItems: 'center',
  },
  linkText: { color: '#e4e6eb', fontSize: 15 },
  retry: {
    marginTop: 12,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  retryText: { color: '#e4e6eb', fontSize: 13 },
  credit: { color: '#5a5f6c', fontSize: 11, lineHeight: 16 },
});
