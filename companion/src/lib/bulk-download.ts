/**
 * Pulling Scryfall's bulk files onto the phone.
 *
 * The one part of the Scryfall path that touches the device, kept apart
 * from `scryfall.ts` so everything that decides what goes in the index
 * stays testable under Node.
 *
 * The shape is forced by React Native: `fetch` will not stream a response
 * body, so a 74 MB gzip cannot be inflated as it arrives. It goes to disk
 * compressed, is read back a chunk at a time, and is deleted afterwards.
 * The uncompressed form — around half a gigabyte — is never written
 * anywhere; only the five fields per card that get kept.
 */

import { USER_AGENT } from './scryfall.ts';

/** How much of the file to inflate at once. */
const CHUNK = 512 * 1024;

/**
 * Download a bulk file and hand its bytes back in pieces.
 *
 * The file lands in the cache directory, where the OS may reclaim it, and
 * is removed as soon as it has been read either way — a 74 MB leftover
 * from a download somebody cancelled is worse than doing it again.
 */
export async function* downloadedChunks(
  url: string,
  onDownloaded?: () => void,
): AsyncGenerator<Uint8Array> {
  // Imported lazily. This module reaches a native filesystem that does not
  // exist under Node, and importing it at the top would take every test in
  // the project down with it — including the tests for the parser this
  // feeds, which is the code most worth testing.
  const { Directory, File, Paths } = await import('expo-file-system');

  const cache = new Directory(Paths.cache, 'densadeck-bulk');
  try {
    cache.create({ intermediates: true, idempotent: true });
  } catch {
    // Already there, which is the common case on a retry.
  }

  const downloaded = await File.downloadFileAsync(url, cache, {
    idempotent: true,
    headers: { 'User-Agent': USER_AGENT },
  });
  onDownloaded?.();

  // Re-opened by path: the value `downloadFileAsync` resolves to is the
  // base file type, which has no reader on it.
  const file = new File(downloaded.uri);
  try {
    const stream = file.readableStream();
    const reader = stream.getReader();
    // Read in whatever pieces the platform gives, then re-cut to a size
    // worth inflating — a few hundred tiny pushes per megabyte costs more
    // in call overhead than the inflating does.
    let held: Uint8Array[] = [];
    let heldBytes = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value?.byteLength) continue;
      held.push(value);
      heldBytes += value.byteLength;
      if (heldBytes >= CHUNK) {
        yield join(held, heldBytes);
        held = [];
        heldBytes = 0;
      }
    }
    if (heldBytes) yield join(held, heldBytes);
  } finally {
    // Whether it finished, failed, or was cancelled.
    try {
      file.delete();
    } catch {
      // A file the OS has already reclaimed is not a problem.
    }
  }
}

function join(parts: Uint8Array[], total: number): Uint8Array {
  if (parts.length === 1) return parts[0]!;
  const out = new Uint8Array(total);
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.byteLength;
  }
  return out;
}
