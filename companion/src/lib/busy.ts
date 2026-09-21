/**
 * Doing one thing at a time, and always admitting when you have stopped.
 *
 * A "busy" flag set on the way in and cleared on the way out is trivial
 * until the body grows a second exit. Then one path forgets, the flag
 * sticks on for ever, and everything that waits on it waits for ever —
 * silently, because a stuck flag looks exactly like work still in
 * progress.
 *
 * That is not hypothetical. The scan screen set `busy` at the top of its
 * photo handler and cleared it in a `finally` attached to the SECOND of
 * two `try` blocks. Every early return from the first one leaked. It went
 * unnoticed for the whole life of the feature because the only path that
 * returned there — a successful on-device identification — was itself
 * broken and never taken. The moment that was fixed, the first auto scan
 * left the flag on and the loop went quiet: one card, then nothing, with
 * no error anywhere.
 *
 * So the invariant lives here instead of in a handler's control flow,
 * where a `return` added a year later cannot break it.
 */

export interface Flag {
  current: boolean;
}

/**
 * Run `work` with the flag raised, and lower it however that ends.
 *
 * Re-entry is refused rather than queued: two captures at once would race
 * to file the same card twice, and the caller that lost would report on
 * the other one's result.
 *
 * @returns what `work` returned, or undefined if it was already running.
 */
export async function whileBusy<T>(
  flag: Flag,
  work: () => Promise<T>,
  onChange?: (busy: boolean) => void,
): Promise<T | undefined> {
  if (flag.current) return undefined;
  flag.current = true;
  onChange?.(true);
  try {
    return await work();
  } finally {
    // The point of the whole module. Early return, thrown error or normal
    // completion all arrive here.
    flag.current = false;
    onChange?.(false);
  }
}
