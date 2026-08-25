/**
 * Decks on the phone.
 *
 * A deck is a document, not a counter, so it does NOT get the delta treatment
 * the collection does: two people editing one decklist apart have no
 * meaningful merge, and half-merging two lists produces something neither of
 * them meant. Last write wins, and the loss is one edit rather than a card.
 *
 * Deck contents are also independent of ownership. A deck lists "Sol Ring";
 * whether you own one, and which printing, is a separate question the
 * collection answers. Conflating them would mean editing a deck could change
 * what you own.
 */

import type { DesktopClient } from './client.ts';
import type { Database } from './store.ts';

export interface Deck {
  deck_id: string;
  name: string;
  format: string;
  /** Card name -> copies. Deliberately not printings; see above. */
  decklist: Record<string, number>;
  notes: string;
  updated_at: string;
}

/** One line of a decklist as typed, e.g. "4 Lightning Bolt". */
const LINE = /^\s*(?:(\d+)\s*x?\s+)?(.+?)\s*$/;

/**
 * Read a pasted or typed decklist.
 *
 * Tolerant on purpose: people paste from anywhere, and refusing a list
 * because one line has a set code in brackets helps nobody. Anything
 * unreadable is skipped rather than failing the whole import, and the caller
 * is told what was skipped so it can say so.
 */
export function parseDecklist(text: string): {
  cards: Record<string, number>;
  skipped: string[];
} {
  const cards: Record<string, number> = {};
  const skipped: string[] = [];

  for (const raw of (text || '').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('//')) continue;
    // Section headers from exported lists.
    if (/^(sideboard|commander|deck|maybeboard)\b:?$/i.test(line)) continue;

    const match = LINE.exec(line);
    if (!match) {
      skipped.push(line);
      continue;
    }
    const count = Number(match[1] ?? 1);
    // Trailing set codes and collector numbers, as exports emit them.
    const name = (match[2] ?? '')
      .replace(/\s*\([A-Za-z0-9]{2,6}\)\s*\d*\s*$/, '')
      .replace(/\s*\[[^\]]*\]\s*$/, '')
      .trim();

    if (!name || !Number.isFinite(count) || count <= 0) {
      skipped.push(line);
      continue;
    }
    cards[name] = (cards[name] ?? 0) + count;
  }
  return { cards, skipped };
}

export function formatDecklist(cards: Record<string, number>): string {
  return Object.entries(cards)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, count]) => `${count} ${name}`)
    .join('\n');
}

export function deckSize(cards: Record<string, number>): number {
  return Object.values(cards).reduce((sum, n) => sum + n, 0);
}

export class DeckStore {
  private db: Database;

  constructor(db: Database) {
    this.db = db;
  }

  async save(deck: Deck): Promise<void> {
    await this.db.run(
      `INSERT INTO decks (deck_id, name, format, decklist_json, notes, updated_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(deck_id) DO UPDATE SET
         name = excluded.name,
         format = excluded.format,
         decklist_json = excluded.decklist_json,
         notes = excluded.notes,
         updated_at = excluded.updated_at`,
      [
        deck.deck_id,
        deck.name,
        deck.format,
        JSON.stringify(deck.decklist),
        deck.notes,
        deck.updated_at,
      ],
    );
  }

  async list(): Promise<Deck[]> {
    const rows = await this.db.all<{
      deck_id: string;
      name: string;
      format: string;
      decklist_json: string;
      notes: string;
      updated_at: string;
    }>('SELECT * FROM decks ORDER BY updated_at DESC LIMIT 200');
    return rows.map((r) => ({
      deck_id: r.deck_id,
      name: r.name,
      format: r.format,
      decklist: JSON.parse(r.decklist_json || '{}'),
      notes: r.notes,
      updated_at: r.updated_at,
    }));
  }

  async get(deckId: string): Promise<Deck | undefined> {
    return (await this.list()).find((d) => d.deck_id === deckId);
  }

  async remove(deckId: string): Promise<void> {
    await this.db.run('DELETE FROM decks WHERE deck_id = ?', [deckId]);
  }
}

/**
 * How much of a deck you can actually build from what you own.
 *
 * Computed on the phone from the local mirror rather than asked of the
 * desktop, because this is the question you ask standing in a shop deciding
 * what to buy — exactly when the desktop is least likely to be reachable.
 *
 * Counted at the CARD NAME level: a deck slot says "Sol Ring" and any
 * printing of it will do.
 */
export function shortfall(
  decklist: Record<string, number>,
  owned: Array<{ card_name: string; quantity: number }>,
): Array<{ name: string; need: number; have: number; short: number }> {
  const have = new Map<string, number>();
  for (const stack of owned) {
    const key = stack.card_name.toLowerCase();
    have.set(key, (have.get(key) ?? 0) + stack.quantity);
  }

  return Object.entries(decklist)
    .map(([name, need]) => {
      const owns = have.get(name.toLowerCase()) ?? 0;
      return { name, need, have: owns, short: Math.max(0, need - owns) };
    })
    .filter((row) => row.short > 0)
    .sort((a, b) => b.short - a.short || a.name.localeCompare(b.name));
}

/** Ask the desktop to think about a deck. There is no offline answer. */
export async function analyzeOnDesktop(
  client: DesktopClient,
  deck: Deck,
): Promise<unknown> {
  return client.call('analyst/analyze', {
    decklist_text: formatDecklist(deck.decklist),
    name: deck.name,
    format: deck.format,
  });
}


/**
 * Put a card into a decklist, owned or not.
 *
 * Deck contents and ownership are separate questions: a deck says what it
 * wants, the collection says what you have, and `shortfall` is where the two
 * meet. Refusing to list a card you do not own would make the deck builder
 * useless for the thing people mainly use one for — working out what to buy.
 */
export function addToDeck(
  decklist: Record<string, number>,
  name: string,
  count = 1,
): Record<string, number> {
  const clean = (name || '').trim();
  if (!clean || count <= 0) return decklist;
  return { ...decklist, [clean]: (decklist[clean] ?? 0) + count };
}

export function removeFromDeck(
  decklist: Record<string, number>,
  name: string,
  count = 1,
): Record<string, number> {
  const clean = (name || '').trim();
  const have = decklist[clean];
  if (!have) return decklist;
  const next = { ...decklist };
  if (have <= count) delete next[clean];
  else next[clean] = have - count;
  return next;
}

/**
 * What a deck would cost to finish, counting only the copies you lack.
 *
 * Cards with no known price are counted separately rather than as zero — a
 * total that quietly treats "unknown" as "free" is worse than one that admits
 * what it could not price.
 */
export function costToFinish(
  missing: Array<{ name: string; short: number }>,
  prices: Record<string, number | null | undefined>,
): { usd: number; unpriced: number } {
  let usd = 0;
  let unpriced = 0;
  for (const row of missing) {
    const price = prices[row.name.toLowerCase()];
    if (price == null) unpriced += row.short;
    else usd += price * row.short;
  }
  return { usd: Math.round(usd * 100) / 100, unpriced };
}


export interface WishlistRow {
  card_name: string;
  /** What a single deck needs at once. */
  quantity: number;
  /** What every deck would need if all were built together. */
  quantityAcrossDecks: number;
  wantedBy: Array<{ deck_id: string; deck_name: string; quantity: number }>;
}

/**
 * What your decks want that you do not own.
 *
 * DERIVED, not stored. Both halves of the input already sync — decks as
 * documents, ownership as deltas — so recomputing gives the same answer on
 * every device without a wishlist needing a sync protocol of its own, and
 * without the two ever being able to disagree.
 *
 * It also means this works with no signal at all, which matters: "what do I
 * still need" is a question asked in a shop.
 */
export function wishlistFromDecks(
  decks: Deck[],
  owned: Array<{ card_name: string; quantity: number }>,
): WishlistRow[] {
  const rows = new Map<string, WishlistRow>();

  for (const deck of decks) {
    for (const missing of shortfall(deck.decklist, owned)) {
      const key = missing.name.toLowerCase();
      const existing = rows.get(key);
      const source = {
        deck_id: deck.deck_id,
        deck_name: deck.name,
        quantity: missing.short,
      };
      if (existing) {
        // The headline is what ONE deck needs at once. Two decks each
        // wanting a single copy need one copy between them unless both are
        // built at the same time, and quoting two sends someone shopping for
        // a card they do not need.
        existing.quantity = Math.max(existing.quantity, missing.short);
        existing.quantityAcrossDecks += missing.short;
        existing.wantedBy.push(source);
      } else {
        rows.set(key, {
          card_name: missing.name,
          quantity: missing.short,
          quantityAcrossDecks: missing.short,
          wantedBy: [source],
        });
      }
    }
  }

  return [...rows.values()].sort(
    (a, b) => b.quantity - a.quantity || a.card_name.localeCompare(b.card_name),
  );
}

/** What the whole wishlist would cost, unpriced cards reported separately. */
export function wishlistCost(
  rows: WishlistRow[],
  prices: Record<string, number | null | undefined>,
): { usd: number; unpriced: number } {
  return costToFinish(
    rows.map((r) => ({ name: r.card_name, short: r.quantity })),
    prices,
  );
}
