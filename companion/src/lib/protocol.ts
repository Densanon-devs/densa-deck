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
  | 'deck-delete'
  // One game, and which version was on the table. Carries its own uid: a
  // local row id means nothing on the other device. A `removed` flag rides
  // the same kind rather than needing a second one — taking a game back is
  // a fact about that game.
  | 'deck-game';

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
  /** Rules text, keywords and type line. Superseded by `anywhere`. */
  text?: string;
  /**
   * One box, searching the whole card — name, rules text, keywords, type.
   *
   * `&&` and `||` combine terms, with `||` the looser of the two, so
   * `a && b || c` reads as `(a && b) || c`.
   */
  anywhere?: string;
  /** Drop Arena-only cards: you cannot own one or take one to a table. */
  exclude_digital?: boolean;
  sort?: 'name' | 'cmc' | 'cmc_desc' | 'rarity' | 'price';
  /** Omit for the whole catalogue. */
  ownership?: 'owned' | 'unowned';
  /**
   * Narrow `ownership` to ONE collection, by uid.
   *
   * "Only the cards in my Modern binder" is a different and more useful
   * question while building a deck than "only cards I own somewhere" — a
   * grouping you have made is usually the shape of the deck you are making.
   *
   * A uid rather than the local integer id, which means nothing across
   * devices. Ignored unless `ownership` is set: on its own it would read as
   * "cards in this collection AND every card in Magic", which is not a
   * question.
   */
  owned_in?: string;
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

/**
 * A deck slot on its way to being resolved.
 *
 * Three shapes in one type, because a slot is allowed to be any of them: a
 * printing id (exact), a set and collector number (exact, but as written on
 * the card rather than as the catalogue keys it), or a bare name meaning
 * "any printing".
 */
export interface DeckSlotRef {
  name: string;
  printing_id?: string;
  set_code?: string;
  collector_number?: string;
}

/**
 * What the desktop made of a slot.
 *
 * `found` false rather than an absent row: a reply shorter than the request
 * would leave the caller working out which slots went missing, and getting
 * that wrong is how a deck screen shows the wrong card's picture.
 */
export interface ResolvedSlot extends DeckSlotRef {
  printing_id: string;
  set_code: string;
  collector_number: string;
  price_usd?: number | null;
  found: boolean;
}

export interface DeckResolveReply {
  slots: ResolvedSlot[];
  /** False before the opt-in printings ingest has been run on the desktop. */
  catalogue_ready: boolean;
}

/**
 * What happened when a scanned card was tagged into a group.
 *
 * Three outcomes, and a caller that cannot tell them apart is a scanner that
 * lies. `tagged` is the good one. `owned: 0` means the card is not in your
 * collection at all — real information when you are picking a bundle out of a
 * pile, and NOT a reason to add it. `candidates` means you own the printing
 * more than one way, and which physical object goes in the bundle is a
 * question only you can answer.
 */
export interface TagCandidate {
  item_id: number;
  card_name: string;
  finish: string;
  condition: string;
  location: string;
  quantity: number;
}

export interface TagResult {
  printing_id: string;
  item_id?: number;
  card_name?: string;
  /** 1 when this call put it in the group, 0 when it was already there. */
  tagged: number;
  already_in?: boolean;
  /** Copies you own. 0 means you do not own this card. */
  owned: number;
  candidates: TagCandidate[];
  collection_uid: string;
}

/** A deck saved on the PC, as the sidebar there lists them. */
export interface DesktopDeck {
  deck_id: string;
  name?: string;
  format?: string;
  versions?: number;
  updated_at?: string;
}

/** One version of a PC deck, with the text that can be edited or analysed. */
export interface DesktopDeckDetail {
  deck_id: string;
  name?: string;
  format?: string;
  decklist?: Record<string, number>;
  decklist_text?: string;
  version_number?: number;
  saved_at?: string;
}

/**
 * A deck built out of a collection.
 *
 * `roles` is as much the point as `decklist` is: a real collection usually
 * cannot fill a format's targets, and a build that quietly handed back sixty
 * cards with four lands would have told you nothing.
 */
export interface BuiltDeck {
  format: string;
  commander: string;
  colors: string[];
  decklist: Record<string, number>;
  decklist_text: string;
  total_cards: number;
  target_size: number;
  short_by: number;
  pool_size: number;
  playable_in_colors: number;
  roles: Array<{ role: string; wanted: number; filled: number; short: number }>;
  analysis?: unknown;
  analysis_error?: string;
}

/** What the PC says after it saves a deck it was handed. */
export interface SavedToPc {
  deck_id?: string;
  version_number?: number;
  saved_at?: string;
  /** Combo lines this save broke, if the combo cache is populated. */
  combos_broken?: Array<{ name?: string }>;
}

/** What is in a group, what it is worth, and what you would regret selling. */
export interface GroupReview {
  collection_uid: string;
  name: string;
  stacks: number;
  copies: number;
  value_usd: number;
  unpriced_stacks: number;
  wanted_elsewhere: Array<{
    card_name: string;
    collections: string[];
    quantity: number;
    leaving?: number;
    left_after?: number;
  }>;
  cards: Array<{
    item_id: number;
    printing_id: string;
    card_name: string;
    set_code: string;
    collector_number: string;
    finish: string;
    condition: string;
    quantity: number;
    owned?: number;
    unit_price_usd?: number | null;
  }>;
}

/** A manifest rendered for someone else to read or import. */
export interface GroupManifest {
  text: string;
  filename: string;
  format: string;
  name: string;
  copies: number;
  stacks: number;
  value_usd: number;
  // True when the group held more stacks than one manifest could carry. The
  // buyer counts the box against this document, so a short one has to say so.
  truncated?: boolean;
}
