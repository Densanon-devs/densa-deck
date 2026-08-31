/**
 * Getting the card index without a PC.
 *
 * The phone needs two indexes to work alone — which printing is which, and
 * what each card does — and until now the only source was a paired desktop.
 * That made a phone-only customer unable to scan on day one, which is the
 * first thing anyone tries.
 *
 * Scryfall publishes exactly this as bulk data, which is the source the
 * desktop already ingests, and asks that it be used INSTEAD of hammering
 * their per-card API — 105,000 printings is 600 paged requests, and this
 * is one file.
 *
 * The shape of the job is set by three facts:
 *
 *  * The files are `.jsonl.gz` — one card per line, gzipped. Line-delimited
 *    is what makes this possible at all: a 500 MB JSON array would have to
 *    be held whole to parse, and a phone will not do that.
 *  * They are served as real gzip (`Content-Type: application/gzip`), not
 *    transparently decompressed, so it has to be inflated here.
 *  * React Native's `fetch` does not stream a response body. So the
 *    compressed file goes to disk, and is then inflated a chunk at a time
 *    in memory. The uncompressed form — half a gigabyte — is never written
 *    anywhere; only the handful of fields per card that get kept.
 *
 * Attribution is not optional: card data is Scryfall's, and every surface
 * that shows it says so.
 */

import { Inflate } from 'pako';

/** Bulk sets worth pulling, and what each is for. */
export type BulkKind = 'oracle_cards' | 'default_cards';

export interface BulkSource {
  kind: BulkKind;
  url: string;
  /** Compressed bytes, for a progress bar that means something. */
  bytes: number;
  updatedAt: string;
}

/** One row of the printing index: id, name, set, number, mana value. */
export type PrintingRow = [string, string, string, string, number | null];

/** One row of the oracle index. */
export type OracleIndexRow =
  [string, string, string, string, string, number | null, string];

/**
 * Scryfall asks for a descriptive agent and will refuse a generic one.
 *
 * Named and versioned so they can identify our traffic, which is the deal
 * for using their bandwidth.
 */
export const USER_AGENT = 'DensaDeck/1.0 (companion; densanon.com)';

/** Where the current bulk files are. Their URLs change every day. */
export async function bulkSources(
  fetchImpl: typeof fetch = fetch,
): Promise<Record<BulkKind, BulkSource>> {
  const response = await fetchImpl('https://api.scryfall.com/bulk-data', {
    headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Scryfall said ${response.status} asking for bulk data.`);
  }
  const body = (await response.json()) as {
    data?: Array<Record<string, unknown>>;
  };
  const out = {} as Record<BulkKind, BulkSource>;
  for (const entry of body.data ?? []) {
    const kind = String(entry.type ?? '') as BulkKind;
    if (kind !== 'oracle_cards' && kind !== 'default_cards') continue;
    const url = String(entry.jsonl_download_uri ?? '');
    // Line-delimited or nothing. The plain `download_uri` is one enormous
    // JSON array, which cannot be parsed a piece at a time.
    if (!url) continue;
    out[kind] = {
      kind,
      url,
      bytes: Number(entry.compressed_size ?? 0),
      updatedAt: String(entry.updated_at ?? ''),
    };
  }
  if (!out.oracle_cards || !out.default_cards) {
    throw new Error('Scryfall did not offer the bulk files we need.');
  }
  return out;
}

/**
 * Whether a card belongs in the index at all.
 *
 * English paper cards. The others are real cards and deliberately excluded:
 * a digital-only printing cannot be the one in your hand, and a
 * non-English one carries a name the scanner will never read off an
 * English card — including both would triple the index and add nothing but
 * wrong answers to an exact-key match.
 */
export function isIndexable(card: Record<string, unknown>): boolean {
  if (card.lang !== 'en') return false;
  if (card.digital === true) return false;
  const games = card.games;
  return Array.isArray(games) ? games.includes('paper') : true;
}

/** The printing-index row for one card, or null if it does not belong. */
export function toPrintingRow(
  card: Record<string, unknown>,
): PrintingRow | null {
  if (!isIndexable(card)) return null;
  const id = String(card.id ?? '');
  const name = String(card.name ?? '');
  if (!id || !name) return null;
  const cmc = card.cmc;
  return [
    id,
    name,
    String(card.set ?? ''),
    String(card.collector_number ?? ''),
    typeof cmc === 'number' ? cmc : null,
  ];
}

/** The oracle-index row for one card, or null. */
export function toOracleRow(
  card: Record<string, unknown>,
): OracleIndexRow | null {
  if (!isIndexable(card)) return null;
  const oracleId = String(card.oracle_id ?? '');
  const name = String(card.name ?? '');
  if (!oracleId || !name) return null;
  const cmc = card.cmc;
  const identity = card.color_identity;
  return [
    oracleId,
    name,
    String(card.type_line ?? ''),
    // A two-faced card keeps its rules text on the faces rather than the
    // top level, and reading nothing there would leave half the card blank.
    String(card.oracle_text ?? textFromFaces(card)),
    String(card.mana_cost ?? ''),
    typeof cmc === 'number' ? cmc : null,
    Array.isArray(identity) ? identity.join('') : '',
  ];
}

function textFromFaces(card: Record<string, unknown>): string {
  const faces = card.card_faces;
  if (!Array.isArray(faces)) return '';
  return faces
    .map((face: Record<string, unknown>) => {
      const name = String(face?.name ?? '');
      const text = String(face?.oracle_text ?? '');
      return name && text ? `${name}\n${text}` : text;
    })
    .filter(Boolean)
    .join('\n//\n');
}

/**
 * Turn a chunk of decompressed bytes into whole lines.
 *
 * A chunk almost never ends on a line boundary, so the tail is carried
 * forward. Losing it would drop one card per chunk — a few hundred cards
 * scattered through the index, each of which simply would not scan, with
 * nothing to show anybody why.
 */
export class LineReader {
  private tail = '';

  /** Whole lines from this chunk; the partial last one is kept back. */
  push(text: string): string[] {
    const lines = (this.tail + text).split('\n');
    this.tail = lines.pop() ?? '';
    return lines;
  }

  /** Whatever is left at the end of the file. */
  flush(): string[] {
    const last = this.tail.trim();
    this.tail = '';
    return last ? [last] : [];
  }
}

/**
 * Parse one JSONL line into whichever row is wanted.
 *
 * A line that will not parse is SKIPPED, not thrown. The file is 100,000
 * lines from someone else's server: one bad line should cost one card, not
 * the whole download.
 *
 * The array wrapper characters get their own mention because the JSONL
 * files are pure lines — but a caller pointed at the plain `download_uri`
 * by mistake would feed this `[` and `{...},` forever, and silently
 * indexing nothing is worse than saying so.
 */
export function parseLine<T>(
  line: string,
  pick: (card: Record<string, unknown>) => T | null,
): T | null {
  const text = line.trim().replace(/,$/, '');
  if (!text || text === '[' || text === ']') return null;
  try {
    return pick(JSON.parse(text) as Record<string, unknown>);
  } catch {
    return null;
  }
}

export interface InflateProgress {
  /** Rows kept so far. */
  rows: number;
  /** Compressed bytes consumed, against `BulkSource.bytes`. */
  bytes: number;
}

/**
 * Inflate a gzipped JSONL file and hand back rows in batches.
 *
 * Chunked on purpose, in both directions: the gzip is pushed through the
 * inflater a piece at a time so peak memory is a chunk rather than a file,
 * and rows come back in batches so the caller writes a few hundred at once
 * instead of a hundred thousand times.
 */
export async function readBulk<T>(
  chunks: AsyncIterable<Uint8Array>,
  pick: (card: Record<string, unknown>) => T | null,
  onBatch: (rows: T[]) => Promise<void>,
  onProgress?: (p: InflateProgress) => void,
  batchSize = 500,
): Promise<number> {
  const inflate = new Inflate({ to: 'string' });
  const reader = new LineReader();
  let batch: T[] = [];
  let rows = 0;
  let bytes = 0;
  let failed: Error | null = null;

  const take = async (text: string, last: boolean) => {
    const lines = last
      ? [...reader.push(text), ...reader.flush()]
      : reader.push(text);
    for (const line of lines) {
      const row = parseLine(line, pick);
      if (!row) continue;
      batch.push(row);
      rows += 1;
      if (batch.length >= batchSize) {
        await onBatch(batch);
        batch = [];
      }
    }
  };

  // pako hands decompressed pieces to onData synchronously during push, so
  // the writes are collected here and awaited after each push rather than
  // inside the callback.
  let pending = '';
  inflate.onData = (piece: unknown) => { pending += String(piece); };
  inflate.onEnd = (status: number) => {
    if (status !== 0) failed = new Error(`The download was corrupt (${status}).`);
  };

  for await (const chunk of chunks) {
    bytes += chunk.byteLength;
    inflate.push(chunk, false);
    if (failed) throw failed;
    if (pending) {
      await take(pending, false);
      pending = '';
    }
    onProgress?.({ rows, bytes });
  }
  inflate.push(new Uint8Array(0), true);
  if (failed) throw failed;
  await take(pending, true);
  if (batch.length) await onBatch(batch);
  onProgress?.({ rows, bytes });
  return rows;
}
