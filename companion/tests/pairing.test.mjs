/**
 * Pairing and reachability.
 *
 * Losing a pairing means a trip back to the desktop to scan a QR code, which
 * is precisely what cannot be done from a shop. These pin the behaviours that
 * keep that from happening by accident.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { DesktopClient, Unpaired, Unreachable, parsePairingUrl } from '../src/lib/client.ts';
import {
  deviceId,
  forgetPairing,
  loadPairing,
  savePairing,
  withLanFallback,
} from '../src/lib/pairing.ts';
import { LocalStore } from '../src/lib/store.ts';
import { FakeDesktop, MemoryDatabase, resetUuid, testUuid } from './harness.mjs';

async function makeStore() {
  const store = new LocalStore(new MemoryDatabase());
  await store.init();
  return store;
}

describe('reading the QR link', () => {
  test('a pairing url yields an address and a token', () => {
    const pairing = parsePairingUrl(
      'https://100.124.242.11:8791/scan?t=abc123',
    );
    assert.equal(pairing.baseUrl, 'https://100.124.242.11:8791');
    assert.equal(pairing.token, 'abc123');
  });

  test('a link without a token is not a pairing', () => {
    // The web version learned this the hard way: a URL stripped of its token
    // for tidiness produced home-screen shortcuts that could never connect.
    assert.equal(parsePairingUrl('https://100.64.0.1:8791/scan'), null);
  });

  test('nonsense is rejected rather than half-accepted', () => {
    assert.equal(parsePairingUrl('not a url at all'), null);
    assert.equal(parsePairingUrl(''), null);
  });

  test('the app is told where to talk, which is not the browser address', () => {
    // The desktop serves the web page over TLS because a browser has no
    // camera outside a secure context. That certificate is self-signed, and
    // Android refuses those with no way to override it from JavaScript — so
    // pointing the app at the browser address fails on the first request.
    const pairing = parsePairingUrl(
      'https://100.64.0.1:8791/scan?t=abc&api=http://100.64.0.1:8792',
    );
    assert.equal(pairing.baseUrl, 'http://100.64.0.1:8792');
    assert.equal(pairing.token, 'abc');
  });

  test('an older link without an api endpoint still works', () => {
    const pairing = parsePairingUrl('https://100.64.0.1:8791/scan?t=abc');
    assert.equal(pairing.baseUrl, 'https://100.64.0.1:8791');
  });

  test('surrounding whitespace from a scan is tolerated', () => {
    const pairing = parsePairingUrl('  https://100.64.0.1:8791/scan?t=xyz \n');
    assert.equal(pairing.token, 'xyz');
  });
});

describe('remembering the desktop', () => {
  test('a pairing survives being stored and read back', async () => {
    const store = await makeStore();
    await savePairing(store, { baseUrl: 'https://host:8791', token: 'tok' });
    const loaded = await loadPairing(store);
    assert.equal(loaded.baseUrl, 'https://host:8791');
    assert.equal(loaded.token, 'tok');
  });

  test('no pairing reads as null, not as a broken one', async () => {
    assert.equal(await loadPairing(await makeStore()), null);
  });

  test('a corrupt record does not crash the app on launch', async () => {
    const store = await makeStore();
    await store.setMeta('pairing', '{not json');
    assert.equal(await loadPairing(store), null);
  });

  test('forgetting works', async () => {
    const store = await makeStore();
    await savePairing(store, { baseUrl: 'https://host:8791', token: 'tok' });
    await forgetPairing(store);
    assert.equal(await loadPairing(store), null);
  });
});

describe('device identity', () => {
  test('is minted once and then stable', async () => {
    resetUuid();
    const store = await makeStore();
    const first = await deviceId(store, testUuid);
    const second = await deviceId(store, testUuid);
    assert.equal(first, second, 'a device that forgets re-sends its history');
  });
});

describe('reaching the desktop', () => {
  test('the LAN address is tried when the tailnet is down', async () => {
    const desktop = new FakeDesktop();
    const tailnet = 'https://100.64.0.1:8791';
    const lan = 'https://192.168.1.20:8791';

    const fetchImpl = async (url, init) => {
      if (String(url).startsWith(tailnet)) throw new Error('no route to host');
      return desktop.fetchImpl(url, init);
    };
    const client = new DesktopClient(
      withLanFallback({ baseUrl: tailnet, token: desktop.token }, lan),
      { fetchImpl },
    );
    assert.equal(await client.reachable(), true);
  });

  test('the address that worked is tried first next time', async () => {
    const desktop = new FakeDesktop();
    const attempts = [];
    const fetchImpl = async (url, init) => {
      attempts.push(String(url).split('/api/')[0]);
      if (String(url).includes('100.64')) throw new Error('no route');
      return desktop.fetchImpl(url, init);
    };
    const client = new DesktopClient(
      { baseUrl: 'https://100.64.0.1:8791', token: desktop.token,
        lanUrl: 'https://192.168.1.20:8791' },
      { fetchImpl },
    );
    await client.reachable();
    attempts.length = 0;
    await client.reachable();
    assert.equal(attempts[0], 'https://192.168.1.20:8791',
                 'a known-good address should not be rediscovered every call');
  });

  test('unreachable and refused are different problems', async () => {
    // Retrying a 403 forever is exactly the wrong response to being
    // unpaired, and giving up on a dropped connection is wrong too.
    const desktop = new FakeDesktop();
    const client = new DesktopClient(
      { baseUrl: 'https://100.64.0.1:8791', token: desktop.token },
      { fetchImpl: desktop.fetchImpl },
    );

    desktop.reachable = false;
    await assert.rejects(() => client.call('sync/hello'), Unreachable);

    desktop.reachable = true;
    desktop.paired = false;
    await assert.rejects(() => client.call('sync/hello'), Unpaired);
  });

  test('a request that hangs does not hang the app', async () => {
    const client = new DesktopClient(
      { baseUrl: 'https://100.64.0.1:8791', token: 'tok' },
      {
        timeoutMs: 40,
        fetchImpl: (url, init) =>
          new Promise((_resolve, reject) => {
            init.signal.addEventListener('abort', () =>
              reject(new Error('aborted')));
          }),
      },
    );
    await assert.rejects(() => client.call('sync/hello'), Unreachable);
  });
});
