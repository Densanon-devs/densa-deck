/**
 * The shared Densanon hub.
 *
 * The desktop now also answers at `http://<pc>:8770/deck`, one port shared
 * by every Densanon app on that PC, as well as on its own 8792. The phone
 * tries the hub first and falls back to 8792, and remembers which worked.
 * What must never happen: an old QR code that stops pairing, or a phone
 * that pays a timeout on a dead hub before every single call.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import { DesktopClient, parsePairingUrl } from '../src/lib/client.ts';
import { choosePairing, chooseStandalone, rememberPairing } from '../src/lib/pairing.ts';
import { CACHE_MS, Reachability } from '../src/lib/reach.ts';

const LAN = '192.168.1.40';
const TUNNEL = '100.64.1.2';
const legacy = (host) => `http://${host}:8792`;
const hubAt = (host) => `http://${host}:8770/deck`;

/** A /health probe that answers for the URLs in `up`, and records the order. */
function net(up, health = {}) {
  const calls = [];
  return {
    calls,
    probe: async (base) => {
      calls.push(base);
      if (!up.includes(base)) return null;
      const host = base.replace(/^https?:\/\//, '').split(':')[0];
      const peer = host === TUNNEL ? '100.107.166.26' : '192.168.1.77';
      return { ok: true, peer, ...health };
    },
  };
}

function memoryMeta() {
  const meta = new Map();
  return {
    meta,
    async getMeta(key) { return meta.get(key) ?? null; },
    async setMeta(key, value) { meta.set(key, value); },
  };
}

describe('reading a QR code that carries the hub', () => {
  test('the hub addresses are read alongside every old field', () => {
    const pairing = parsePairingUrl(
      `https://${LAN}:8770/deck/scan?t=abc&api=${legacy(LAN)}&lan=${legacy(LAN)}` +
      `&hub=${hubAt(LAN)}&hubts=${hubAt(TUNNEL)}`,
    );
    assert.equal(pairing.baseUrl, legacy(LAN));
    assert.equal(pairing.lanUrl, legacy(LAN));
    assert.equal(pairing.token, 'abc');
    assert.equal(pairing.hubUrl, hubAt(LAN));
    assert.equal(pairing.hubTunnelUrl, hubAt(TUNNEL));
  });

  test('an old QR code without hub fields parses exactly as it always did', () => {
    const pairing = parsePairingUrl(
      `https://${TUNNEL}:8791/scan?t=abc&api=${legacy(TUNNEL)}&lan=${legacy(LAN)}`,
    );
    assert.deepEqual(pairing, {
      baseUrl: legacy(TUNNEL), token: 'abc', lanUrl: legacy(LAN),
    });
  });

  test('a hub address off the local network is dropped, and pairing still works', () => {
    // The token goes wherever these point. A QR code is something a stranger
    // can hold up to the camera.
    const pairing = parsePairingUrl(
      `https://${LAN}:8791/scan?t=abc&api=${legacy(LAN)}` +
      '&hub=http://203.0.113.9:8770/deck&hubts=http://8.8.8.8:8770/deck',
    );
    assert.equal(pairing.baseUrl, legacy(LAN));
    assert.equal(pairing.hubUrl, undefined);
    assert.equal(pairing.hubTunnelUrl, undefined);
  });
});

describe('choosing between the hub and the old port', () => {
  test('the hub is tried first, on the local network before the tunnel', async () => {
    const { probe, calls } = net([hubAt(LAN)]);
    const reach = new Reachability({
      lanUrl: legacy(LAN), tunnelUrl: legacy(TUNNEL),
      hubLanUrl: hubAt(LAN), hubTunnelUrl: hubAt(TUNNEL), token: 't',
    }, probe);
    const result = await reach.resolve();
    assert.equal(result.url, hubAt(LAN));
    assert.equal(result.via, 'lan');
    assert.deepEqual(calls, [hubAt(LAN)]);
  });

  test('no hub falls back to the old port, and the next probe goes there first', async () => {
    let clock = 1000;
    const { probe, calls } = net([legacy(LAN)]);
    const reach = new Reachability({
      lanUrl: legacy(LAN), tunnelUrl: legacy(TUNNEL),
      hubLanUrl: hubAt(LAN), token: 't',
    }, probe, () => clock);

    assert.equal((await reach.resolve()).url, legacy(LAN));
    assert.deepEqual(calls, [hubAt(LAN), legacy(LAN)]);
    assert.equal(reach.current().preferDirect, true);

    // Remembered: the dead hub is not probed ahead of what works.
    clock += CACHE_MS + 1;
    calls.length = 0;
    assert.equal((await reach.resolve()).url, legacy(LAN));
    assert.deepEqual(calls, [legacy(LAN)]);
  });

  test('the hub answering again clears the preference', async () => {
    const { probe } = net([hubAt(TUNNEL)]);
    const reach = new Reachability({
      lanUrl: legacy(LAN), tunnelUrl: legacy(TUNNEL),
      hubLanUrl: hubAt(LAN), hubTunnelUrl: hubAt(TUNNEL),
      preferDirect: true, token: 't',
    }, probe);
    const result = await reach.resolve();
    assert.equal(result.url, hubAt(TUNNEL));
    assert.equal(result.via, 'tunnel');
    assert.equal(reach.current().preferDirect, false);
  });

  test('a pairing with no hub probes exactly what it did before', async () => {
    const { probe, calls } = net([legacy(TUNNEL)]);
    const reach = new Reachability(
      { lanUrl: legacy(LAN), tunnelUrl: legacy(TUNNEL), token: 't' }, probe);
    assert.equal((await reach.resolve()).url, legacy(TUNNEL));
    assert.deepEqual(calls, [legacy(LAN), legacy(TUNNEL)]);
  });

  test('offline still names an address to fail on, the old one first', async () => {
    const { probe } = net([]);
    const reach = new Reachability({
      lanUrl: legacy(LAN), hubLanUrl: hubAt(LAN), token: 't',
    }, probe);
    assert.equal((await reach.resolve()).url, legacy(LAN));
  });
});

describe('learning the hub from /health', () => {
  test('an old pairing learns the hub, and tries it on the next call', async () => {
    const { probe, calls } = net([legacy(LAN), hubAt(LAN)],
                                 { hub: { lan: hubAt(LAN), tailnet: hubAt(TUNNEL) } });
    const reach = new Reachability(
      { lanUrl: legacy(LAN), tunnelUrl: legacy(TUNNEL), token: 't' }, probe);

    assert.equal((await reach.resolve()).url, legacy(LAN));
    assert.equal(reach.current().hubLanUrl, hubAt(LAN));
    assert.equal(reach.current().hubTunnelUrl, hubAt(TUNNEL));

    // Not cached: the very next call goes to the hub it just heard about.
    calls.length = 0;
    assert.equal((await reach.resolve()).url, hubAt(LAN));
    assert.deepEqual(calls, [hubAt(LAN)]);
  });

  test('over the LAN, a different LAN hub hint does not replace ours', async () => {
    // Same multi-NIC reason as the plain LAN address: arriving over the LAN
    // means our addresses are not stale, and the desktop's default route
    // may be an interface this phone cannot reach.
    const { probe } = net([legacy(LAN)], { hub: { lan: 'http://192.168.1.41:8770/deck' } });
    const reach = new Reachability({
      lanUrl: legacy(LAN), hubLanUrl: hubAt(LAN), preferDirect: true, token: 't',
    }, probe);
    await reach.resolve();
    // The stored one was not stale (we arrived over the LAN), so it stays...
    assert.equal(reach.current().hubLanUrl, hubAt(LAN));
    assert.equal(reach.current().preferDirect, true);
  });

  test('a moved LAN hub address is adopted after reaching the PC by tunnel', async () => {
    const moved = 'http://192.168.1.41:8770/deck';
    const { probe } = net([legacy(TUNNEL)], { hub: { lan: moved } });
    const reach = new Reachability({
      lanUrl: legacy(LAN), tunnelUrl: legacy(TUNNEL),
      hubLanUrl: hubAt(LAN), preferDirect: true, token: 't',
    }, probe);
    await reach.resolve();
    assert.equal(reach.current().hubLanUrl, moved);
    assert.equal(reach.current().preferDirect, false, 'new information is worth a try');
  });

  test('hints the phone must not follow are refused', async () => {
    const { probe } = net([legacy(LAN)], {
      hub: {
        lan: 'http://127.0.0.1:8770/deck',      // the phone dialling itself
        tailnet: 'http://8.8.8.8:8770/deck',    // a public address
      },
    });
    const reach = new Reachability({ lanUrl: legacy(LAN), token: 't' }, probe);
    await reach.resolve();
    assert.equal(reach.current().hubLanUrl, undefined);
    assert.equal(reach.current().hubTunnelUrl, undefined);
  });

  test('an https or path-less hint is refused too', async () => {
    const { probe } = net([legacy(LAN)], {
      hub: { lan: `https://${LAN}:8770/deck`, tailnet: `http://${TUNNEL}:8770` },
    });
    const reach = new Reachability({ lanUrl: legacy(LAN), token: 't' }, probe);
    await reach.resolve();
    assert.equal(reach.current().hubLanUrl, undefined);
    assert.equal(reach.current().hubTunnelUrl, undefined);
  });
});

describe('the client through the hub', () => {
  function recordingFetch(answer = { tier: 'free' }) {
    const seen = [];
    const fetchImpl = async (url, init) => {
      seen.push({ url, headers: init?.headers ?? {} });
      return { ok: true, status: 200, json: async () => answer };
    };
    return { seen, fetchImpl };
  }

  test('calls go to the hub with the same paths and the same token', async () => {
    const { probe } = net([hubAt(LAN)]);
    const { seen, fetchImpl } = recordingFetch();
    const client = new DesktopClient({
      baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN), hubUrl: hubAt(LAN),
    }, { probe, fetchImpl });
    const out = await client.call('tier');
    assert.deepEqual(out, { tier: 'free' });
    assert.equal(seen[0].url, `${hubAt(LAN)}/api/tier`);
    assert.equal(seen[0].headers['X-Densa-Token'], 'tok');
  });

  test('what was learned is handed back to be saved', async () => {
    const { probe } = net([legacy(LAN)], { hub: { lan: hubAt(LAN) } });
    const { fetchImpl } = recordingFetch();
    const saved = [];
    const client = new DesktopClient(
      { baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN) },
      { probe, fetchImpl, onPairingChange: (p) => saved.push(p) },
    );
    await client.call('tier');
    assert.equal(saved.length, 1);
    assert.deepEqual(saved[0], {
      baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN), hubUrl: hubAt(LAN),
    });
    // The next call tries the hub it just heard about; here it does not
    // answer, and that is worth saving too, so later launches skip it.
    await client.call('tier');
    assert.equal(saved.length, 2);
    assert.equal(saved[1].preferDirect, true);
    // Nothing new after that, so nothing more to save.
    await client.call('tier');
    assert.equal(saved.length, 2);
  });

  test('a failing save never fails the call', async () => {
    const { probe } = net([legacy(LAN)], { hub: { lan: hubAt(LAN) } });
    const { fetchImpl } = recordingFetch();
    const client = new DesktopClient(
      { baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN) },
      { probe, fetchImpl, onPairingChange: () => { throw new Error('disk full'); } },
    );
    assert.deepEqual(await client.call('tier'), { tier: 'free' });
  });

  test('diagnose reports the hub only when there is one', async () => {
    const { fetchImpl } = recordingFetch({ ok: true });
    const without = new DesktopClient(
      { baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN) }, { fetchImpl });
    assert.deepEqual((await without.diagnose()).map((r) => r.label), ['Wi-Fi', 'Tailscale']);

    const withHub = new DesktopClient(
      { baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN), hubUrl: hubAt(LAN) },
      { fetchImpl });
    const reports = await withHub.diagnose();
    assert.deepEqual(reports.map((r) => r.label),
                     ['Wi-Fi', 'Tailscale', 'Wi-Fi (Densanon hub)']);
    assert.equal(reports[2].url, hubAt(LAN));
  });
});

describe('saving what was learned', () => {
  const pairing = { baseUrl: legacy(TUNNEL), token: 'tok', lanUrl: legacy(LAN) };

  test('it is saved over the same pairing', async () => {
    const store = memoryMeta();
    await choosePairing(store, pairing);
    const learned = { ...pairing, hubUrl: hubAt(LAN), preferDirect: true };
    assert.equal(await rememberPairing(store, learned), true);
    assert.deepEqual(JSON.parse(store.meta.get('pairing')), learned);
  });

  test('it never brings back a pairing the user walked away from', async () => {
    // The client outlives the choice: a probe can finish after the user
    // chose standalone, and must not restore the old desktop.
    const store = memoryMeta();
    await choosePairing(store, pairing);
    await chooseStandalone(store);
    assert.equal(await rememberPairing(store, { ...pairing, hubUrl: hubAt(LAN) }), false);
    assert.equal(store.meta.get('pairing'), '');
  });

  test('it never overwrites a different desktop', async () => {
    const store = memoryMeta();
    await choosePairing(store, { ...pairing, token: 'other' });
    assert.equal(await rememberPairing(store, { ...pairing, hubUrl: hubAt(LAN) }), false);
    assert.equal(JSON.parse(store.meta.get('pairing')).token, 'other');
  });
});

describe('the QR link\'s local address', () => {
  test('one on your own network is kept', () => {
    const p = parsePairingUrl('http://100.64.1.2:8792/scan?t=tok&lan=http://192.168.1.20:8792');
    assert.equal(p.lanUrl, 'http://192.168.1.20:8792');
  });

  test('one off your networks is dropped, so the token never goes there', () => {
    const p = parsePairingUrl('http://100.64.1.2:8792/scan?t=tok&lan=http://203.0.113.9:8792');
    assert.ok(p);
    assert.equal(p.lanUrl, undefined);
    assert.equal(p.baseUrl, 'http://100.64.1.2:8792');
  });
});
