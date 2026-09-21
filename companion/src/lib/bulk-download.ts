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

/** How a download is going, while it is going. */
export type DownloadWatcher = (bytes: number, total: number) => void;

/**
 * Download a bulk file and hand its bytes back in pieces.
 *
 * The file lands in the cache directory, where the OS may reclaim it, and
 * is removed as soon as it has been read either way — a 74 MB leftover
 * from a download somebody cancelled is worse than doing it again.
 *
 * `watch` is called as the bytes arrive, and it is not a nicety.
 * `File.downloadFileAsync` is a single await over 78 MB: it reports
 * nothing until the whole file is down, so every progress bar in the
 * app sat at zero for the entire slow part and then raced through the
 * fast one. Reported as "it never actually seems to finish or update
 * me on how long or how far", which is exactly what it looked like.
 *
 * The legacy download API is used for this and only for this. It is
 * the one that takes a progress callback; the new `File` API has no
 * equivalent, and a silent download is worse than an old import.
 */
export async function* downloadedChunks(
  url: string,
  watch?: DownloadWatcher,
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

  const target = new File(cache, nameFrom(url));
  try {
    // A leftover from a download that died halfway would otherwise be
    // read as if it were whole, and a truncated gzip fails in the
    // inflater rather than here, which is a long way from the cause.
    target.delete();
  } catch {
    // Nothing there, which is the common case.
  }

  await downloadWatched(url, target.uri, watch);

  // Re-opened by path: the value the download resolves to is the base
  // file type, which has no reader on it.
  const file = new File(target.uri);
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

/**
 * The download itself, reporting as it goes.
 *
 * Kept apart so the generator above reads as "get the file, then read
 * it", and so the one legacy import in this project has a single
 * place to live.
 */
async function downloadWatched(
  url: string,
  toUri: string,
  watch?: DownloadWatcher,
): Promise<void> {
  const legacy = await import('expo-file-system/legacy');
  const task = legacy.createDownloadResumable(
    url,
    toUri,
    { headers: { 'User-Agent': USER_AGENT } },
    (p) => watch?.(p.totalBytesWritten, p.totalBytesExpectedToWrite),
  );
  const out = await task.downloadAsync();
  if (!out?.uri) {
    throw new Error('The card index download did not complete.');
  }
}

/**
 * A filename for the cache, from the URL.
 *
 * Scryfall's bulk URLs carry the build date, so two downloads on
 * different days do not collide and one on the same day reuses the
 * same name — which is the name being deleted first.
 */
function nameFrom(url: string): string {
  const last = String(url).split('?')[0]?.split('/').pop() || 'bulk.jsonl.gz';
  return last.replace(/[^\w.-]/g, '_');
}
