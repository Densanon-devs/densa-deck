/**
 * Searching every card in Magic, from the phone's own index.
 *
 * The desktop searches on far more than this — rules text, format
 * legality, price, printing counts — so it is asked first and this only
 * answers when there is nobody to ask. But "only answers by name" is not
 * good enough: the browser offers colours, types, rarities and sets, and a
 * filter with nothing behind it silently returns nothing, which reads as
 * the app refusing to find a card rather than as data it does not hold.
 *
 * So every filter the browser can send is honoured here, against the two
 * indexes the phone already has. Pure, so all of it is testable in Node.
 */

import type { CardQuery, CatalogueCard } from './protocol.ts';

export interface OracleFacts {
  oracle_id: string;
  name: string;
  type_line: string;
  oracle_text: string;
  mana_cost: string;
  cmc: number | null;
  color_identity: string;
}

export interface PrintingFacts {
  printing_id: string;
  name: string;
  set_code: string;
  collector_number: string;
  cmc: number | null;
  rarity: string;
}

/**
 * Colour letters, however the index happens to store them.
 *
 * The two sources disagree and both are already in the wild: the desktop
 * sends the JSON it holds — `["U", "W"]` — while the Scryfall path joins
 * them into `UW`. Splitting on separators reads the second as one colour
 * called "UW", which matches nothing and made every colour filter come
 * back empty.
 *
 * Keeping only the five colour letters reads both, and cannot be confused
 * by brackets, quotes or commas.
 */
function coloursOf(card: OracleFacts): string[] {
  return (card.color_identity || '')
    .toUpperCase()
    .split('')
    .filter((c) => 'WUBRG'.includes(c));
}

/**
 * Whether a card's colours satisfy the chosen ones.
 *
 * Three modes, because they are three different questions and the deck
 * builder needs all of them: cards you could PLAY in these colours
 * (identity), cards of exactly this combination, and cards touching any of
 * them.
 */
export function coloursMatch(
  card: OracleFacts,
  wanted: string[],
  mode: 'identity' | 'exact' | 'any' = 'identity',
): boolean {
  if (!wanted.length) return true;
  const has = coloursOf(card);
  const want = wanted.map((c) => c.toUpperCase());
  if (mode === 'any') return has.some((c) => want.includes(c));
  if (mode === 'exact') {
    return has.length === want.length && has.every((c) => want.includes(c));
  }
  // Identity: nothing outside the chosen colours. A colourless card fits
  // every deck, which is why an empty list passes rather than failing.
  return has.every((c) => want.includes(c));
}

/**
 * Whether a type line matches any of the wanted types.
 *
 * Substring, case-insensitive, because a type line is prose — "Legendary
 * Creature — Elf Druid" has to match a search for "creature" and for
 * "elf", and neither is a field.
 */
export function typesMatch(card: OracleFacts, wanted: string[]): boolean {
  if (!wanted.length) return true;
  const line = (card.type_line || '').toLowerCase();
  return wanted.some((t) => line.includes(t.trim().toLowerCase()));
}

export interface SearchInputs {
  oracle: OracleFacts[];
  printings: PrintingFacts[];
  /** Lowercased names this phone owns at least one of. */
  owned: Set<string>;
}

/**
 * Run a browser query against the local indexes.
 *
 * Ordering matches the desktop's instinct rather than the database's: a
 * card whose name STARTS with what you typed is what you meant, and one
 * that merely contains it is a coincidence you scroll past.
 */
export function searchLocally(
  query: CardQuery,
  { oracle, printings, owned }: SearchInputs,
  limit = 60,
): CatalogueCard[] {
  const term = String(query.name ?? '').trim().toLowerCase();
  const colours = (query.colors ?? []).filter(Boolean);
  const types = (query.types ?? []).filter(Boolean);
  const rarities = (query.rarities ?? [query.rarity])
    .filter((r): r is string => !!r).map((r) => r.toLowerCase());
  const sets = (query.set_codes ?? [query.set_code])
    .filter((s): s is string => !!s).map((s) => s.toLowerCase());

  // One representative printing per name, and the set/rarity facts that
  // hang off it. A card is in one set per printing, so "either of these
  // sets" is a question about printings rather than about cards.
  const byName = new Map<string, PrintingFacts[]>();
  for (const p of printings) {
    const key = p.name.trim().toLowerCase();
    const list = byName.get(key);
    if (list) list.push(p);
    else byName.set(key, [p]);
  }

  // Nothing asked for is not "everything". The browser guards against this
  // too, but a fallback that answered a blank query with the whole
  // catalogue would hand back thirty-four thousand rows to render.
  const asked = !!term || colours.length || types.length || rarities.length
    || sets.length || query.cmc_min != null || query.cmc_max != null
    || !!query.owned;
  if (!asked) return [];

  const out: Array<{ card: CatalogueCard; at: number }> = [];
  for (const card of oracle) {
    const key = card.name.trim().toLowerCase();

    const at = term ? key.indexOf(term) : 0;
    if (term && at < 0) continue;
    if (!coloursMatch(card, colours, query.color_match ?? 'identity')) continue;
    if (!typesMatch(card, types)) continue;
    if (query.cmc_min != null && (card.cmc ?? -1) < query.cmc_min) continue;
    if (query.cmc_max != null && (card.cmc ?? 99) > query.cmc_max) continue;
    if (query.owned && !owned.has(key)) continue;

    const prints = byName.get(key) ?? [];
    const inSet = sets.length
      ? prints.filter((p) => sets.includes(p.set_code.toLowerCase()))
      : prints;
    if (sets.length && !inSet.length) continue;
    const atRarity = rarities.length
      ? inSet.filter((p) => rarities.includes((p.rarity || '').toLowerCase()))
      : inSet;
    if (rarities.length && !atRarity.length) continue;

    // The printing that satisfied the filters, so the art shown is of a
    // copy that actually matches what was asked for.
    const pick = atRarity[0] ?? inSet[0] ?? prints[0];
    out.push({
      at,
      card: {
        scryfall_id: '',
        oracle_id: card.oracle_id,
        name: card.name,
        type_line: card.type_line,
        mana_cost: card.mana_cost,
        cmc: card.cmc ?? 0,
        colors: [],
        color_identity: coloursOf(card),
        rarity: pick?.rarity ?? '',
        set_code: pick?.set_code ?? '',
        printing_id: pick?.printing_id ?? '',
      } as CatalogueCard,
    });
  }

  return out
    .sort((a, b) => a.at - b.at || a.card.name.localeCompare(b.card.name))
    .slice(0, limit)
    .map((x) => x.card);
}
