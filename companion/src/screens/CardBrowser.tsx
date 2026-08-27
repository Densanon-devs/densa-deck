/**
 * Flipping through cards to find one, rather than typing its name.
 *
 * Deckbuilding is a browsing job. You know you want a two-mana red removal
 * spell; you do not know which. A text box that needs the answer before it
 * will show you anything is the wrong shape for that question, which is why
 * every client that people actually build decks in shows a grid of pictures
 * and a row of filters instead.
 *
 * Searches the WHOLE catalogue by default, not the collection. What you own
 * is one filter among several here — "what could go in this deck" is a
 * different question from "what do I have", and answering the second when
 * the first was asked is how a deckbuilder becomes useless.
 *
 * The images are Scryfall URLs carrying our User-Agent, cached by the phone
 * once seen. See `lib/images.ts` — their CDN answers 400 to the default one.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import { artSource } from '../lib/images.ts';
import type {
  CardQuery,
  CatalogueCard,
  CataloguePrinting,
  CatalogueSet,
} from '../lib/protocol.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  onPick: (card: CatalogueCard) => void | Promise<void>;
  /** Offered alongside Add when the caller can take one back out. */
  onUnpick?: (card: CatalogueCard) => void | Promise<void>;
  onClose?: () => void;
  /** Shown on each tile, e.g. how many are already in the deck. */
  countFor?: (card: CatalogueCard) => number;
  /**
   * Whether a tap opens the card or adds it outright.
   *
   * Building a deck, a tap should show you the card — at this size you
   * cannot read the rules text, and adding something you have not read on a
   * thumbnail you may have misidentified is a mistake you find later. On the
   * wishlist, where the answer is just yes, adding straight away is right.
   */
  previewOnTap?: boolean;
}

const COLOURS: Array<{ key: string; label: string }> = [
  { key: 'W', label: 'W' },
  { key: 'U', label: 'U' },
  { key: 'B', label: 'B' },
  { key: 'R', label: 'R' },
  { key: 'G', label: 'G' },
  { key: 'C', label: 'C' },
];

const RARITIES = ['common', 'uncommon', 'rare', 'mythic'];

const SORTS: Array<{ key: NonNullable<CardQuery['sort']>; label: string }> = [
  { key: 'name', label: 'A–Z' },
  { key: 'cmc', label: 'Cost ↑' },
  { key: 'cmc_desc', label: 'Cost ↓' },
  { key: 'rarity', label: 'Rarity' },
  { key: 'price', label: 'Price' },
];

const TYPES = [
  'Creature',
  'Instant',
  'Sorcery',
  'Artifact',
  'Enchantment',
  'Planeswalker',
  'Land',
];

export function CardBrowser({
  state,
  onPick,
  onUnpick,
  onClose,
  countFor,
  previewOnTap = false,
}: Props) {
  const [preview, setPreview] = useState<CatalogueCard | null>(null);
  // Every printing of the card being looked at, so you can swipe through the
  // art. The same card in six sets is six different pictures, and which one
  // you own or want is a real question — this is the only place in the app
  // that could answer it and did not.
  const [variants, setVariants] = useState<CataloguePrinting[]>([]);

  useEffect(() => {
    if (!preview) {
      setVariants([]);
      return;
    }
    void state
      .printingsFor(preview.name)
      .then((r) => setVariants(r.printings ?? []))
      // A card whose printings cannot be fetched still shows its own art;
      // losing the swipe is not losing the screen.
      .catch(() => setVariants([]));
  }, [preview, state]);
  const [name, setName] = useState('');
  const [colours, setColours] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [ownedOnly, setOwnedOnly] = useState(false);
  const [rarities, setRarities] = useState<string[]>([]);
  const [sets, setSets] = useState<string[]>([]);
  const [sort, setSort] = useState<NonNullable<CardQuery['sort']>>('name');
  const [text, setText] = useState('');
  const [allSets, setAllSets] = useState<CatalogueSet[]>([]);
  const [pickingSet, setPickingSet] = useState(false);
  const [cards, setCards] = useState<CatalogueCard[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [searched, setSearched] = useState(false);

  const run = useCallback(async () => {
    const query: CardQuery = { limit: 60, sort };
    const term = name.trim();
    const words = text.trim();
    if (term) query.name = term;
    if (words) query.text = words;
    if (colours.length) {
      query.colors = colours;
      query.color_match = 'any';
    }
    if (types.length) query.types = types;
    if (rarities.length) query.rarities = rarities;
    if (sets.length) query.set_codes = sets;
    if (ownedOnly) query.ownership = 'owned';

    // Every field empty would ask the desktop for the entire catalogue.
    if (!term && !words && !colours.length && !types.length &&
        !rarities.length && !sets.length && !ownedOnly) {
      setProblem('Pick a filter, or type part of a name or a rules word.');
      return;
    }

    setBusy(true);
    setProblem('');
    try {
      const reply = await state.searchCards(query);
      setCards(reply.cards ?? []);
      setSearched(true);
    } finally {
      setBusy(false);
    }
  }, [name, text, colours, types, rarities, sets, ownedOnly, sort, state]);

  // Re-run when a filter changes, but not on every keystroke: the search
  // goes to the PC and typing "Lightning Bolt" would send eleven requests.
  useEffect(() => {
    if (!colours.length && !types.length && !rarities.length && !sets.length &&
        !ownedOnly && !name.trim() && !text.trim()) return;
    void run().catch(reporting('searching', setProblem));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colours, types, rarities, sets, ownedOnly, sort]);

  useEffect(() => {
    if (!pickingSet || allSets.length) return;
    void state
      .sets()
      .then((r) => setAllSets(r.sets ?? []))
      .catch(reporting('the set list', setProblem));
  }, [pickingSet, allSets.length, state]);

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <TextInput
          style={styles.search}
          placeholder="Search every card in Magic…"
          placeholderTextColor="#8a8f9c"
          value={name}
          onChangeText={setName}
          onSubmitEditing={() => void run().catch(reporting('searching', setProblem))}
          returnKeyType="search"
          autoCorrect={false}
        />
        {onClose ? (
          <Pressable onPress={onClose}>
            <Text style={styles.close}>Done</Text>
          </Pressable>
        ) : null}
      </View>

      <View style={[styles.filters, styles.filterRow]}>
        {COLOURS.map((colour) => {
          const on = colours.includes(colour.key);
          return (
            <Pressable
              key={colour.key}
              style={[styles.pip, on && styles.pipOn]}
              onPress={() => setColours((c) => toggle(c, colour.key))}
            >
              <Text style={[styles.pipText, on && styles.pipTextOn]}>
                {colour.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <TextInput
        style={[styles.search, styles.filterRow]}
        placeholder="Rules text — deathtouch, draw a card…"
        placeholderTextColor="#8a8f9c"
        value={text}
        onChangeText={setText}
        onSubmitEditing={() => void run().catch(reporting('searching', setProblem))}
        returnKeyType="search"
        autoCorrect={false}
      />

      <ScrollView
        horizontal
        style={styles.filterRow}
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {SORTS.map((option) => (
          <Pressable
            key={option.key}
            style={[styles.chip, sort === option.key && styles.chipOn]}
            onPress={() => setSort(option.key)}
          >
            <Text
              style={[styles.chipText, sort === option.key && styles.chipTextOn]}
            >
              {option.label}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      <ScrollView
        horizontal
        style={styles.filterRow}
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {RARITIES.map((r) => {
          const on = rarities.includes(r);
          return (
            <Pressable
              key={r}
              style={[styles.chip, on && styles.chipOn]}
              onPress={() => setRarities((list) => toggle(list, r))}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>
                {r.charAt(0).toUpperCase() + r.slice(1)}
              </Text>
            </Pressable>
          );
        })}
        <Pressable
          style={[styles.chip, sets.length > 0 && styles.chipOn]}
          onPress={() => setPickingSet((open) => !open)}
        >
          <Text style={[styles.chipText, sets.length > 0 && styles.chipTextOn]}>
            {sets.length ? `Sets (${sets.length})` : 'Sets'}
          </Text>
        </Pressable>
      </ScrollView>

      {/* Newest first: alphabetical would bury this year's under thirty
          years of others. Picking two means "either" — a card cannot be in
          two sets, so an AND returns nothing and looks broken. */}
      {pickingSet ? (
        <ScrollView style={styles.setList}>
          <View style={styles.filters}>
            {allSets.map((entry) => {
              const on = sets.includes(entry.set_code);
              return (
                <Pressable
                  key={entry.set_code}
                  style={[styles.chip, on && styles.chipOn]}
                  onPress={() => setSets((list) => toggle(list, entry.set_code))}
                >
                  <Text style={[styles.chipText, on && styles.chipTextOn]}>
                    {entry.set_code.toUpperCase()}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </ScrollView>
      ) : null}

      <ScrollView
        horizontal
        style={styles.filterRow}
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filters}
      >
        {TYPES.map((type) => {
          const on = types.includes(type);
          return (
            <Pressable
              key={type}
              style={[styles.chip, on && styles.chipOn]}
              onPress={() => setTypes((t) => toggle(t, type))}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>{type}</Text>
            </Pressable>
          );
        })}
        <Pressable
          style={[styles.chip, ownedOnly && styles.chipOn]}
          onPress={() => setOwnedOnly((o) => !o)}
        >
          <Text style={[styles.chipText, ownedOnly && styles.chipTextOn]}>
            Only mine
          </Text>
        </Pressable>
      </ScrollView>

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}
      {busy ? <ActivityIndicator color="#8a8f9c" /> : null}

      {searched && !busy && !cards.length && !problem ? (
        <Text style={styles.muted}>Nothing matches that.</Text>
      ) : null}

      <View style={styles.grid}>
        {cards.map((card) => {
          const held = countFor?.(card) ?? 0;
          return (
            <Pressable
              key={card.scryfall_id || card.name}
              style={styles.tile}
              onPress={() =>
                previewOnTap ? setPreview(card) : void onPick(card)
              }
            >
              <Image
                source={artSource(card.scryfall_id, 'small')}
                style={styles.tileArt}
                resizeMode="contain"
              />
              {/* The name under the picture, because a thumbnail of a card
                  you have not seen before is not yet recognisable. */}
              <Text style={styles.tileName} numberOfLines={2}>
                {card.name}
              </Text>
              {held ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{held}</Text>
                </View>
              ) : null}
            </Pressable>
          );
        })}
      </View>

      {/* The card, big enough to read, and the three things you might want
          to do with it. */}
      {preview ? (
        <View style={styles.previewWrap}>
          <ScrollView contentContainerStyle={styles.previewInner}>
            {variants.length > 1 ? (
              <ScrollView
                horizontal
                pagingEnabled
                showsHorizontalScrollIndicator={false}
                style={styles.pager}
              >
                {variants.map((printing) => (
                  <View key={printing.printing_id} style={styles.page}>
                    <Image
                      source={artSource(printing.printing_id, 'large')}
                      style={styles.previewArt}
                      resizeMode="contain"
                    />
                    <Text style={styles.muted}>
                      {printing.set_code.toUpperCase()} #
                      {printing.collector_number}
                      {printing.price_usd != null
                        ? `  ·  $${printing.price_usd.toFixed(2)}`
                        : ''}
                    </Text>
                  </View>
                ))}
              </ScrollView>
            ) : (
              <Image
                source={artSource(preview.scryfall_id, 'large')}
                style={styles.previewArt}
                resizeMode="contain"
              />
            )}
            {variants.length > 1 ? (
              <Text style={styles.muted}>
                {variants.length} printings — swipe to see them
              </Text>
            ) : null}
            <Text style={styles.previewName}>{preview.name}</Text>
            <Text style={styles.muted}>{preview.type_line}</Text>
            {countFor ? (
              <Text style={styles.muted}>
                {countFor(preview)} in here already
              </Text>
            ) : null}

            <View style={styles.previewButtons}>
              <Pressable
                style={styles.previewButton}
                onPress={() => setPreview(null)}
              >
                <Text style={styles.previewButtonText}>Close</Text>
              </Pressable>
              {onUnpick ? (
                <Pressable
                  style={styles.previewButton}
                  onPress={() => void onUnpick(preview)}
                >
                  <Text style={styles.previewButtonText}>Remove</Text>
                </Pressable>
              ) : null}
              <Pressable
                style={[styles.previewButton, styles.previewAdd]}
                onPress={() => void onPick(preview)}
              >
                <Text style={styles.previewAddText}>Add</Text>
              </Pressable>
            </View>
            {/* Stays open on purpose: adding a second copy is one more tap
                rather than finding the card again. */}
          </ScrollView>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  // No flex and no fixed height: the browser is as tall as its content
  // and the page it sits in does the scrolling. A fixed box crushed the
  // rows to fit AND left the grid unscrollable, because the outer page
  // had already taken the gesture — so only the rows that happened to
  // fit were ever reachable.
  screen: { gap: 8 },
  // Every fixed-height row needs this, and the grid needs `flex: 1`.
  //
  // The browser lives in a 560px box. Its rows add up to more than that, so
  // without a shrink guard React Native compresses them ALL vertically — the
  // borders survive at full width and the text inside is clipped to nothing,
  // which reads as pills with no labels and inputs with no placeholder.
  // Chips also needed flexShrink horizontally; this is the other axis, and
  // fixing one did not fix the other.
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    flexShrink: 0,
  },
  search: {
    flexShrink: 0,
    flex: 1,
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  close: { color: '#e53e3e', fontSize: 15, fontWeight: '600' },
  filters: { flexDirection: 'row', gap: 6, alignItems: 'center' },
  filterRow: { flexGrow: 0, flexShrink: 0 },
  pip: {
    flexShrink: 0,
    width: 36,
    height: 36,
    borderRadius: 18,
    borderColor: '#2d3142',
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pipOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  pipText: { color: '#8a8f9c', fontWeight: '700' },
  pipTextOn: { color: '#fff' },
  chip: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 7,
    // Both of these, and neither is optional. Inside a horizontal ScrollView
    // a row of chips is laid out against the visible width, so every one of
    // them shrinks until its label is squeezed to nothing — the pills render
    // as empty outlines with a smear where the word should be. flexShrink
    // stops the box collapsing; flexBasis: 'auto' makes it take its content's
    // width rather than an equal share.
    flexShrink: 0,
    flexBasis: 'auto',
  },
  // Filled when selected, not merely outlined. Green text on the dark
  // background was legible in isolation and not at 12px in a row of eight —
  // the selected chip became the hardest one to read, which is backwards.
  chipOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  chipText: { color: '#c9ced9', fontSize: 12, flexShrink: 0 },
  chipTextOn: { color: '#ffffff', fontWeight: '700' },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    paddingBottom: 30,
  },
  tile: { width: '31%' },
  tileArt: {
    width: '100%',
    aspectRatio: 745 / 1040,
    borderRadius: 6,
    backgroundColor: '#1a1d27',
  },
  tileName: { color: '#c9ced9', fontSize: 11, marginTop: 3 },
  badge: {
    position: 'absolute',
    top: 4,
    right: 4,
    minWidth: 22,
    borderRadius: 11,
    backgroundColor: '#38a169',
    alignItems: 'center',
    paddingVertical: 2,
    paddingHorizontal: 6,
  },
  badgeText: { color: '#fff', fontSize: 12, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13 },
  previewWrap: {
    position: 'absolute',
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: '#0f1117f2',
    zIndex: 20,
  },
  previewInner: { padding: 16, gap: 8, alignItems: 'center' },
  pager: { width: '100%' },
  page: { width: 300, alignItems: 'center', gap: 6, paddingHorizontal: 6 },
  previewArt: { width: 280, aspectRatio: 745 / 1040, borderRadius: 12 },
  previewName: { color: '#e4e6eb', fontSize: 18, fontWeight: '700' },
  previewButtons: { flexDirection: 'row', gap: 10, marginTop: 10 },
  previewButton: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 22,
    paddingVertical: 12,
  },
  previewButtonText: { color: '#e4e6eb', fontSize: 15 },
  previewAdd: { backgroundColor: '#38a169', borderColor: '#38a169' },
  previewAddText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  setList: { maxHeight: 130, flexShrink: 0 },
  problem: { color: '#e53e3e', fontSize: 12, lineHeight: 18 },
});
