/**
 * The wire contract with the desktop, in one place.
 *
 * These types are the TypeScript half of a protocol whose Python half lives in
 * `src/densa_deck/sync/` and `src/densa_deck/app/phone.py`. They are kept
 * together in this repo rather than published separately precisely so a change
 * to one side is a change to the other in the same commit.
 *
 * Every response from the bridge is a JSON object — never a bare array — so a
 * single envelope type covers every route. That was not free: a list-returning
 * route used to arrive shaped differently from its neighbours, which forces a
 * client to know per route what it is about to receive.
 */

/** Bumped when the exchange changes shape in a way an old client can't read. */
export const PROTOCOL_VERSION = 1;

export type EventKind =
  | 'stack-delta'
  | 'collection-upsert'
  | 'collection-delete'
  | 'deck-upsert'
  | 'deck-delete';

/**
 * One thing that happened, addressed so any device can apply it.
 *
 * `eventUid` is the idempotency key: applying a known uid is a no-op, which is
 * what makes a retried push safe. Never reuse one for a different change.
 */
export interface SyncEvent {
  event_uid: string;
  device: string;
  seq: number;
  kind: EventKind | string;
  payload: Record<string, unknown>;
  created_at: string;
}

/**
 * A quantity change, addressed by NATURAL KEY rather than by any local id.
 *
 * Both devices derive this key from the card itself, so they agree on which
 * stack is meant without ever having coordinated. A local row id would not
 * survive the trip — two devices offline both mint the same integers.
 */
// A type alias rather than an interface, and deliberately: TypeScript gives
// aliases an implicit index signature, so this is assignable to
// Record<string, unknown> for the event payload without `extends
// Record<string, unknown>` — which widened every field to `{}` and quietly
// destroyed the type safety this file exists to provide.
export type StackDelta = {
  printing_id: string;
  card_name: string;
  oracle_id: string;
  finish: string;
  condition: string;
  language: string;
  location: string;
  collection_uid: string;
  /** Signed. Never a total: totals do not commute, deltas do. */
  delta: number;
  reason: string;
};

export interface CollectionRow {
  collection_id: number;
  collection_uid: string;
  name: string;
  kind: string;
  notes: string;
  is_default: boolean;
  cards: number;
  unique_printings: number;
}

export interface CollectionsReply {
  collections: CollectionRow[];
  master: { cards: number; unique_printings: number; value_usd: number };
  default_collection_id: number;
}

export interface CollectionItem {
  item_id: number;
  printing_id: string;
  card_name: string;
  set_code?: string;
  collector_number?: string;
  finish: string;
  condition: string;
  language: string;
  location: string;
  collection_id: number;
  quantity: number;
  price_usd?: number | null;
}

export interface CollectionPage {
  items: CollectionItem[];
  total: number;
  offset: number;
  limit: number;
  page_value_usd: number;
  page_copies: number;
}

export interface HelloReply {
  device: string;
  head: number;
  events: number;
  peer_cursor: number;
  protocol: number;
}

export interface PullReply {
  events: SyncEvent[];
  cursor: number;
  head: number;
  device: string;
  /** True when another round is needed immediately; no guessing required. */
  more: boolean;
}

export interface PushReply {
  applied: number;
  duplicates: number;
  failed: number;
  problems: Array<{ event_uid: string; reason: string }>;
  head: number;
  device: string;
}

/** Any route can answer with this instead of its normal payload. */
export interface ApiError {
  ok: false;
  error: string;
  error_type?: string;
}

export function isApiError(value: unknown): value is ApiError {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as { ok?: unknown }).ok === false
  );
}

/**
 * The natural key of a stack, as a single comparable string.
 *
 * Used to match a local row against an incoming delta. Collection is part of
 * it: the same card in two collections is two stacks, and merging them would
 * silently move cards between groupings.
 */
export function stackKey(d: {
  printing_id: string;
  finish: string;
  condition: string;
  language: string;
  location: string;
  collection_uid: string;
}): string {
  return [
    d.printing_id,
    d.finish || 'nonfoil',
    d.condition || 'NM',
    d.language || 'en',
    d.location || '',
    d.collection_uid || '',
  ].join('\0');
}

/** A card from the catalogue — which is far larger than any collection. */
export interface CatalogueCard {
  scryfall_id: string;
  oracle_id: string;
  name: string;
  type_line: string;
  mana_cost: string;
  cmc: number;
  colors: string[];
  color_identity: string[];
  rarity: string;
  set_code: string;
  price_usd?: number | null;
  image_url?: string;
}

export interface CardSearchReply {
  cards: CatalogueCard[];
  total: number;
  offset: number;
  limit: number;
}

/**
 * What to search for.
 *
 * `ownership` is deliberately optional and defaults to searching EVERYTHING:
 * "what could go in this deck" is a different question from "what do I have",
 * and a deck builder that could only offer cards you already own would be
 * useless for the thing people actually do with one.
 */
export interface CardQuery extends Record<string, unknown> {
  name?: string;
  colors?: string[];
  color_match?: 'identity' | 'exact' | 'any';
  cmc_min?: number;
  cmc_max?: number;
  types?: string[];
  format_legal?: string;
  rarity?: string;
  max_price?: number;
  set_code?: string;
  /** Any of these sets. A card cannot be in two, so picking two means "either". */
  set_codes?: string[];
  rarities?: string[];
  /** Rules text, keywords and type line — "deathtouch" finds every card with it. */
  text?: string;
  sort?: 'name' | 'cmc' | 'cmc_desc' | 'rarity' | 'price';
  /** Omit for the whole catalogue. */
  ownership?: 'owned' | 'unowned';
  limit?: number;
  offset?: number;
}

/** One face of a split, transforming or adventure card. */
export interface CardFace {
  name: string;
  mana_cost: string;
  type_line: string;
  oracle_text: string;
  power: string;
  toughness: string;
}

/**
 * What a card is and what it does.
 *
 * Every field is optional except the name, because the desktop answers with
 * whatever it has. A card missing from the local catalogue still comes back
 * with `unknown_card` set rather than an error: the art and what you own are
 * right either way, and a screen that refused to render over missing rules
 * text would be worse than one that says the text is missing.
 */
export interface CardDetail {
  printing_id: string;
  card_name: string;
  mana_cost?: string;
  cmc?: number;
  type_line?: string;
  oracle_text?: string;
  power?: string;
  toughness?: string;
  loyalty?: string;
  rarity?: string;
  set_code?: string;
  colors?: string[];
  color_identity?: string[];
  keywords?: string[];
  price_usd?: number | null;
  legalities?: Record<string, string>;
  faces?: CardFace[];
  images?: Record<string, string>;
  scryfall_url?: string;
  unknown_card?: boolean;
}

/** A card that appears in more than one collection. */
export interface OverlapCard {
  item_id: number;
  printing_id: string;
  card_name: string;
  finish: string;
  quantity: number;
  collection_count: number;
  collections: string[];
  /** More lists want it than you own copies of it. */
  overcommitted: boolean;
}

export interface OverlapsReply {
  cards: OverlapCard[];
  overcommitted: number;
}

export interface CatalogueSet {
  set_code: string;
  cards: number;
}

/** One printing of a card — the same rules text, different art and set. */
export interface CataloguePrinting {
  printing_id: string;
  set_code: string;
  set_name?: string;
  collector_number: string;
  rarity?: string;
  price_usd?: number | null;
}
