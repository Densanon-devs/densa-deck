/**
 * The decks that live on your PC, and making a new one out of a shelf.
 *
 * Two things the phone could not reach, for different reasons.
 *
 * **Decks saved on the PC** were addressable the whole time — `decks/list`
 * and `decks/get` have been on the bridge since it was written — and nothing
 * on the phone asked. The Decks tab shows the phone's OWN decks, which are a
 * genuinely different set: built in a shop, no versions, no analysis. Both
 * are real, so both are listed, and this screen is the other half.
 *
 * **Building out of a collection** is new. Every other suggestion path in the
 * engine reaches for the whole catalogue, which answers "what should I buy";
 * this answers the question you ask standing over the box, which is "make me
 * something out of THIS". The deck is arithmetic against the pool rather than
 * a model, so it comes out the same twice and works with nothing loaded — the
 * analyst is for explaining it afterwards.
 *
 * Everything here needs the desktop. Said plainly rather than dressed up: the
 * pool has to be judged against 34,000 cards for colour and legality, and
 * there is no honest offline answer.
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
import { DeckStore, parseDecklist } from '../lib/decks.ts';
import type { Deck } from '../lib/decks.ts';
import type {
  BuiltDeck,
  DesktopDeck,
  DesktopDeckDetail,
} from '../lib/protocol.ts';
// The STORE's row, not the protocol's — `state.collections()` reads the
// phone's own mirror, and the two shapes differ by the fields only the
// desktop knows.
import type { CollectionRow } from '../lib/store.ts';
import { uuid } from '../lib/uuid.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  decks: DeckStore;
  onOpenLocal: (deckId: string) => void;
}

const FORMATS = ['commander', 'modern', 'standard', 'pauper', 'legacy'];

export function PcDecksScreen({ state, decks, onOpenLocal }: Props) {
  const [rows, setRows] = useState<DesktopDeck[] | null>(null);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<DesktopDeckDetail | null>(null);
  const [analysis, setAnalysis] = useState('');
  const [thinking, setThinking] = useState(false);

  // Building from a shelf.
  const [shelves, setShelves] = useState<CollectionRow[]>([]);
  const [pickedShelf, setPickedShelf] = useState('');
  const [format, setFormat] = useState('commander');
  const [commander, setCommander] = useState('');
  const [built, setBuilt] = useState<BuiltDeck | null>(null);
  const [building, setBuilding] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setProblem('');
    try {
      setRows(await state.desktopDecks());
    } finally {
      setBusy(false);
    }
  }, [state]);

  useEffect(() => {
    void load().catch(reporting('your PC decks', setProblem));
    // The shelf list comes from the phone's own mirror, so the picker is
    // there even before the PC answers.
    void state.collections().then(setShelves).catch(() => setShelves([]));
  }, [load, state]);

  const openDeck = useCallback(
    async (deck: DesktopDeck) => {
      setAnalysis('');
      setProblem('');
      setOpen(await state.desktopDeck(deck.deck_id));
    },
    [state],
  );

  /** Ask the PC to think about the deck it is already holding. */
  const analyse = useCallback(
    async (text: string, name: string) => {
      setThinking(true);
      setAnalysis('');
      try {
        setAnalysis(JSON.stringify(await state.analyze(text, name), null, 2));
      } catch (err) {
        setProblem(
          `${(err as Error).message}. Analysis runs on your PC — it needs the ` +
            'card database, so it only works when your PC is reachable.',
        );
      } finally {
        setThinking(false);
      }
    },
    [state],
  );

  /**
   * Take a copy of a PC deck to edit here.
   *
   * A COPY, with its own id, rather than something that edits the PC's deck
   * from a distance. The desktop's decks are versioned and this phone's are
   * not, and quietly writing into a version history from a handset is how you
   * lose the thing versions exist to protect.
   */
  const copyDown = useCallback(
    async (detail: DesktopDeckDetail) => {
      const text =
        detail.decklist_text ||
        Object.entries(detail.decklist ?? {})
          .map(([name, qty]) => `${qty} ${name}`)
          .join('\n');
      const { cards, sideboard } = parseDecklist(text);
      const copy: Deck = {
        deck_id: uuid(),
        name: `${detail.name || detail.deck_id} (from PC)`,
        format: detail.format || '',
        decklist: cards,
        sideboard,
        notes: '',
        updated_at: new Date().toISOString(),
      };
      await decks.save(copy);
      onOpenLocal(copy.deck_id);
    },
    [decks, onOpenLocal],
  );

  const build = useCallback(async () => {
    if (!pickedShelf) return;
    setBuilding(true);
    setBuilt(null);
    setProblem('');
    try {
      setBuilt(await state.buildFromCollection(pickedShelf, format, commander));
    } catch (err) {
      setProblem((err as Error).message);
    } finally {
      setBuilding(false);
    }
  }, [state, pickedShelf, format, commander]);

  /** Keep a built deck as a deck on this phone, so it can be edited. */
  const keepBuilt = useCallback(async () => {
    if (!built) return;
    const { cards, sideboard } = parseDecklist(built.decklist_text);
    const shelf = shelves.find((c) => c.collection_uid === pickedShelf);
    const deck: Deck = {
      deck_id: uuid(),
      name: `${shelf?.name ?? 'Collection'} deck`,
      format: built.format,
      decklist: cards,
      sideboard,
      notes: '',
      updated_at: new Date().toISOString(),
    };
    await decks.save(deck);
    onOpenLocal(deck.deck_id);
  }, [built, decks, shelves, pickedShelf, onOpenLocal]);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>On your PC</Text>

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {/* ---------------------------------------- build from a collection */}
      <Text style={styles.section}>Build one from a shelf</Text>
      <Text style={styles.muted}>
        Uses only the cards in that collection — nothing you don’t own. It
        says what it couldn’t fill rather than quietly handing you a deck
        with no lands in it.
      </Text>

      <View style={styles.chipRow}>
        {shelves.map((shelf) => {
          const on = pickedShelf === shelf.collection_uid;
          return (
            <Pressable
              key={shelf.collection_uid}
              style={[styles.chip, on && styles.chipOn]}
              onPress={() => setPickedShelf(on ? '' : shelf.collection_uid)}
            >
              <Text style={[styles.chipText, on && styles.chipTextOn]}>
                {shelf.name} ({shelf.cards})
              </Text>
            </Pressable>
          );
        })}
      </View>

      {pickedShelf ? (
        <>
          <View style={styles.chipRow}>
            {FORMATS.map((f) => (
              <Pressable
                key={f}
                style={[styles.chip, format === f && styles.chipOn]}
                onPress={() => setFormat(f)}
              >
                <Text
                  style={[styles.chipText, format === f && styles.chipTextOn]}
                >
                  {f}
                </Text>
              </Pressable>
            ))}
          </View>
          {format === 'commander' ? (
            <TextInput
              style={styles.input}
              value={commander}
              onChangeText={setCommander}
              placeholder="Commander (optional — sets the colours)"
              placeholderTextColor="#8a8f9c"
              autoCorrect={false}
            />
          ) : null}
          <Pressable style={styles.primary} onPress={() => void build()}>
            <Text style={styles.primaryText}>
              {building ? 'Your PC is building…' : 'Build a deck from this'}
            </Text>
          </Pressable>
        </>
      ) : null}

      {building ? <ActivityIndicator color="#48bb78" /> : null}

      {built ? (
        <View style={styles.built}>
          <Text style={styles.builtTitle}>
            {built.total_cards} of {built.target_size} cards
            {built.colors.length ? ` · ${built.colors.join('')}` : ''}
          </Text>
          {built.commander ? (
            <Text style={styles.muted}>Commander: {built.commander}</Text>
          ) : null}
          <Text style={styles.muted}>
            {built.playable_in_colors} of {built.pool_size} cards in that
            collection are legal in these colours.
          </Text>

          {/* The report is as much the point as the list. */}
          {built.roles.map((role) => (
            <Text
              key={role.role}
              style={role.short ? styles.roleShort : styles.roleOk}
            >
              {role.role}: {role.filled} of {role.wanted}
              {role.short ? ` — ${role.short} short` : ''}
            </Text>
          ))}
          {built.short_by ? (
            <Text style={styles.roleShort}>
              {built.short_by} cards short of a legal deck. That collection
              doesn’t hold enough to finish it.
            </Text>
          ) : null}

          <View style={styles.row}>
            <Pressable
              style={[styles.secondary, styles.grow]}
              onPress={() => void keepBuilt()}
            >
              <Text style={styles.secondaryText}>Keep as a deck</Text>
            </Pressable>
            <Pressable
              style={[styles.secondary, styles.grow]}
              onPress={() =>
                void analyse(built.decklist_text, 'Built from your collection')
              }
            >
              <Text style={styles.secondaryText}>Analyse it</Text>
            </Pressable>
          </View>
          <Text style={styles.list} selectable>
            {built.decklist_text}
          </Text>
        </View>
      ) : null}

      {/* ------------------------------------------------ decks on the PC */}
      <Text style={styles.section}>Decks saved on your PC</Text>
      {busy && rows === null ? (
        <Text style={styles.muted}>Asking your PC…</Text>
      ) : null}
      {rows !== null && rows.length === 0 ? (
        <Text style={styles.muted}>
          No decks saved on your PC yet. Save one there and it will show up
          here.
        </Text>
      ) : null}

      {(rows ?? []).map((deck) => (
        <Pressable
          key={deck.deck_id}
          style={styles.result}
          onPress={() => void openDeck(deck).catch(reporting('that deck', setProblem))}
        >
          <View style={styles.grow}>
            <Text style={styles.name}>{deck.name || deck.deck_id}</Text>
            <Text style={styles.muted}>
              {deck.format || 'no format'}
              {deck.versions ? ` · ${deck.versions} versions` : ''}
            </Text>
          </View>
          <Text style={styles.plus}>›</Text>
        </Pressable>
      ))}

      {open ? (
        <View style={styles.built}>
          <Text style={styles.builtTitle}>{open.name || open.deck_id}</Text>
          <Text style={styles.muted}>
            {open.format || 'no format'}
            {open.version_number ? ` · version ${open.version_number}` : ''}
          </Text>
          <View style={styles.row}>
            <Pressable
              style={[styles.secondary, styles.grow]}
              onPress={() =>
                void analyse(
                  open.decklist_text ||
                    Object.entries(open.decklist ?? {})
                      .map(([n, q]) => `${q} ${n}`)
                      .join('\n'),
                  open.name || 'Deck',
                )
              }
              disabled={thinking}
            >
              <Text style={styles.secondaryText}>
                {thinking ? 'Thinking…' : 'Analyse on my PC'}
              </Text>
            </Pressable>
            <Pressable
              style={[styles.secondary, styles.grow]}
              onPress={() =>
                void copyDown(open).catch(reporting('copying it down', setProblem))
              }
            >
              <Text style={styles.secondaryText}>Copy to phone</Text>
            </Pressable>
          </View>
          <Pressable onPress={() => setOpen(null)}>
            <Text style={styles.muted}>Close</Text>
          </Pressable>
        </View>
      ) : null}

      {thinking ? <ActivityIndicator color="#48bb78" /> : null}
      {analysis ? <Text style={styles.analysis}>{analysis}</Text> : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 16, gap: 10, paddingBottom: 60 },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  problem: { color: '#ecc94b', lineHeight: 20 },
  section: {
    color: '#8a8f9c',
    textTransform: 'uppercase',
    fontSize: 12,
    letterSpacing: 1,
    marginTop: 14,
  },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  chip: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 7,
  },
  chipOn: { backgroundColor: '#38a169', borderColor: '#38a169' },
  chipText: { color: '#c9ced9', fontSize: 12 },
  chipTextOn: { color: '#ffffff', fontWeight: '700' },
  input: {
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    color: '#e4e6eb',
    paddingHorizontal: 12,
    paddingVertical: 10,
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
    padding: 12,
    alignItems: 'center',
  },
  secondaryText: { color: '#e4e6eb' },
  row: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  grow: { flex: 1 },
  result: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#2d3142',
  },
  name: { color: '#e4e6eb', flex: 1 },
  plus: { color: '#48bb78', fontSize: 22, fontWeight: '700', paddingLeft: 8 },
  built: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    gap: 6,
  },
  builtTitle: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  roleOk: { color: '#68d391', fontSize: 13 },
  roleShort: { color: '#ecc94b', fontSize: 13, lineHeight: 19 },
  list: {
    color: '#c9ced9',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
  analysis: {
    color: '#8a8f9c',
    fontFamily: 'monospace',
    fontSize: 11,
    lineHeight: 16,
  },
});
