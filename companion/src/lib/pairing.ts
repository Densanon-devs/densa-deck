/**
 * Remembering which desktop this phone belongs to.
 *
 * Stored in the app's own database rather than anywhere the OS might clear:
 * losing the pairing means a trip back to the desktop to scan a QR code, and
 * the entire point of the companion is being useful when the desktop is
 * somewhere else.
 */

import type { Pairing } from './client.ts';
import type { LocalStore } from './store.ts';

const PAIRING_KEY = 'pairing';
const DEVICE_KEY = 'device.id';

export async function savePairing(
  store: LocalStore,
  pairing: Pairing,
): Promise<void> {
  await store.setMeta(PAIRING_KEY, JSON.stringify(pairing));
}

/**
 * Whether this phone has been told to run without a PC.
 *
 * Remembered, because the alternative is being asked to pair on every
 * launch by an app that works fine without one — which reads as nagging
 * rather than as a supported way to own it.
 */
const STANDALONE_KEY = 'app.standalone';

export async function isStandalone(store: {
  getMeta(key: string): Promise<string | null | undefined>;
}): Promise<boolean> {
  return (await store.getMeta(STANDALONE_KEY)) === 'yes';
}

export async function setStandalone(
  store: { setMeta(key: string, value: string): Promise<void> },
  on: boolean,
): Promise<void> {
  await store.setMeta(STANDALONE_KEY, on ? 'yes' : '');
}

export async function loadPairing(store: LocalStore): Promise<Pairing | null> {
  const raw = await store.getMeta(PAIRING_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Pairing;
    return parsed.baseUrl && parsed.token ? parsed : null;
  } catch {
    return null;
  }
}

export async function forgetPairing(store: LocalStore): Promise<void> {
  await store.setMeta(PAIRING_KEY, '');
}

/**
 * Storage narrow enough for the two choices below to be testable.
 *
 * `LocalStore` drags a database in with it; the decisions here are about
 * two strings, and pinning them should not need one.
 */
interface MetaStore {
  getMeta(key: string): Promise<string | null | undefined>;
  setMeta(key: string, value: string): Promise<void>;
}

/**
 * Run without a PC, and mean it.
 *
 * One call rather than two, because the two drifted apart: choosing
 * standalone set the flag and left the old desktop address in place. The
 * launch reads the address first, so every start came back paired to a
 * machine that had forgotten this phone, and the app told a standalone
 * user his PC would not connect -- about a PC he had said he did not have.
 * Uninstalling did not help, because Android restores the database.
 */
export async function chooseStandalone(store: MetaStore): Promise<void> {
  await store.setMeta(PAIRING_KEY, '');
  await store.setMeta(STANDALONE_KEY, 'yes');
}

/**
 * Take a desktop, and stop being standalone.
 *
 * The mirror of the above, and what makes the precedence in
 * `decideStartup` safe to state as a rule instead of a guess.
 */
export async function choosePairing(
  store: MetaStore,
  pairing: Pairing,
): Promise<void> {
  await store.setMeta(PAIRING_KEY, JSON.stringify(pairing));
  await store.setMeta(STANDALONE_KEY, '');
}

/** What a launch should open into. */
export type Startup = 'standalone' | 'paired' | 'ask';

/**
 * Which of the three states this phone is in.
 *
 * Pulled out of the startup effect so the precedence is a stated rule with
 * a test on it rather than the order two `await`s happen to sit in.
 *
 * Standalone wins over a stored pairing. Both present can only mean a
 * phone written by the older build that set the flag without clearing the
 * address -- and between an explicit "no PC" and a leftover address, the
 * explicit choice is the one the user made on purpose. That also heals
 * those phones on their next launch, which matters because the alternative
 * is asking people to reinstall an app whose data survives reinstalling.
 */
export async function decideStartup(store: MetaStore): Promise<Startup> {
  if (await isStandalone(store)) return 'standalone';
  const raw = await store.getMeta(PAIRING_KEY);
  if (!raw) return 'ask';
  try {
    const parsed = JSON.parse(raw) as Pairing;
    return parsed.baseUrl && parsed.token ? 'paired' : 'ask';
  } catch {
    return 'ask';
  }
}

/**
 * This phone's identity, minted once and kept forever.
 *
 * Sync is meaningless without a stable answer to "who am I": a device that
 * forgets looks like a brand new peer and re-sends its whole history, and the
 * desktop's watermark for the old identity is stranded.
 */
export async function deviceId(
  store: LocalStore,
  uuid: () => string,
): Promise<string> {
  const existing = await store.getMeta(DEVICE_KEY);
  if (existing) return existing;
  const minted = `phone-${uuid()}`;
  await store.setMeta(DEVICE_KEY, minted);
  return minted;
}

/**
 * Where a phone should try to reach a desktop, best first.
 *
 * The tailnet address works from anywhere the tailnet reaches, which is the
 * normal case. A LAN address is worth keeping as a second option because it
 * survives Tailscale being off, and at home it is often faster.
 */
export function withLanFallback(pairing: Pairing, lanUrl?: string): Pairing {
  if (!lanUrl || lanUrl === pairing.baseUrl) return pairing;
  return { ...pairing, lanUrl };
}
