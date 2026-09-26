/**
 * The deck analysis, readable on a phone.
 *
 * Reported as: "Analyse on my PC" printed a code block — the raw JSON the
 * PC sent. The fixture is a real analysis of a real deck (Etrata -
 * Assassins, a Dimir commander deck), so these check the sheet against
 * what the PC actually says rather than an invented shape.
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';

import { analysisView } from '../src/lib/analysis-view.ts';

const ETRATA = JSON.parse(
  readFileSync(new URL('./fixtures/analysis-etrata.json', import.meta.url), 'utf-8'),
);

describe('the analysis sheet', () => {
  const v = analysisView(ETRATA);

  test('says what deck it is about, in words', () => {
    assert.equal(v.title, 'Etrata - Assassins');
    assert.equal(v.subtitle, 'Commander · Stax');
  });

  test('leads with power, out of ten, with its parts', () => {
    assert.equal(v.power.overall, ETRATA.power.overall);
    assert.equal(v.power.tier, 'Focused');
    assert.equal(v.power.bars.length, 6);
    for (const bar of v.power.bars) assert.equal(bar.max, 10);
  });

  test('puts what to fix together, warnings first', () => {
    assert.ok(v.fix.length >= ETRATA.issues.length);
    assert.equal(v.fix[0].severity, 'warning');
    assert.match(v.fix[0].text, /ramp/i);
    // Advice from both places the PC gives it, once each.
    const texts = v.fix.map((f) => f.text);
    assert.equal(new Set(texts).size, texts.length);
    for (const r of ETRATA.recommendations) assert.ok(texts.includes(r));
  });

  test('the curve keeps every step from 1 to 6, so gaps show as gaps', () => {
    const mvs = v.curve.map((c) => c.mv);
    for (const mv of ['1', '2', '3', '4', '5', '6']) assert.ok(mvs.includes(mv), mv);
    assert.equal(v.curveMax, 22);
    const total = v.curve.reduce((s, c) => s + c.count, 0);
    const expected = Object.values(ETRATA.mana_curve).reduce((s, n) => s + n, 0);
    assert.equal(total, expected);
  });

  test('colours are only the ones the deck casts, pips against sources', () => {
    assert.deepEqual(v.colours.map((c) => c.colour), ['U', 'B']);
    const black = v.colours.find((c) => c.colour === 'B');
    assert.equal(black.pips, ETRATA.color_distribution.B);
    assert.equal(black.sources, ETRATA.color_sources.B);
    // 30 sources for 73 black symbols is the strain the PC also warns about.
    assert.equal(black.tone, 'weak');
  });

  test('types are sorted biggest first', () => {
    const counts = v.types.map((t) => t.value);
    assert.deepEqual(counts, [...counts].sort((a, b) => b - a));
    assert.equal(v.types[0].label, 'Land');
  });

  test('the cards that need attention are named', () => {
    assert.equal(v.hardToCast[0].name, 'Lost in the Maze');
    assert.match(v.hardToCast[0].chance, /^\d+% on curve$/);
    assert.equal(v.staples.missing[0].name, 'Sol Ring');
    assert.ok(v.synergies.length > 0);
  });

  test('nothing in it is left as a raw object', () => {
    const walk = (x) => {
      if (Array.isArray(x)) return x.forEach(walk);
      if (x && typeof x === 'object') return Object.values(x).forEach(walk);
      if (typeof x === 'string') assert.ok(!x.includes('[object Object]'), x);
    };
    walk(v);
  });
});

describe('a payload from an older or smaller PC', () => {
  test('an empty analysis draws an empty sheet, not a crash', () => {
    const v = analysisView({});
    assert.equal(v.title, 'Deck');
    assert.equal(v.power, null);
    assert.equal(v.staples, null);
    assert.deepEqual(v.fix, []);
    assert.deepEqual(v.colours, []);
  });

  test('garbage in any field is ignored, not thrown', () => {
    const v = analysisView({ power: 'x', issues: 5, mana_curve: null, color_distribution: [] });
    assert.equal(v.power, null);
    assert.deepEqual(v.fix, []);
  });

  test('numbers sent as strings still count', () => {
    const v = analysisView({ mana_curve: { 2: '4' }, total_cards: '100' });
    assert.equal(v.curve.find((c) => c.mv === '2').count, 4);
    assert.equal(v.glance[0].value, '100');
  });
});
