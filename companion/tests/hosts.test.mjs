/**
 * Where a pairing token is allowed to go.
 *
 * The Android manifest now grants cleartext HTTP app-wide, because it has to:
 * Android 9 made cleartext default to off, Expo only turns it on for debug
 * builds, and this app talks plain HTTP to the desktop on purpose — the
 * alternative is a real certificate via `tailscale serve`, which publishes the
 * machine's name to the public Certificate Transparency log permanently.
 *
 * Android's network security config cannot express "private ranges only": it
 * matches domains and literal hosts, not CIDR blocks, and the desktop's
 * address is a DHCP lease. So the restriction lives in code, where it can be
 * tested — which is what this file is.
 *
 * The threat is not theoretical. A QR code is a thing a stranger can hold in
 * front of a camera, and the token in it grants read and write access to
 * someone's whole collection.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { checkHost, hostOf, isAllowedHost } from '../src/lib/hosts.ts';

describe('picking the host out of a URL', () => {
  test('scheme, port and path are not part of it', () => {
    assert.equal(hostOf('http://192.168.1.5:8792/health?token=x'), '192.168.1.5');
  });

  test('credentials do not smuggle a host past the check', () => {
    // `http://100.64.0.1@evil.example.com/` looks like a tailnet address and
    // resolves to evil.example.com. Reading up to the first dot would pass it.
    assert.equal(hostOf('http://100.64.0.1@example.com/'), 'example.com');
  });

  test('an IPv6 literal loses its brackets', () => {
    assert.equal(hostOf('http://[::1]:8792/'), '::1');
  });

  test('case does not matter', () => {
    assert.equal(hostOf('http://EXAMPLE.com/'), 'example.com');
  });
});

describe('addresses this app will talk to', () => {
  for (const url of [
    'http://100.64.0.1:8792',
    'http://100.124.242.11:8792',
    'http://100.127.255.254:8792',
    'http://10.0.0.5:8792',
    'http://172.16.4.4:8792',
    'http://172.31.0.1:8792',
    'http://192.168.88.10:8792',
    'http://127.0.0.1:8792',
    'http://localhost:8792',
  ]) {
    test(`${url} is allowed`, () => {
      assert.equal(isAllowedHost(url), true);
    });
  }
});

describe('addresses it refuses', () => {
  for (const url of [
    // Either side of the Tailscale range. A prefix match on "100." would let
    // both of these through, and both are ordinary public addresses.
    'http://100.63.0.1:8792',
    'http://100.128.0.1:8792',
    // 172.15 and 172.32 sit either side of the private block.
    'http://172.15.0.1:8792',
    'http://172.32.0.1:8792',
    'http://8.8.8.8:8792',
    'http://example.com:8792',
    'http://cards.example.com/scan',
  ]) {
    test(`${url} is refused`, () => {
      assert.equal(isAllowedHost(url), false);
    });
  }

  test('an empty address is refused rather than treated as local', () => {
    assert.equal(isAllowedHost(''), false);
    assert.equal(isAllowedHost('not a url'), false);
  });

  test('the refusal says something a person can act on', () => {
    const verdict = checkHost('http://example.com:8792');
    assert.equal(verdict.allowed, false);
    assert.ok(verdict.reason?.includes('example.com'));
    assert.ok(/nothing was sent/i.test(verdict.reason ?? ''));
  });
});
