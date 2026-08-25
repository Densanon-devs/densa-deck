/**
 * Things Node has that a phone does not.
 *
 * This is the third bug of one species. The app called
 * `globalThis.crypto.randomUUID()` — which Node has, jsdom has, and every
 * browser has. React Native has no global `crypto` at all: not Hermes, not
 * React Native's own polyfills, not Expo's WinterCG runtime. So it threw the
 * instant a QR code was read, inside a promise nobody was awaiting, in a
 * release build that reports unhandled rejections nowhere. The pairing screen
 * just sat there.
 *
 * Every test in this repo runs under Node, where all of this exists. That is
 * the gap these checks close: not "is the logic right" but "does this API
 * exist where the code runs".
 *
 * The list is what has been checked against react-native and expo in
 * node_modules and confirmed absent. It is not exhaustive, and it is not meant
 * to be — it grows when something else turns out to be missing.
 */

import { strict as assert } from 'node:assert';
import { readFileSync, readdirSync } from 'node:fs';
import { describe, test } from 'node:test';

const ABSENT_ON_DEVICE = {
  crypto: 'no global crypto in React Native — use src/lib/uuid.ts, or expo-crypto if it must be a CSPRNG',
  localStorage: 'no Web Storage — use expo-sqlite',
  sessionStorage: 'no Web Storage — use expo-sqlite',
  indexedDB: 'no IndexedDB — use expo-sqlite',
  document: 'there is no DOM',
  Buffer: 'Node only — use Uint8Array',
};

function sources() {
  const out = [];
  const walk = (dir) => {
    for (const entry of readdirSync(new URL(dir, import.meta.url), { withFileTypes: true })) {
      if (entry.isDirectory()) walk(`${dir}${entry.name}/`);
      else if (/\.tsx?$/.test(entry.name)) {
        out.push([
          `${dir}${entry.name}`,
          readFileSync(new URL(`${dir}${entry.name}`, import.meta.url), 'utf8'),
        ]);
      }
    }
  };
  walk('../src/');
  out.push(['../App.tsx', readFileSync(new URL('../App.tsx', import.meta.url), 'utf8')]);
  return out;
}

// Comments name the missing globals in order to explain them, so reading raw
// source would flag the explanation as the offence.
const code = (text) =>
  text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*(\/\/|\*).*$/gm, '');

describe('the app only uses globals that exist on a phone', () => {
  const files = sources();

  test('there are sources to check', () => {
    assert.ok(files.length >= 10, `found ${files.length}`);
  });

  for (const [name, why] of Object.entries(ABSENT_ON_DEVICE)) {
    test(`nothing reaches for ${name}`, () => {
      const guilty = files
        .filter(([, text]) =>
          new RegExp(`(^|[^.\w])(globalThis\.|global\.|window\.)?${name}\s*[.[(]`, 'm')
            .test(code(text)),
        )
        .map(([file]) => file);
      assert.deepEqual(guilty, [], `${name}: ${why}`);
    });
  }
});
