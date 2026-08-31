/**
 * Reading Scryfall's bulk files on a phone.
 *
 * The phone needs the card index to work alone, and until now the only
 * source was a paired desktop — which left a phone-only customer unable to
 * scan on day one, the first thing anyone tries.
 *
 * These cover the parts that are pure: what belongs in the index, what each
 * row becomes, and the chunk handling that makes a 74 MB gzip readable in
 * bounded memory. The download itself is a seam.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';
import { gzipSync } from 'node:zlib';

import {
  LineReader,
  bulkSources,
  isIndexable,
  parseLine,
  readBulk,
  toOracleRow,
  toPrintingRow,
} from '../src/lib/scryfall.ts';
import { chooseSource, describeSource } from '../src/lib/index-source.ts';

const CARD = {
  id: 'p-sol', oracle_id: 'o-sol', name: 'Sol Ring',
  type_line: 'Artifact', oracle_text: 'Add two colourless.',
  mana_cost: '{1}', cmc: 1, color_identity: [], set: 'cmm',
  collector_number: '410', lang: 'en', games: ['paper'], digital: false,
};

describe('what belongs in the index', () => {
  test('an English paper card does', () => {
    assert.equal(isIndexable(CARD), true);
  });

  test('a digital-only printing does not', () => {
    // It cannot be the card in your hand.
    assert.equal(isIndexable({ ...CARD, digital: true }), false);
  });

  test('nor a non-English one', () => {
    // It carries a name the scanner will never read off an English card,
    // and including them would triple the index to add wrong answers.
    assert.equal(isIndexable({ ...CARD, lang: 'ja' }), false);
  });

  test('nor an online-only game', () => {
    assert.equal(isIndexable({ ...CARD, games: ['mtgo'] }), false);
  });
});

describe('what a card becomes', () => {
  test('a printing row carries the exact key a scan matches on', () => {
    assert.deepEqual(toPrintingRow(CARD),
      ['p-sol', 'Sol Ring', 'cmm', '410', 1]);
  });

  test('an oracle row carries what the card does', () => {
    const row = toOracleRow(CARD);
    assert.equal(row[1], 'Sol Ring');
    assert.equal(row[2], 'Artifact');
    assert.match(row[3], /two colourless/);
  });

  test('a two-faced card keeps the text off its faces', () => {
    // The rules text lives on the faces rather than the top level, and
    // reading nothing there leaves half the card blank.
    const row = toOracleRow({
      ...CARD,
      oracle_text: undefined,
      card_faces: [
        { name: 'Front', oracle_text: 'Front text.' },
        { name: 'Back', oracle_text: 'Back text.' },
      ],
    });
    assert.match(row[3], /Front text/);
    assert.match(row[3], /Back text/);
  });

  test('a card with no id is skipped rather than stored blank', () => {
    assert.equal(toPrintingRow({ ...CARD, id: '' }), null);
  });

  test('a missing mana value is null, not zero', () => {
    // Zero is a real mana value. Guessing it would put every unreadable
    // card at the front of a curve sort.
    assert.equal(toPrintingRow({ ...CARD, cmc: undefined })[4], null);
  });
});

describe('reading lines out of chunks', () => {
  test('a line split across two chunks is not lost', () => {
    // A chunk almost never ends on a line boundary. Dropping the tail
    // costs one card per chunk — a few hundred scattered through the
    // index, each of which simply would not scan.
    const reader = new LineReader();
    assert.deepEqual(reader.push('one\ntw'), ['one']);
    assert.deepEqual(reader.push('o\nthree'), ['two']);
    assert.deepEqual(reader.flush(), ['three']);
  });

  test('and the last line with no trailing newline still arrives', () => {
    const reader = new LineReader();
    reader.push('only');
    assert.deepEqual(reader.flush(), ['only']);
  });

  test('flushing twice does not repeat it', () => {
    const reader = new LineReader();
    reader.push('only');
    reader.flush();
    assert.deepEqual(reader.flush(), []);
  });
});

describe('a bad line costs one card, not the download', () => {
  test('unparseable JSON is skipped', () => {
    // A hundred thousand lines from someone else's server.
    assert.equal(parseLine('{not json', toPrintingRow), null);
  });

  test('and the array wrapper of the WRONG file is ignored', () => {
    // Pointed at the plain download_uri by mistake, this would otherwise
    // silently index nothing.
    assert.equal(parseLine('[', toPrintingRow), null);
    assert.equal(parseLine(']', toPrintingRow), null);
  });
});

describe('reading a whole gzipped file in pieces', () => {
  const jsonl = [
    JSON.stringify(CARD),
    JSON.stringify({ ...CARD, id: 'p-bolt', name: 'Lightning Bolt' }),
    JSON.stringify({ ...CARD, id: 'p-online', digital: true }),
  ].join('\n') + '\n';

  async function* pieces(buf, size) {
    for (let i = 0; i < buf.length; i += size) {
      yield new Uint8Array(buf.subarray(i, i + size));
    }
  }

  test('every indexable card comes back', async () => {
    const rows = [];
    const n = await readBulk(pieces(gzipSync(jsonl), 16), toPrintingRow,
      async (batch) => { rows.push(...batch); }, undefined, 1);
    assert.equal(n, 2, 'the digital printing should not be indexed');
    assert.deepEqual(rows.map((r) => r[1]), ['Sol Ring', 'Lightning Bolt']);
  });

  test('tiny chunks give the same answer as one big one', async () => {
    // The whole point of the chunking: bounded memory must not change the
    // result.
    const small = [];
    const big = [];
    await readBulk(pieces(gzipSync(jsonl), 4), toPrintingRow,
      async (b) => { small.push(...b); });
    await readBulk(pieces(gzipSync(jsonl), 1 << 20), toPrintingRow,
      async (b) => { big.push(...b); });
    assert.deepEqual(small, big);
  });

  test('progress counts compressed bytes, so a bar can mean something',
    async () => {
      const seen = [];
      await readBulk(pieces(gzipSync(jsonl), 16), toPrintingRow,
        async () => {}, (p) => seen.push(p.bytes));
      assert.ok(seen.length > 1);
      assert.deepEqual(seen, [...seen].sort((a, b) => a - b));
    });

  test('a corrupt download is reported rather than half-indexed', async () => {
    const broken = gzipSync(jsonl);
    broken[broken.length - 6] ^= 0xff;
    await assert.rejects(
      () => readBulk(pieces(broken, 64), toPrintingRow, async () => {}));
  });

  test('rows arrive in batches rather than one at a time', async () => {
    const batches = [];
    await readBulk(pieces(gzipSync(jsonl), 1 << 20), toPrintingRow,
      async (b) => { batches.push(b.length); }, undefined, 500);
    assert.equal(batches.length, 1, 'a hundred thousand writes, one per card');
  });
});

describe('finding the current files', () => {
  const reply = {
    data: [
      {
        type: 'oracle_cards',
        jsonl_download_uri: 'https://x/o.jsonl.gz',
        compressed_size: 100,
        updated_at: 'now',
      },
      {
        type: 'default_cards',
        jsonl_download_uri: 'https://x/d.jsonl.gz',
        compressed_size: 200,
        updated_at: 'now',
      },
      { type: 'rulings', jsonl_download_uri: 'https://x/r.jsonl.gz' },
    ],
  };

  test('both files are found and sized', async () => {
    const out = await bulkSources(async () => ({
      ok: true, status: 200, json: async () => reply,
    }));
    assert.equal(out.default_cards.bytes, 200);
    assert.match(out.oracle_cards.url, /jsonl\.gz$/);
  });

  test('an entry with no line-delimited file is refused, not guessed',
    async () => {
      // The plain download_uri is one enormous JSON array, which cannot be
      // parsed a piece at a time — taking it would run the phone out of
      // memory rather than fail cleanly.
      // BOTH files present, both offering only the array form — so the
      // rejection has to come from refusing that form, not from the file
      // being absent entirely.
      await assert.rejects(() => bulkSources(async () => ({
        ok: true,
        status: 200,
        json: async () => ({
          data: [
            { type: 'oracle_cards', download_uri: 'https://x/o.json' },
            { type: 'default_cards', download_uri: 'https://x/d.json' },
          ],
        }),
      })));
    });

  test('a refusal from Scryfall says so', async () => {
    await assert.rejects(() => bulkSources(async () => ({
      ok: false, status: 503, json: async () => ({}),
    })), /503/);
  });
});

describe('which source to use', () => {
  test('the PC when it is there', () => {
    // 7 MB over the LAN in under a second, against 74 MB from the
    // internet that has to be inflated and parsed on the phone.
    assert.equal(chooseSource({ desktopAvailable: true }), 'desktop');
  });

  test('and Scryfall when it is not', () => {
    assert.equal(chooseSource({ desktopAvailable: false }), 'scryfall');
  });

  test('each says what the wait will be like', () => {
    assert.match(describeSource('scryfall'), /wifi/i);
    assert.match(describeSource('desktop'), /seconds/i);
  });
});
