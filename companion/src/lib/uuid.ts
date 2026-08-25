/**
 * Identifiers that exist on a phone.
 *
 * The app used `globalThis.crypto.randomUUID()`. Node has that, jsdom has it,
 * every browser has it — and React Native does not. Neither Hermes, nor React
 * Native, nor Expo's WinterCG runtime installs a global `crypto`, so the call
 * threw the moment a QR code was read, the promise it was inside had nobody
 * awaiting it, and a release build reports unhandled rejections nowhere. The
 * screen simply did not change.
 *
 * What these identify is worth being clear about, because it decides how much
 * is needed here. A device id separates this phone from the desktop in the
 * sync log. A deck id is the key a rename is applied to. An event id is what
 * makes a replayed sync push idempotent. All three need to be *unique*; none
 * of them needs to be *unguessable*, and none is a secret or a capability.
 *
 * So uniqueness is not left to the PRNG alone. Each id carries the
 * millisecond it was made and a per-process counter, which means two ids from
 * this device cannot collide even if `Math.random` repeats itself, and two
 * devices would have to be in the same millisecond AND agree on 74 random bits.
 *
 * This is deliberately NOT a source of randomness for anything security-
 * bearing. If a token, a key or a nonce is ever needed, that wants a real
 * CSPRNG from native code — `expo-crypto` — and not this.
 */

/** Starts somewhere arbitrary so two installs do not march in step. */
let counter = Math.floor(Math.random() * 0x1000);

const HEX: string[] = [];
for (let i = 0; i < 256; i += 1) HEX.push((i + 0x100).toString(16).slice(1));

export function uuid(now: () => number = Date.now): string {
  const bytes = new Uint8Array(16);

  // 48 bits of millisecond clock. Two ids made in different milliseconds
  // differ here whatever the PRNG does.
  let time = now();
  for (let i = 5; i >= 0; i -= 1) {
    bytes[i] = time % 256;
    time = Math.floor(time / 256);
  }

  // 12 bits of counter, so ids made inside one millisecond still differ.
  counter = (counter + 1) % 0x1000;
  bytes[6] = (counter >> 8) & 0x0f;
  bytes[7] = counter & 0xff;

  for (let i = 8; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256);

  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40; // version 4
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80; // variant 10

  const hex = (from: number, to: number) => {
    let out = '';
    for (let i = from; i < to; i += 1) out += HEX[bytes[i] ?? 0];
    return out;
  };

  return (
    `${hex(0, 4)}-${hex(4, 6)}-${hex(6, 8)}-${hex(8, 10)}-${hex(10, 16)}`
  );
}
