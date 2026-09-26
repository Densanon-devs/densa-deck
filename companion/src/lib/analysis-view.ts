/**
 * The PC's deck analysis, shaped for reading on a phone.
 *
 * The phone used to print the analysis as raw JSON — braces, quoted keys,
 * a colour distribution nobody can read at a glance. This turns the same
 * payload into the sections the sheet draws, most actionable first: how
 * strong it is, what to fix, then the numbers behind both.
 *
 * Kept free of React so it can be tested against a real payload. Every
 * field is optional and coerced, because the payload comes over the wire
 * from whichever desktop version is on the other end, and a missing block
 * must drop a section, not crash the screen.
 */

export type Tone = 'good' | 'ok' | 'weak';

export interface Bar {
  label: string;
  value: number;      // in the bar's own units
  max: number;
  tone: Tone;
  display: string;    // what to print beside it
}

export interface ColourRow {
  colour: string;     // W U B R G C
  name: string;
  pips: number;       // symbols the spells ask for
  sources: number;    // lands and rocks that make it
  tone: Tone;
  note: string;
}

export interface AnalysisView {
  title: string;
  subtitle: string;
  power: { overall: number; tier: string; bars: Bar[]; up: string[]; down: string[] } | null;
  glance: Array<{ label: string; value: string }>;
  fix: Array<{ severity: 'warning' | 'info'; text: string }>;
  curve: Array<{ mv: string; count: number }>;
  curveMax: number;
  colours: ColourRow[];
  types: Bar[];
  scores: Bar[];
  manaBase: { grade: string; notes: string[] } | null;
  hardToCast: Array<{ name: string; cost: string; chance: string; colour: string }>;
  staples: {
    coverage: string;
    missing: Array<{ name: string; priority: string; reason: string }>;
    present: string[];
  } | null;
  synergies: Array<{ a: string; b: string; reason: string }>;
  unresolved: string[];
}

const COLOUR_NAMES: Record<string, string> = {
  W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colourless',
};
const COLOUR_ORDER = ['W', 'U', 'B', 'R', 'G', 'C'];

const POWER_PARTS: Array<[string, string]> = [
  ['speed', 'Speed'],
  ['interaction', 'Interaction'],
  ['combo_potential', 'Combo potential'],
  ['mana_efficiency', 'Mana efficiency'],
  ['win_condition_quality', 'Win conditions'],
  ['card_quality', 'Card quality'],
];

const SCORE_PARTS: Array<[string, string]> = [
  ['mana_base', 'Mana base'],
  ['curve', 'Curve'],
  ['card_advantage', 'Card advantage'],
  ['interaction', 'Interaction'],
  ['threat_density', 'Threats'],
  ['ramp', 'Ramp'],
];

type Json = Record<string, unknown>;

function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function obj(v: unknown): Json {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Json) : {};
}

function list<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

function tone(fraction: number): Tone {
  if (fraction >= 0.7) return 'good';
  if (fraction >= 0.45) return 'ok';
  return 'weak';
}

function sentence(s: string): string {
  const t = s.trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : t;
}

export function analysisView(raw: unknown): AnalysisView {
  const a = obj(raw);

  const archetype = str(a.archetype);
  const format = str(a.format);

  // ---- power
  const p = obj(a.power);
  const power = Object.keys(p).length
    ? {
        overall: num(p.overall),
        tier: sentence(str(p.tier)),
        bars: POWER_PARTS.filter(([k]) => p[k] !== undefined).map(([k, label]) => ({
          label,
          value: num(p[k]),
          max: 10,
          tone: tone(num(p[k]) / 10),
          display: num(p[k]).toFixed(1),
        })),
        up: list<string>(p.reasons_up).map(str).filter(Boolean),
        down: list<string>(p.reasons_down).map(str).filter(Boolean),
      }
    : null;

  // ---- at a glance
  const glance: AnalysisView['glance'] = [];
  const pushGlance = (label: string, v: unknown, digits = 0) => {
    if (v === undefined || v === null) return;
    glance.push({ label, value: num(v).toFixed(digits) });
  };
  pushGlance('Cards', a.total_cards);
  pushGlance('Lands', a.land_count);
  pushGlance('Avg mana value', a.average_cmc, 2);
  pushGlance('Ramp', a.ramp_count);
  pushGlance('Card draw', a.draw_count);
  pushGlance('Interaction', a.interaction_count);

  // ---- what to fix: warnings first, then the rest, then plain advice
  const issues = list<Json>(a.issues).map((i) => ({
    severity: (str(i.severity) === 'warning' || str(i.severity) === 'error'
      ? 'warning' : 'info') as 'warning' | 'info',
    text: str(i.message) + (i.card ? ` (${str(i.card)})` : ''),
  }));
  issues.sort((x, y) => (x.severity === y.severity ? 0 : x.severity === 'warning' ? -1 : 1));
  const advice = [
    ...list<string>(a.recommendations),
    ...list<string>(obj(a.advanced).advanced_recommendations),
  ].map(str).filter(Boolean);
  const seen = new Set(issues.map((i) => i.text));
  const fix = [
    ...issues,
    ...advice.filter((t) => !seen.has(t)).map((text) => ({ severity: 'info' as const, text })),
  ];

  // ---- curve, 0 .. 7+
  const rawCurve = obj(a.mana_curve);
  const buckets = new Map<string, number>();
  for (const [k, v] of Object.entries(rawCurve)) {
    const mv = Math.floor(num(k));
    const key = mv >= 7 ? '7+' : String(mv);
    buckets.set(key, (buckets.get(key) ?? 0) + num(v));
  }
  const curveKeys = ['0', '1', '2', '3', '4', '5', '6', '7+']
    .filter((k, i, all) => buckets.has(k) || (i > 0 && i < all.length - 1));
  const curve = curveKeys.map((mv) => ({ mv, count: buckets.get(mv) ?? 0 }));
  const curveMax = Math.max(1, ...curve.map((c) => c.count));

  // ---- colours: pips asked for vs sources that make them. Only colours the
  // deck actually casts; a Dimir deck's land base "making" red through a
  // rainbow land is not a red requirement.
  const pips = obj(a.color_distribution);
  const sources = obj(a.color_sources);
  const colours = COLOUR_ORDER.filter((c) => num(pips[c]) > 0).map((c) => {
    const need = num(pips[c]);
    const have = num(sources[c]);
    const ratio = need ? have / need : 1;
    return {
      colour: c,
      name: COLOUR_NAMES[c] ?? c,
      pips: need,
      sources: have,
      // Roughly one source per pip reads as healthy; well under half strains.
      tone: tone(Math.min(1, ratio)),
      note: `${have} source${have === 1 ? '' : 's'} for ${need} symbol${need === 1 ? '' : 's'}`,
    };
  });

  // ---- card types
  const typeEntries = Object.entries(obj(a.type_distribution))
    .map(([label, v]) => ({ label, count: num(v) }))
    .filter((t) => t.count > 0)
    .sort((x, y) => y.count - x.count);
  const typeTotal = Math.max(1, typeEntries.reduce((s, t) => s + t.count, 0));
  const types = typeEntries.map((t) => ({
    label: t.label,
    value: t.count,
    max: typeTotal,
    tone: 'ok' as Tone,
    display: String(t.count),
  }));

  // ---- scores (0-100)
  const sc = obj(a.scores);
  const scores = SCORE_PARTS.filter(([k]) => sc[k] !== undefined).map(([k, label]) => ({
    label,
    value: num(sc[k]),
    max: 100,
    tone: tone(num(sc[k]) / 100),
    display: String(Math.round(num(sc[k]))),
  }));

  // ---- mana base
  const adv = obj(a.advanced);
  const grade = str(adv.mana_base_grade);
  const notes = list<string>(adv.mana_base_notes).map(str).filter(Boolean);
  const manaBase = grade || notes.length ? { grade, notes } : null;

  const hardToCast = list<Json>(obj(a.castability).unreliable_cards).map((c) => ({
    name: str(c.name),
    cost: str(c.mana_cost),
    chance: `${Math.round(num(c.on_curve_probability) * 100)}% on curve`,
    colour: str(c.bottleneck_color),
  }));

  // ---- staples
  const st = obj(a.staples);
  const staples = Object.keys(st).length
    ? {
        coverage: `${Math.round(num(st.staple_coverage) * 100)}%`,
        missing: list<Json>(st.missing).map((m) => ({
          name: str(m.name),
          priority: str(m.priority),
          reason: str(m.reason),
        })),
        present: list<string>(st.present_staples).map(str),
      }
    : null;

  const synergies = list<Json>(adv.synergies)
    .map((s) => ({ a: str(s.card_a), b: str(s.card_b), reason: str(s.reason),
                   strength: num(s.strength) }))
    .sort((x, y) => y.strength - x.strength)
    .map(({ a: ca, b, reason }) => ({ a: ca, b, reason }));

  return {
    title: str(a.deck_name) || 'Deck',
    subtitle: [format, archetype].filter(Boolean).map(sentence).join(' · '),
    power,
    glance,
    fix,
    curve,
    curveMax,
    colours,
    types,
    scores,
    manaBase,
    hardToCast,
    staples,
    synergies,
    unresolved: list<unknown>(a.unresolved_cards).map((u) =>
      typeof u === 'string' ? u : str(obj(u).name)).filter(Boolean),
  };
}
