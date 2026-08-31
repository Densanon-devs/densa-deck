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
