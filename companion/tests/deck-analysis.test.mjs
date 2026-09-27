/**
 * The full analysis, run once per version of a deck and then kept.
 *
 * Asked for: "it should also do the pro analysis if unlocked, and it should
 * cache results on a version of a deck, so if the deck isn't changed then it
 * should retain the stats, new version clears". And: "the free analysis
 * should still be accessible with a linked not pro phone".
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, test } from 'node:test';

import { ProRequired } from '../src/lib/client.ts';
import {
  addBracket, cacheKey, clearCached, deckSignature, readCached,
  refreshMissing, runAnalysis, writeCached,
} from '../src/lib/deck-analysis.ts';
import {
  bracketView, comboLines, gauntletView, goldfishView,
} from '../src/lib/analysis-view.ts';

const fixture = (name) => JSON.parse(
  readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf-8'));
const BASIC = fixture('analysis-etrata.json');
const PRO = fixture('analysis-etrata-pro.json');

const DECK = 'Commander:\n1 Etrata, the Silencer\n\nMainboard:\n1 Sol Ring\n35 Island\n';

function fakeApi({ pro = true, failing = [] } = {}) {
  const calls = [];
  const answer = (part, value) => async () => {
    calls.push(part);
    if (failing.includes(part)) throw new Error(`${part} broke`);
    return value;
  };
  const proOnly = (part, value) => async () => {
    calls.push(part);
    if (!pro) throw new ProRequired(`${part} is a Densa Deck Pro feature.`);
    if (failing.includes(part)) throw new Error(`${part} broke`);
    return value;
  };
  return {
    calls,
    analyze: answer('basic', BASIC),
    combos: answer('combos', PRO.combos),
    nearMissCombos: answer('nearMiss', PRO.nearMiss),
    goldfish: proOnly('goldfish', PRO.goldfish),
    gauntlet: proOnly('gauntlet', PRO.gauntlet),
    bracketFit: async (_t, target) => ({ ...PRO.bracket, target_label: target }),
  };
}

function memoryIo() {
  const map = new Map();
  return {
    map,
    readAnalysisCache: async (k) => map.get(k),
    writeAnalysisCache: async (k, v) => { map.set(k, v); },
  };
}

describe('a version of a deck', () => {
  test('the same list is the same version, whitespace aside', () => {
    assert.equal(deckSignature(DECK), deckSignature(`  ${DECK}\n\n`));
    assert.equal(deckSignature(DECK), deckSignature(DECK.replace('1 Sol', '1  Sol')));
  });

  test('any real change is a new version', () => {
    assert.notEqual(deckSignature(DECK), deckSignature(DECK.replace('35 Island', '34 Island')));
    assert.notEqual(deckSignature(DECK), deckSignature(DECK.replace('Sol Ring', 'Arcane Signet')));
    assert.notEqual(deckSignature(DECK, 'commander'), deckSignature(DECK, 'modern'));
  });
});

describe('running it', () => {
  test('Pro gets every part, the structural analysis first', async () => {
    const api = fakeApi({ pro: true });
    const a = await runAnalysis(api, DECK, 'Etrata', 'sig', () => {});
    assert.equal(api.calls[0], 'basic');
    for (const part of ['basic', 'combos', 'nearMiss', 'goldfish', 'gauntlet']) {
      assert.ok(a.results[part], part);
    }
    assert.deepEqual(a.locked, []);
  });

  test('a free phone still gets the whole free analysis', async () => {
    const a = await runAnalysis(fakeApi({ pro: false }), DECK, 'Etrata', 'sig', () => {});
    for (const part of ['basic', 'combos', 'nearMiss']) assert.ok(a.results[part], part);
    // The simulations are marked Pro, not reported as failures.
    assert.deepEqual([...a.locked].sort(), ['gauntlet', 'goldfish']);
    assert.deepEqual(a.errors, {});
  });

  test('each part is shown as it lands, not all at the end', async () => {
    const seen = [];
    await runAnalysis(fakeApi(), DECK, 'Etrata', 'sig',
                      (a) => seen.push(Object.keys(a.results).length));
    assert.equal(seen[0], 1);
    assert.equal(seen.at(-1), 5);
  });

  test('one part failing does not blank the rest', async () => {
    const a = await runAnalysis(fakeApi({ failing: ['gauntlet'] }), DECK, 'E', 's', () => {});
    assert.match(a.errors.gauntlet, /broke/);
    assert.ok(a.results.goldfish);
  });

  test('if the structural analysis fails nothing else is asked', async () => {
    const api = fakeApi({ failing: ['basic'] });
    const a = await runAnalysis(api, DECK, 'E', 's', () => {});
    assert.deepEqual(api.calls, ['basic']);
    assert.ok(a.errors.basic);
  });
});

describe('keeping it', () => {
  test('an unchanged deck gets its stats back', async () => {
    const io = memoryIo();
    const sig = deckSignature(DECK);
    const a = await runAnalysis(fakeApi(), DECK, 'E', sig, () => {});
    await writeCached(io, 'local:deck-1', a);

    const back = await readCached(io, 'local:deck-1', deckSignature(`${DECK}\n`));
    assert.ok(back);
    assert.deepEqual(back.results.goldfish, PRO.goldfish);
  });

  test('a new version of the deck does not see the old numbers', async () => {
    const io = memoryIo();
    const a = await runAnalysis(fakeApi(), DECK, 'E', deckSignature(DECK), () => {});
    await writeCached(io, 'local:deck-1', a);
    const edited = DECK.replace('35 Island', '34 Island');
    assert.equal(await readCached(io, 'local:deck-1', deckSignature(edited)), null);
  });

  test('and analysing the new version replaces the old entry', async () => {
    const io = memoryIo();
    await writeCached(io, 'k', await runAnalysis(fakeApi(), DECK, 'E', deckSignature(DECK), () => {}));
    const edited = DECK.replace('35 Island', '34 Island');
    await writeCached(io, 'k', await runAnalysis(fakeApi(), edited, 'E', deckSignature(edited), () => {}));
    assert.equal(io.map.size, 1);
    assert.equal(await readCached(io, 'k', deckSignature(DECK)), null);
    assert.ok(await readCached(io, 'k', deckSignature(edited)));
  });

  test('each deck has its own entry', async () => {
    const io = memoryIo();
    const a = await runAnalysis(fakeApi(), DECK, 'E', deckSignature(DECK), () => {});
    await writeCached(io, 'local:a', a);
    assert.equal(await readCached(io, 'local:b', deckSignature(DECK)), null);
    assert.equal(cacheKey('pc:x'), 'analysis:pc:x');
  });

  test('a deleted deck forgets its analysis', async () => {
    const io = memoryIo();
    await writeCached(io, 'k', await runAnalysis(fakeApi(), DECK, 'E', deckSignature(DECK), () => {}));
    await clearCached(io, 'k');
    assert.equal(await readCached(io, 'k', deckSignature(DECK)), null);
  });

  test('a corrupt cache entry is ignored, not thrown', async () => {
    const io = memoryIo();
    io.map.set(cacheKey('k'), '{not json');
    assert.equal(await readCached(io, 'k', 'sig'), null);
  });

  test('bracket answers are kept with the version they were asked of', async () => {
    const api = fakeApi();
    let a = await runAnalysis(api, DECK, 'E', 'sig', () => {});
    a = await addBracket(api, DECK, a, '3-optimized');
    assert.equal(a.brackets['3-optimized'].target_label, '3-optimized');
  });
});

describe('coming back to a cached analysis', () => {
  test('parts locked when it was run are asked again -- Pro may be bought since', async () => {
    const free = await runAnalysis(fakeApi({ pro: false }), DECK, 'E', 's', () => {});
    const api = fakeApi({ pro: true });
    const now = await refreshMissing(api, DECK, 'E', free, () => {});
    assert.deepEqual([...api.calls].sort(), ['gauntlet', 'goldfish']);
    assert.deepEqual(now.locked, []);
    assert.ok(now.results.goldfish);
  });

  test('a part that failed is retried; the ones that worked are not', async () => {
    const broken = await runAnalysis(fakeApi({ failing: ['combos'] }), DECK, 'E', 's', () => {});
    const api = fakeApi();
    const now = await refreshMissing(api, DECK, 'E', broken, () => {});
    assert.deepEqual(api.calls, ['combos']);
    assert.equal(now.errors.combos, undefined);
    assert.ok(now.results.combos);
  });

  test('a complete analysis asks for nothing', async () => {
    const whole = await runAnalysis(fakeApi(), DECK, 'E', 's', () => {});
    const api = fakeApi();
    await refreshMissing(api, DECK, 'E', whole, () => {});
    assert.deepEqual(api.calls, []);
  });
});

describe('drawing the extra sections from what the PC really sends', () => {
  test('goldfish', () => {
    const g = goldfishView(PRO.goldfish);
    assert.ok(g.tiles.length >= 4);
    assert.ok(g.killTurns.length > 0);
    assert.ok(g.mana.summary);
    assert.equal(goldfishView({}), null);
  });

  test('gauntlet: every archetype, best first', () => {
    const g = gauntletView(PRO.gauntlet);
    assert.equal(g.matchups.length, PRO.gauntlet.matchups.length);
    const rates = g.matchups.map((m) => m.value);
    assert.deepEqual(rates, [...rates].sort((a, b) => b - a));
    assert.equal(g.scores.length, 4);
    assert.equal(gauntletView({}), null);
  });

  test('near-miss combos name the missing card', () => {
    const near = comboLines(PRO.nearMiss, 'near_combos');
    assert.equal(near.length, PRO.nearMiss.near_combos.length);
    assert.ok(near.every((c) => c.missing.length > 0));
  });

  test('bracket fit', () => {
    const b = bracketView(PRO.bracket);
    assert.equal(b.verdict, 'Under delivers');
    assert.equal(b.tone, 'weak');
    assert.match(b.reads, /Optimized/);
    assert.equal(bracketView({ ok: false, error: 'x' }), null);
  });
});
