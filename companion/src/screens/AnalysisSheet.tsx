/**
 * The PC's deck analysis, as something to read rather than parse.
 *
 * Opened from "Analyse on my PC" on both the PC tab and an open deck. It
 * used to print the payload as a JSON block; lib/analysis-view.ts turns it
 * into sections and this draws them, most actionable first.
 */

import React, { useState } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { analysisView, type Bar, type Tone } from '../lib/analysis-view.ts';

const TONE: Record<Tone, string> = { good: '#48bb78', ok: '#ecc94b', weak: '#e53e3e' };
const MANA: Record<string, string> = {
  W: '#f8f3d8', U: '#9cc9ef', B: '#b9aeb0', R: '#f09a7a', G: '#93c8a0', C: '#cfcfcf',
};
const SHOWN = 6;

interface Props {
  analysis: unknown;
  onClose: () => void;
}

export function AnalysisSheet({ analysis, onClose }: Props) {
  const insets = useSafeAreaInsets();
  const v = analysisView(analysis);

  return (
    <Modal visible animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <View style={[styles.sheet, { paddingTop: insets.top + 8 }]}>
        <View style={styles.header}>
          <View style={{ flex: 1 }}>
            <Text style={styles.title} numberOfLines={2}>{v.title}</Text>
            {v.subtitle ? <Text style={styles.muted}>{v.subtitle}</Text> : null}
          </View>
          <Pressable onPress={onClose} style={styles.close} hitSlop={12}>
            <Text style={styles.closeText}>Done</Text>
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={[styles.body, { paddingBottom: insets.bottom + 32 }]}>
          {v.power ? (
            <Section title="Power">
              <View style={styles.powerRow}>
                <Text style={styles.powerNumber}>{v.power.overall.toFixed(1)}</Text>
                <View>
                  <Text style={styles.muted}>out of 10</Text>
                  {v.power.tier ? <Text style={styles.tier}>{v.power.tier}</Text> : null}
                </View>
              </View>
              {v.power.bars.map((b) => <BarRow key={b.label} bar={b} />)}
              {v.power.up.map((r) => <Bullet key={r} mark="▲" colour={TONE.good} text={r} />)}
              {v.power.down.map((r) => <Bullet key={r} mark="▼" colour={TONE.weak} text={r} />)}
            </Section>
          ) : null}

          {v.fix.length ? (
            <Section title="What to look at">
              {v.fix.map((f) => (
                <Bullet
                  key={f.text}
                  mark={f.severity === 'warning' ? '!' : '•'}
                  colour={f.severity === 'warning' ? TONE.ok : '#8a8f9c'}
                  text={f.text}
                />
              ))}
            </Section>
          ) : null}

          {v.glance.length ? (
            <Section title="At a glance">
              <View style={styles.tiles}>
                {v.glance.map((g) => (
                  <View key={g.label} style={styles.tile}>
                    <Text style={styles.tileValue}>{g.value}</Text>
                    <Text style={styles.tileLabel}>{g.label}</Text>
                  </View>
                ))}
              </View>
            </Section>
          ) : null}

          {v.curve.length ? (
            <Section title="Mana curve">
              <View style={styles.curve}>
                {v.curve.map((c) => (
                  <View key={c.mv} style={styles.curveCol}>
                    <Text style={styles.curveCount}>{c.count || ''}</Text>
                    <View style={styles.curveTrack}>
                      <View style={[styles.curveBar,
                        { height: `${(c.count / v.curveMax) * 100}%` }]} />
                    </View>
                    <Text style={styles.curveLabel}>{c.mv}</Text>
                  </View>
                ))}
              </View>
            </Section>
          ) : null}

          {v.colours.length ? (
            <Section title="Colours">
              {v.colours.map((c) => (
                <View key={c.colour} style={styles.colourRow}>
                  <View style={[styles.pip, { backgroundColor: MANA[c.colour] ?? '#cfcfcf' }]}>
                    <Text style={styles.pipText}>{c.colour}</Text>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.text}>{c.name}</Text>
                    <Text style={[styles.small, { color: TONE[c.tone] }]}>{c.note}</Text>
                  </View>
                </View>
              ))}
            </Section>
          ) : null}

          {v.types.length ? (
            <Section title="Card types">
              {v.types.map((b) => <BarRow key={b.label} bar={b} neutral />)}
            </Section>
          ) : null}

          {v.scores.length ? (
            <Section title="Scores">
              {v.scores.map((b) => <BarRow key={b.label} bar={b} />)}
            </Section>
          ) : null}

          {v.manaBase ? (
            <Section title="Mana base">
              {v.manaBase.grade ? (
                <Text style={styles.grade}>Grade {v.manaBase.grade}</Text>
              ) : null}
              {v.manaBase.notes.map((n) => <Bullet key={n} mark="•" colour="#8a8f9c" text={n} />)}
            </Section>
          ) : null}

          {v.hardToCast.length ? (
            <Section title="Hard to cast on curve">
              {v.hardToCast.map((c) => (
                <View key={c.name} style={styles.cardRow}>
                  <Text style={styles.text}>{c.name}</Text>
                  <Text style={styles.small}>{c.cost} · {c.chance}</Text>
                </View>
              ))}
            </Section>
          ) : null}

          {v.staples && (v.staples.missing.length || v.staples.present.length) ? (
            <Section title={`Staples · ${v.staples.coverage} of the list`}>
              <More
                items={v.staples.missing}
                render={(m) => (
                  <View key={m.name} style={styles.cardRow}>
                    <View style={styles.nameLine}>
                      <Text style={styles.text}>{m.name}</Text>
                      {m.priority ? <Text style={styles.badge}>{m.priority}</Text> : null}
                    </View>
                    {m.reason ? <Text style={styles.small}>{m.reason}</Text> : null}
                  </View>
                )}
              />
              {v.staples.present.length ? (
                <Text style={[styles.small, { marginTop: 8 }]}>
                  Already in the deck: {v.staples.present.join(', ')}
                </Text>
              ) : null}
            </Section>
          ) : null}

          {v.synergies.length ? (
            <Section title="Cards that work together">
              <More
                items={v.synergies}
                render={(s) => (
                  <View key={`${s.a}|${s.b}`} style={styles.cardRow}>
                    <Text style={styles.text}>{s.a} + {s.b}</Text>
                    {s.reason ? <Text style={styles.small}>{s.reason}</Text> : null}
                  </View>
                )}
              />
            </Section>
          ) : null}

          {v.unresolved.length ? (
            <Section title="Not recognised">
              <Text style={styles.small}>{v.unresolved.join(', ')}</Text>
            </Section>
          ) : null}
        </ScrollView>
      </View>
    </Modal>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function BarRow({ bar, neutral = false }: { bar: Bar; neutral?: boolean }) {
  const pct = Math.max(0, Math.min(1, bar.max ? bar.value / bar.max : 0));
  return (
    <View style={styles.barRow}>
      <Text style={styles.barLabel} numberOfLines={1}>{bar.label}</Text>
      <View style={styles.barTrack}>
        <View style={[styles.barFill, {
          width: `${pct * 100}%`,
          backgroundColor: neutral ? '#4a90e2' : TONE[bar.tone],
        }]} />
      </View>
      <Text style={styles.barValue}>{bar.display}</Text>
    </View>
  );
}

function Bullet({ mark, colour, text }: { mark: string; colour: string; text: string }) {
  return (
    <View style={styles.bullet}>
      <Text style={[styles.bulletMark, { color: colour }]}>{mark}</Text>
      <Text style={[styles.text, { flex: 1 }]}>{text}</Text>
    </View>
  );
}

function More<T>({ items, render }: { items: T[]; render: (item: T) => React.ReactNode }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <>
      {shown.map(render)}
      {items.length > SHOWN ? (
        <Pressable onPress={() => setAll(!all)} hitSlop={8}>
          <Text style={styles.more}>{all ? 'Show fewer' : `Show all ${items.length}`}</Text>
        </Pressable>
      ) : null}
    </>
  );
}

const styles = StyleSheet.create({
  sheet: { flex: 1, backgroundColor: '#0f1117' },
  header: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 12,
    paddingHorizontal: 20, paddingBottom: 12,
    borderBottomWidth: 1, borderBottomColor: '#2d3142',
  },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 19 },
  close: {
    borderColor: '#2d3142', borderWidth: 1, borderRadius: 10,
    paddingVertical: 8, paddingHorizontal: 14,
  },
  closeText: { color: '#e4e6eb', fontWeight: '600' },
  body: { padding: 20, gap: 14 },
  section: {
    backgroundColor: '#1a1d27', borderColor: '#2d3142', borderWidth: 1,
    borderRadius: 12, padding: 14, gap: 8,
  },
  sectionTitle: {
    color: '#8a8f9c', textTransform: 'uppercase', fontSize: 12,
    letterSpacing: 0.8, fontWeight: '700', marginBottom: 2,
  },
  text: { color: '#e4e6eb', fontSize: 15, lineHeight: 21 },
  small: { color: '#8a8f9c', fontSize: 13, lineHeight: 18 },
  powerRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 4 },
  powerNumber: { color: '#e4e6eb', fontSize: 44, fontWeight: '800' },
  tier: { color: '#e4e6eb', fontSize: 16, fontWeight: '600' },
  barRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  barLabel: { color: '#c9ced9', fontSize: 13, width: 118 },
  barTrack: { flex: 1, height: 8, borderRadius: 4, backgroundColor: '#2d3142', overflow: 'hidden' },
  barFill: { height: '100%', borderRadius: 4 },
  barValue: { color: '#e4e6eb', fontSize: 13, width: 34, textAlign: 'right' },
  bullet: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  bulletMark: { fontSize: 15, lineHeight: 21, width: 14, textAlign: 'center', fontWeight: '800' },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tile: {
    flexBasis: '31%', flexGrow: 1, backgroundColor: '#11151d',
    borderRadius: 10, paddingVertical: 10, alignItems: 'center',
  },
  tileValue: { color: '#e4e6eb', fontSize: 22, fontWeight: '700' },
  tileLabel: { color: '#8a8f9c', fontSize: 12, marginTop: 2 },
  curve: { flexDirection: 'row', alignItems: 'flex-end', gap: 6, height: 150 },
  curveCol: { flex: 1, alignItems: 'center', height: '100%' },
  curveCount: { color: '#c9ced9', fontSize: 12, height: 18 },
  curveTrack: { flex: 1, width: '100%', justifyContent: 'flex-end' },
  curveBar: { width: '100%', backgroundColor: '#4a90e2', borderRadius: 4, minHeight: 2 },
  curveLabel: { color: '#8a8f9c', fontSize: 12, marginTop: 4 },
  colourRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  pip: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  pipText: { color: '#111', fontWeight: '800' },
  grade: { color: '#e4e6eb', fontSize: 18, fontWeight: '700' },
  cardRow: { paddingVertical: 4 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  badge: {
    color: '#ecc94b', fontSize: 11, textTransform: 'uppercase',
    borderColor: '#ecc94b', borderWidth: 1, borderRadius: 6,
    paddingHorizontal: 6, paddingVertical: 1, overflow: 'hidden',
  },
  more: { color: '#4a90e2', fontWeight: '600', marginTop: 4 },
});
