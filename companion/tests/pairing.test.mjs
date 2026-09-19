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
  chooseStandalone,
  choosePairing,
  decideStartup,
  deviceId,
  forgetPairing,
  loadPairing,
  savePairing,
  withLanFallback,
  isStandalone,
  setStandalone,
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

  test('the link carries both routes: Wi-Fi and tailnet', () => {
    const pairing = parsePairingUrl(
      'https://100.64.0.1:8791/scan?t=abc' +
      '&api=http://100.64.0.1:8792&lan=http://192.168.88.10:8792',
    );
    assert.equal(pairing.baseUrl, 'http://100.64.0.1:8792', 'tunnel');
    assert.equal(pairing.lanUrl, 'http://192.168.88.10:8792', 'Wi-Fi');
  });

  test('a desktop with no local address just omits it', () => {
    const pairing = parsePairingUrl(
      'https://100.64.0.1:8791/scan?t=abc&api=http://100.64.0.1:8792',
    );
    assert.equal(pairing.lanUrl, undefined);
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
  test('the LAN address is preferred when the phone is home', async () => {
    // Faster than the tunnel, and the traffic never leaves the house.
    const desktop = new FakeDesktop();
    const client = new DesktopClient(
      { baseUrl: 'http://100.64.0.1:8792', token: desktop.token,
        lanUrl: 'http://192.168.88.10:8792' },
      {
        fetchImpl: desktop.fetchImpl,
        probe: async (base) => ({
          ok: true,
          peer: base.includes('100.64') ? '100.107.166.26' : '192.168.88.3',
        }),
      },
    );
    assert.equal(await client.reachable(), true);
    assert.equal(client.via, 'lan');
  });

  test('the tunnel takes over when the LAN address is dead', async () => {
    const desktop = new FakeDesktop();
    const client = new DesktopClient(
      { baseUrl: 'http://100.64.0.1:8792', token: desktop.token,
        lanUrl: 'http://192.168.88.6:8792' },     // a lease that has moved
      {
        fetchImpl: desktop.fetchImpl,
        probe: async (base) =>
          base.includes('100.64')
            ? { ok: true, peer: '100.107.166.26' }
            : null,
      },
    );
    assert.equal(await client.reachable(), true);
    assert.equal(client.via, 'tunnel');
  });

  test('a dead address is dropped so the next call re-probes', async () => {
    // Otherwise the client hammers an address that has stopped answering.
    const desktop = new FakeDesktop();
    let probes = 0;
    const client = new DesktopClient(
      { baseUrl: 'http://100.64.0.1:8792', token: desktop.token },
      {
        fetchImpl: async () => { throw new Error('ECONNREFUSED'); },
        probe: async () => { probes += 1; return { ok: true, peer: '100.107.166.26' }; },
      },
    );
    await assert.rejects(() => client.call('sync/hello'), Unreachable);
    await assert.rejects(() => client.call('sync/hello'), Unreachable);
    assert.equal(probes, 2, 'the second call re-probed rather than trusting a dead winner');
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

describe('choosing to run without a PC, and changing your mind', () => {
  /**
   * Standalone is a decision, not a skip, so it is remembered — an app
   * that works fine alone should not ask to be paired on every launch.
   *
   * And it has to be reversible from inside the app, because the only way
   * in used to be the connection strip, which a standalone phone does not
   * show.
   */
  function store() {
    const meta = new Map();
    return {
      meta,
      async getMeta(key) { return meta.get(key); },
      async setMeta(key, value) { meta.set(key, value); },
    };
  }

  test('a fresh phone has not chosen either way', async () => {
    assert.equal(await isStandalone(store()), false);
  });

  test('choosing it is remembered', async () => {
    const s = store();
    await setStandalone(s, true);
    assert.equal(await isStandalone(s), true);
  });

  test('and it can be turned back off to pair', async () => {
    const s = store();
    await setStandalone(s, true);
    await setStandalone(s, false);
    assert.equal(await isStandalone(s), false);
  });

  test('a value from some other version is not read as yes', async () => {
    // Anything but the exact marker means "not chosen", so a half-written
    // or renamed key cannot silently lock someone out of pairing.
    const s = store();
    await s.setMeta('app.standalone', 'true');
    assert.equal(await isStandalone(s), false);
  });
});

describe('what a launch does with what it finds', () => {
  /**
   * The bug a tester hit: he chose to run without a PC, and the app spent
   * the rest of its life telling him his PC would not connect.
   *
   * Choosing standalone wrote the standalone flag and left the old pairing
   * row sitting in the database. Every launch read the pairing first, found
   * one, and booted paired -- to a desktop that had long since forgotten
   * this phone. So the app nagged about a machine the user had explicitly
   * told it he did not have, and the only cure was a reinstall, which
   * Android's auto-backup then undid.
   *
   * The precedence is the fix, and it is a rule rather than an accident:
   * an explicit "no PC" outranks a leftover address.
   */
  function store() {
    const meta = new Map();
    return {
      meta,
      async getMeta(key) { return meta.get(key); },
      async setMeta(key, value) { meta.set(key, value); },
    };
  }

  test('a fresh phone is asked which it wants', async () => {
    assert.equal(await decideStartup(store()), 'ask');
  });

  test('a paired phone opens paired', async () => {
    const s = store();
    await savePairing(s, { baseUrl: 'https://100.64.0.1:8791', token: 't' });
    assert.equal(await decideStartup(s), 'paired');
  });

  test('a standalone phone opens standalone', async () => {
    const s = store();
    await setStandalone(s, true);
    assert.equal(await decideStartup(s), 'standalone');
  });

  test('and a leftover pairing does NOT override the choice to go alone',
    async () => {
      // This is the tester's phone exactly: both rows present, because
      // choosing standalone never cleared the address. Reading the pairing
      // first is what made the choice look like it had not been saved.
      const s = store();
      await savePairing(s, { baseUrl: 'https://100.64.0.1:8791', token: 't' });
      await setStandalone(s, true);
      assert.equal(await decideStartup(s), 'standalone');
    });
});

describe('choosing one side puts away the other', () => {
  /**
   * The two states are mutually exclusive, and leaving both on disk is what
   * produced a phone that could not be talked out of looking for a PC.
   * Neither choice is trusted to be written by two separate calls at the
   * call site, because that is exactly the pair of calls that got split up.
   */
  function store() {
    const meta = new Map();
    return {
      meta,
      async getMeta(key) { return meta.get(key); },
      async setMeta(key, value) { meta.set(key, value); },
    };
  }

  test('going standalone forgets the desktop address', async () => {
    const s = store();
    await savePairing(s, { baseUrl: 'https://100.64.0.1:8791', token: 't' });

    await chooseStandalone(s);

    assert.equal(await isStandalone(s), true);
    assert.equal(await loadPairing(s), null,
      'a kept address is a PC the app will go on asking about');
  });

  test('pairing drops the standalone choice', async () => {
    // The mirror image, and the reason the precedence above is safe to
    // state as a rule: once pairing clears the flag, the only way to hold
    // both is the old bug.
    const s = store();
    await setStandalone(s, true);

    await choosePairing(s, { baseUrl: 'https://100.64.0.1:8791', token: 't' });

    assert.equal(await isStandalone(s), false);
    assert.deepEqual(await loadPairing(s),
      { baseUrl: 'https://100.64.0.1:8791', token: 't' });
  });

  test('and a standalone phone that pairs, then disconnects, is ASKED',
    async () => {
      // The full round trip. Any residue at either end skips the question.
      const s = store();
      await chooseStandalone(s);
      await choosePairing(s, { baseUrl: 'https://x:8791', token: 't' });

      await forgetPairing(s);
      await setStandalone(s, false);

      assert.equal(await decideStartup(s), 'ask');
    });
});

describe('starting over from inside the app', () => {
  /**
   * Uninstalling does NOT reset this app. Android's auto-backup saves its
   * data and restores it on reinstall, pairing included — so a phone whose
   * desktop had revoked it came back paired to a machine that would never
   * answer, with nothing in the app to press.
   *
   * Disconnecting has to clear BOTH the pairing and the standalone choice,
   * or the next launch skips the question instead of asking it.
   */
  function store() {
    const meta = new Map();
    return {
      meta,
      async getMeta(key) { return meta.get(key); },
      async setMeta(key, value) { meta.set(key, value); },
    };
  }

  test('forgetting the PC leaves no pairing behind', async () => {
    const s = store();
    await savePairing(s, { baseUrl: 'https://100.64.0.1:8791', token: 't' });
    await forgetPairing(s);
    assert.equal(await loadPairing(s), null);
  });

  test('and clearing the standalone choice too means the app ASKS again',
    async () => {
      // Either one left behind sends the next launch straight past the
      // question — one into a dead pairing, the other into standalone.
      const s = store();
      await savePairing(s, { baseUrl: 'https://100.64.0.1:8791', token: 't' });
      await setStandalone(s, true);

      await forgetPairing(s);
      await setStandalone(s, false);

      assert.equal(await loadPairing(s), null);
      assert.equal(await isStandalone(s), false);
    });

  test('the device id survives it', async () => {
    // Sync is meaningless without a stable answer to "who am I": a phone
    // that forgot would look like a new peer and re-send its history.
    const s = store();
    const before = await deviceId(s, () => 'made-up-id');
    await forgetPairing(s);
    assert.equal(await deviceId(s, () => 'a-different-id'), before);
  });
});
