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
