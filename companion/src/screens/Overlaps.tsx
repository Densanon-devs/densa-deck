/**
 * Cards that are in more than one list at once.
 *
 * Collections are filters, not boxes, so a card being in several is normal and
 * often deliberate: part of a set you are completing, in a deck you have
 * built, and among the seventy-five you took to a tournament. It never moved;
 * three lists mention it.
 *
 * But that makes one situation invisible that used to be impossible, and it is
 * the reason this screen exists. If two decks both expect a card and you own
 * one copy, nothing anywhere says so — you find out at the table, holding a
 * deck box with a hole in it. Those are listed first and marked.
 *
 * Needs the desktop: the counting is over the whole collection and the phone
 * mirrors what you own, not every relationship between lists.
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { AppState } from '../lib/app-state.ts';
import type { OverlapCard } from '../lib/protocol.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
}

export function OverlapsScreen({ state }: Props) {
  const [cards, setCards] = useState<OverlapCard[] | null>(null);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);
  /**
   * Edits this phone has made that the PC has not seen.
   *
   * This screen shows the PC's answer while the Cards tab shows the phone's
   * own mirror, and when those two disagree the screen looked simply wrong —
   * "it thinks I have more than I do". Clearing cards here and not syncing is
   * exactly how that happens, and nothing on the screen said which machine it
   * was describing.
   */
  const [pending, setPending] = useState(0);

  useEffect(() => state.subscribe((snapshot) => setPending(snapshot.pendingEdits)),
            [state]);

  const load = useCallback(async () => {
    setBusy(true);
    setProblem('');
    try {
      // Push what this phone knows BEFORE asking the PC what it thinks.
      //
      // This screen asks the PC a question about data the phone may have
      // newer information about — cards removed here that have not reached it
      // yet. Asking first showed the PC's stale answer, which reads as the
      // app claiming you own things you have just got rid of. Best-effort:
      // with no signal there is nothing to push and the PC's last answer is
      // still the best one available, so the banner explains it instead.
      if (await state.pendingCount()) {
        await state.sync().catch(() => undefined);
      }
      const reply = await state.overlaps();
      setCards(reply.cards ?? []);
    } finally {
      setBusy(false);
    }
  }, [state]);

  useEffect(() => {
    void load().catch(reporting('the overlaps', setProblem));
  }, [load]);

  const contested = (cards ?? []).filter((c) => c.overcommitted);
  const shared = (cards ?? []).filter((c) => !c.overcommitted);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={busy}
          onRefresh={() => void load().catch(reporting('the overlaps', setProblem))}
          tintColor="#e4e6eb"
        />
      }
    >
      <Text style={styles.title}>In more than one list</Text>
      <Text style={styles.muted}>
        Counted on your PC, over your whole collection — the phone mirrors
        what you own, not how the lists overlap.
      </Text>

      {pending > 0 ? (
        <Text style={styles.stale}>
          {pending} change{pending === 1 ? '' : 's'} on this phone haven’t
          reached your PC yet, so this list is one sync behind. Cards you’ve
          removed here can still appear until it catches up.
        </Text>
      ) : null}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {cards === null ? (
        <Text style={styles.muted}>Asking your PC…</Text>
      ) : null}

      {cards !== null && cards.length === 0 && !problem ? (
        <Text style={styles.muted}>
          Nothing is in two lists yet. Once a card is in a set you are
          completing AND a deck, it will show here — along with anything two
          decks are both counting on.
        </Text>
      ) : null}

      {contested.length ? (
        <View style={styles.section}>
          <Text style={styles.warnTitle}>
            {contested.length} counted on more than once
          </Text>
          <Text style={styles.muted}>
            More lists want these than you own copies. Nothing is broken today;
            you would find out at the table.
          </Text>
          {contested.map((card) => (
            <Row key={card.item_id} card={card} warn />
          ))}
        </View>
      ) : null}

      {shared.length ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Doing more than one job</Text>
          <Text style={styles.muted}>
            You own enough copies for every list that mentions them.
          </Text>
          {shared.map((card) => (
            <Row key={card.item_id} card={card} />
          ))}
        </View>
      ) : null}
    </ScrollView>
  );
}

function Row({ card, warn = false }: { card: OverlapCard; warn?: boolean }) {
  return (
    <View style={[styles.row, warn && styles.rowWarn]}>
      <View style={styles.grow}>
        <Text style={styles.name}>
          {card.card_name}
          {card.finish === 'foil' ? ' (foil)' : ''}
        </Text>
        <Text style={styles.lists}>{card.collections.join('  ·  ')}</Text>
      </View>
      <Text style={[styles.count, warn && styles.countWarn]}>
        {card.collection_count} / {card.quantity}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 14, gap: 14, paddingBottom: 40 },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  section: { gap: 8 },
  sectionTitle: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  warnTitle: { color: '#ecc94b', fontSize: 16, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  stale: { color: '#ecc94b', fontSize: 13, lineHeight: 19 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  rowWarn: { borderColor: '#ecc94b' },
  grow: { flex: 1 },
  name: { color: '#e4e6eb', fontSize: 15 },
  lists: { color: '#8a8f9c', fontSize: 12, marginTop: 2 },
  // "3 / 1" reads as three lists want it and you have one, which is the
  // whole message in two numbers.
  count: { color: '#8a8f9c', fontSize: 15, fontWeight: '700' },
  countWarn: { color: '#ecc94b' },
  problem: { color: '#e53e3e', fontSize: 13, lineHeight: 19 },
});
