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
  Modal,
  useWindowDimensions,
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
  /**
   * Take this card.
   *
   * The second argument is the printing the user was LOOKING AT when they
   * chose, and it is only ever passed when they said so explicitly — the
   * plain Add stays name-level, because "any printing will do" is what most
   * deck slots mean and is what an import is. Turning every add into an exact
   * one because a picture happened to be on screen would quietly make every
   * deck printing-level.
   */
  onPick: (
    card: CatalogueCard,
    printing?: CataloguePrinting,
  ) => void | Promise<void>;
  /** Offered alongside Add when the caller can take one back out. */
  onUnpick?: (card: CatalogueCard) => void | Promise<void>;
  onClose?: () => void;
  /** Shown on each tile, e.g. how many are already in the deck. */
  countFor?: (card: CatalogueCard) => number;
  /** How many of ONE printing are already in, for the swipe-through preview. */
  countForPrinting?: (printingId: string) => number;
  /**
   * Whether a tap opens the card or adds it outright.
   *
   * Building a deck, a tap should show you the card — at this size you
   * cannot read the rules text, and adding something you have not read on a
   * thumbnail you may have misidentified is a mistake you find later. On the
   * wishlist, where the answer is just yes, adding straight away is right.
   */
  previewOnTap?: boolean;
  /**
   * Bumped by the page when the user scrolls near the bottom.
   *
   * The browser cannot see the scroll itself — the page it sits in owns the
   * scroller, deliberately, because two nested ones meant the grid never
   * scrolled at all. So the page nudges and the browser fetches.
   */
  nearEnd?: number;
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
  countForPrinting,
  previewOnTap = false,
  nearEnd = 0,
}: Props) {
  const [preview, setPreview] = useState<CatalogueCard | null>(null);
  // Every printing of the card being looked at, so you can swipe through the
  // art. The same card in six sets is six different pictures, and which one
  // you own or want is a real question — this is the only place in the app
  // that could answer it and did not.
  const [variants, setVariants] = useState<CataloguePrinting[]>([]);
  // Which one is under the thumb. The pager alone could not say, so "Add this
  // printing" had nothing to name — and a picker you can look through but not
  // pick from is the half that does not help.
  const [showing, setShowing] = useState(0);
  // How wide one page of the printing pager is, measured rather than assumed.
  // It was a hardcoded 300 while the index was computed from the VIEWPORT
  // width — so the moment the two differed, `pagingEnabled` snapped to the
  // wrong place and "Add this printing" named a card other than the one on
  // screen. Silently wrong, and about the worst thing this feature could do.
  const [pagerWidth, setPagerWidth] = useState(0);

  /**
   * How big the card can be and still leave room for the buttons.
   *
   * Sized off the WINDOW rather than fixed at 300, because a card is tall —
   * 745x1040 — and 300 wide is 419 tall before any of the text under it. On
   * a normal handset that plus a name, a type line, two counts and four
   * buttons does not fit, and what fell off the bottom was the buttons.
   *
   * Capped three ways: never wider than 300 (a bigger picture is not a more
   * readable one), never wider than the screen, and never more than about
   * two-fifths of the height.
   */
  const { width: windowWidth, height: windowHeight } = useWindowDimensions();
  const CARD_RATIO = 745 / 1040;
  const artWidth = Math.max(
    140,
    Math.min(300, windowWidth - 96, windowHeight * 0.40 * CARD_RATIO),
  );

  useEffect(() => {
    if (!preview) {
      setVariants([]);
      setShowing(0);
      return;
    }
    setShowing(0);
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
  const [allSets, setAllSets] = useState<CatalogueSet[]>([]);
  const [pickingSet, setPickingSet] = useState(false);
  const [setFilter, setSetFilter] = useState('');
  const [cards, setCards] = useState<CatalogueCard[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  const [searched, setSearched] = useState(false);
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const PAGE = 60;

  const run = useCallback(async () => {
    // Arena-only cards are dropped: you cannot own one, binder one or
    // take one to a table, so in a collection app they are noise.
    const query: CardQuery = { limit: 60, sort, exclude_digital: true };
    const term = name.trim();
    if (term) query.anywhere = term;
    if (colours.length) {
      query.colors = colours;
      query.color_match = 'any';
    }
    if (types.length) query.types = types;
    if (rarities.length) query.rarities = rarities;
    if (sets.length) query.set_codes = sets;
    if (ownedOnly) query.ownership = 'owned';

    // Every field empty would ask the desktop for the entire catalogue.
    if (!term && !colours.length && !types.length &&
        !rarities.length && !sets.length && !ownedOnly) {
      setProblem('Pick a filter, or type part of a name or a rules word.');
      return;
    }

    setBusy(true);
    setProblem('');
    try {
      const reply = await state.searchCards(query);
      setCards(reply.cards ?? []);
      setTotal(reply.total ?? (reply.cards ?? []).length);
      setSearched(true);
    } finally {
      setBusy(false);
    }
  }, [name, colours, types, rarities, sets, ownedOnly, sort, state]);

  // Re-run when a filter changes, but not on every keystroke: the search
  // goes to the PC and typing "Lightning Bolt" would send eleven requests.
  useEffect(() => {
    if (!colours.length && !types.length && !rarities.length && !sets.length &&
        !ownedOnly && !name.trim()) return;
    void run().catch(reporting('searching', setProblem));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colours, types, rarities, sets, ownedOnly, sort]);

  /**
   * The next page, appended.
   *
   * Asking for 419 cards at once would be 419 image requests and a payload
   * nobody scrolls to the end of. Sixty at a time, more as you approach the
   * bottom, and the count says how many there are so "60 of 419" never
   * reads as "419 does not exist".
   */
  const loadMore = useCallback(async () => {
    if (loadingMore || !cards.length || cards.length >= total) return;
    setLoadingMore(true);
    try {
      const query: CardQuery = {
        limit: PAGE,
        offset: cards.length,
        sort,
        exclude_digital: true,
      };
      const term = name.trim();
      if (term) query.anywhere = term;
      if (colours.length) {
        query.colors = colours;
        query.color_match = 'any';
      }
      if (types.length) query.types = types;
      if (rarities.length) query.rarities = rarities;
      if (sets.length) query.set_codes = sets;
      if (ownedOnly) query.ownership = 'owned';

      const reply = await state.searchCards(query);
      // Appended by key rather than concatenated blindly: a page boundary
      // that shifts would otherwise show the same card twice.
      setCards((current) => {
        const seen = new Set(current.map((c) => c.scryfall_id));
        return [...current, ...(reply.cards ?? []).filter((c) => !seen.has(c.scryfall_id))];
      });
    } finally {
      setLoadingMore(false);
    }
  }, [cards.length, total, loadingMore, name, colours, types, rarities,
      sets, ownedOnly, sort, state]);

  useEffect(() => {
    if (!nearEnd) return;
    void loadMore().catch(reporting('loading more', setProblem));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nearEnd]);

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
      {/*
        One box, searching the whole card.
        
        There were two — one for the name, one for the rules text — which
        meant knowing WHICH of them a word lived in before you could look for
        it, and you often do not: "Bolt" is a name, "deathtouch" is rules
        text, and "Goblin" is both. Combining is the user's job now, with
        `&&` and `||`, rather than a decision the layout made for them.
      */}
      <Text style={styles.fieldLabel}>Search — use && and || to combine</Text>
      <View style={styles.header}>
        <TextInput
          style={styles.search}
          placeholder="deathtouch && flying"
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

      {/*
        A window, not a strip. This was a short ScrollView nested inside the
        page's own, so it could not scroll and showed two rows of 335 sets.
        Positioned out of flow it scrolls properly — the same reason the card
        preview works.

        Newest first: alphabetical buries this year's under thirty years of
        others, and the set you want is almost always a recent one.
      */}
      {pickingSet ? (
        <View style={styles.setPanel}>
          <View style={styles.overlayHead}>
            <Text style={styles.overlayTitle}>
              Sets{sets.length ? ` — ${sets.length} picked` : ''}
            </Text>
            <Pressable onPress={() => setPickingSet(false)}>
              <Text style={styles.close}>Done</Text>
            </Pressable>
          </View>
          <TextInput
            style={styles.search}
            placeholder="Filter by set code..."
            placeholderTextColor="#8a8f9c"
            value={setFilter}
            onChangeText={setSetFilter}
            autoCorrect={false}
            autoCapitalize="characters"
          />
          {/*
            Every set at once, wrapped, with NO scroller of its own.
            
            Three attempts at this were a scroller inside the page's
            scroller, and each one showed the half-dozen rows that fitted and
            refused to move — a nested vertical ScrollView never gets the
            gesture. There is one scroller on this screen and it belongs to
            the page. Anything that needs to be reachable has to be laid out,
            not scrolled.
          */}
          <View style={styles.setGrid}>
            {allSets
              .filter((entry) =>
                entry.set_code
                  .toLowerCase()
                  .includes(setFilter.trim().toLowerCase()),
              )
              .map((entry) => {
                const on = sets.includes(entry.set_code);
                return (
                  <Pressable
                    key={entry.set_code}
                    style={[styles.setTile, on && styles.setTileOn]}
                    onPress={() => setSets((list) => toggle(list, entry.set_code))}
                  >
                    <Text style={[styles.setTileCode, on && styles.setTileCodeOn]}>
                      {entry.set_code.toUpperCase()}
                    </Text>
                    <Text style={styles.setTileCount}>{entry.cards}</Text>
                  </Pressable>
                );
              })}
            {allSets.length === 0 ? (
              <Text style={styles.muted}>Asking your PC for the set list...</Text>
            ) : null}
          </View>
        </View>
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

      {cards.length ? (
        <Text style={styles.muted}>
          {cards.length === total
            ? `${total} card${total === 1 ? '' : 's'}`
            : `${cards.length} of ${total} — keep scrolling for more`}
        </Text>
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

      {cards.length && cards.length < total ? (
        <Pressable
          style={styles.more}
          disabled={loadingMore}
          onPress={() => void loadMore().catch(reporting('loading more', setProblem))}
        >
          <Text style={styles.moreText}>
            {loadingMore ? 'Loading…' : `Show more (${total - cards.length} left)`}
          </Text>
        </Pressable>
      ) : null}

      {/*
        The card, big enough to read, floating over the grid.

        A `Modal`, not a panel in the page, and not an absolutely-positioned
        box either — both of those have been tried and each fails a different
        way. Inline, it sat at the very bottom of a page that KEEPS GROWING:
        tap a card, scroll on, another sixty results load, and the thing you
        opened is now further away than when you started. Absolute inside the
        browser was worse — the browser has no height of its own, so the box
        collapsed and cut its own buttons off.

        A Modal has neither problem, because it does not live in this page's
        layout at all. It also owns its own scroller, so a tall card is
        reachable without breaking the one-scroller-per-screen rule, and the
        Android back button closes it instead of leaving the deck.
      */}
      <Modal
        visible={!!preview}
        transparent
        animationType="fade"
        onRequestClose={() => setPreview(null)}
        statusBarTranslucent
      >
        {preview ? (
        <View style={styles.backdrop}>
          {/*
            Tap-to-dismiss sits BEHIND the card, not wrapped around it.

            Wrapped, it broke the swipe: a Pressable is a touch responder, and
            two of them around a horizontal ScrollView claim the pan before
            the pager ever sees it — so the printings could be looked at and
            not swiped through, which is the half that does not help. Behind,
            the dark area is still a way out and the card carries no responder
            of its own, so the gesture reaches the pager.
          */}
          <Pressable
            style={StyleSheet.absoluteFill}
            onPress={() => setPreview(null)}
          />
          <View style={styles.previewWrap}>
          {/*
            The picture, with NO vertical scroller above it.

            The pager used to sit inside one, and a horizontal ScrollView
            nested in a vertical one is a coin toss for which gets the pan on
            Android — which is why the printings would not swipe. The art is
            sized to fit the window now, so it never needed to scroll anyway;
            only the words under it might, and they are what scrolls.
          */}
          <View style={styles.previewTop}>
            {variants.length > 1 ? (
              <ScrollView
                horizontal
                pagingEnabled
                showsHorizontalScrollIndicator={false}
                style={styles.pager}
                onLayout={(event) =>
                  setPagerWidth(event.nativeEvent.layout.width)
                }
                onMomentumScrollEnd={(event) => {
                  // Which page settled, from the offset over the page width.
                  // React Native has no "current page" for a paging
                  // ScrollView, and the alternative — remembering which tile
                  // was tapped — cannot follow a swipe.
                  const width =
                    pagerWidth || event.nativeEvent.layoutMeasurement.width || 1;
                  const at = Math.round(event.nativeEvent.contentOffset.x / width);
                  setShowing(Math.max(0, Math.min(variants.length - 1, at)));
                }}
              >
                {variants.map((printing) => (
                  <View
                    key={printing.printing_id}
                    // One page IS one viewport, so a swipe moves exactly one
                    // printing and the index above is the one on screen.
                    style={[styles.page, pagerWidth ? { width: pagerWidth } : null]}
                  >
                    <Image
                      source={artSource(printing.printing_id, 'large')}
                      style={[styles.previewArt, { width: artWidth }]}
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
                style={[styles.previewArt, { width: artWidth }]}
                resizeMode="contain"
              />
            )}
            {variants.length > 1 ? (
              <Text style={styles.muted}>
                {variants.length} printings — swipe to see them
              </Text>
            ) : null}
          </View>

          {/* Only the words scroll, and only when there are too many of them.
              flexShrink lets the footer claim its height first, so the
              buttons are laid out before this gets what is left. */}
          <ScrollView
            style={styles.previewScroll}
            contentContainerStyle={styles.previewWords}
          >
            <Text style={styles.previewName}>{preview.name}</Text>
            <Text style={styles.muted}>{preview.type_line}</Text>
            {countFor ? (
              <Text style={styles.muted}>
                {countFor(preview)} in here already
              </Text>
            ) : null}
            {countForPrinting && variants[showing] ? (
              <Text style={styles.muted}>
                {countForPrinting(variants[showing].printing_id)} of them this
                printing
              </Text>
            ) : null}

          </ScrollView>

          {/*
            The buttons sit OUTSIDE the scroller, always on screen.

            They were inside it, and on a normal handset the card pushed them
            past the bottom edge — so the one thing the screen exists for was
            the one thing you had to go looking for. An action you have to
            scroll to find is an action that looks missing.
          */}
          <View style={styles.previewFooter}>
            {/*
              Two ways to add, and the difference between them is the whole
              point of the swipe.

              Plain Add records a NAME, which is what a deck slot has always
              meant and what an import is: any copy of Sol Ring fills it. Add
              this printing records the exact card on screen, which is how you
              say the full-art one and not the cheap one — and it is what makes
              the deck's value the deck's value rather than an estimate.

              Explicit rather than inferred from the swipe. Making the picture
              on screen silently decide would turn every add into an exact one
              the moment a card had more than one printing, and nobody asked
              for that.
            */}
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
                <Text style={styles.previewAddText}>
                  {variants.length > 1 ? 'Add any' : 'Add'}
                </Text>
              </Pressable>
              {variants.length > 1 && variants[showing] ? (
                <Pressable
                  style={[styles.previewButton, styles.previewExact]}
                  onPress={() => void onPick(preview, variants[showing])}
                >
                  <Text style={styles.previewAddText}>
                    Add this printing (
                    {variants[showing].set_code.toUpperCase()}{' '}
                    {variants[showing].collector_number})
                  </Text>
                </Pressable>
              ) : null}
            </View>
            {/* Stays open on purpose: adding a second copy is one more tap
                rather than finding the card again. Tap outside to leave. */}
          </View>
          </View>
        </View>
        ) : null}
      </Modal>
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
  fieldLabel: {
    color: '#8a8f9c',
    fontSize: 11,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
    flexShrink: 0,
  },
  more: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 13,
    alignItems: 'center',
  },
  moreText: { color: '#e4e6eb', fontSize: 15 },
  // Dimmed, and covering everything. The grid stays visible behind it so it
  // is obvious what you are on top of, and the dark makes the card the only
  // lit thing on the screen — which is the point of opening it.
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.78)',
    justifyContent: 'center',
    padding: 16,
  },
  previewWrap: {
    backgroundColor: '#151922',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 12,
    // Never taller than the screen. Without a cap a card with four buttons
    // under it can run off the bottom on a short handset, and the button
    // that runs off is the last one — which is the exact-printing one.
    maxHeight: '92%',
  },
  // flexShrink, not flex: the footer is laid out at its natural height and
  // the card takes what is left. flex: 1 would push the buttons off again.
  previewScroll: { flexShrink: 1 },
  previewTop: { paddingTop: 16, paddingHorizontal: 16, gap: 6, alignItems: 'center' },
  previewWords: { padding: 16, paddingTop: 8, gap: 6, alignItems: 'center' },
  previewFooter: {
    padding: 12,
    paddingTop: 6,
    borderTopWidth: 1,
    borderTopColor: '#2d3142',
  },
  pager: { width: '100%' },
  // The width is overridden with the pager's measured width at render time;
  // this is only the fallback for the first frame, before onLayout fires.
  page: { width: 300, alignItems: 'center', gap: 6, paddingHorizontal: 6 },
  // The width comes from the window at render time; see `artWidth`.
  previewArt: { aspectRatio: 745 / 1040, borderRadius: 12 },
  previewName: { color: '#e4e6eb', fontSize: 18, fontWeight: '700' },
  // Wraps, because there are four buttons once a card has more than one
  // printing and a fixed row would push the last one off the edge — which on
  // this screen would be the exact-printing one, the only one that is new.
  previewButtons: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    gap: 8,
  },
  previewButton: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 22,
    paddingVertical: 12,
  },
  previewButtonText: { color: '#e4e6eb', fontSize: 15 },
  previewAdd: { backgroundColor: '#38a169', borderColor: '#38a169' },
  // Outlined rather than filled: it sits beside Add and the two are different
  // choices, not a primary and a secondary. A second green block would read
  // as the same button twice.
  previewExact: { borderColor: '#38a169', paddingHorizontal: 14 },
  previewAddText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  setPanel: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    gap: 8,
  },
  setGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  setTile: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    minWidth: 68,
    alignItems: 'center',
    flexShrink: 0,
  },
  setTileOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  setTileCode: { color: '#c9ced9', fontSize: 13, fontWeight: '700' },
  setTileCodeOn: { color: '#ffffff' },
  setTileCount: { color: '#8a8f9c', fontSize: 10 },
  overlayHead: { flexDirection: 'row', alignItems: 'center' },
  overlayTitle: { color: '#e4e6eb', fontSize: 17, fontWeight: '700', flex: 1 },
  setRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 11,
    paddingHorizontal: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#1a1d27',
  },
  setRowOn: { backgroundColor: '#16241c' },
  setCode: { color: '#c9ced9', fontSize: 15, flex: 1, fontWeight: '600' },
  setCodeOn: { color: '#68d391' },
  problem: { color: '#e53e3e', fontSize: 12, lineHeight: 18 },
});
