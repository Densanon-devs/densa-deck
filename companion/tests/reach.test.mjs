/**
 * LAN ⟷ tunnel resolution.
 *
 * Ported from the regression cover DensAssistant already carries, because the
 * bugs are the same bugs: a phone that connected only over Tailscale while
 * sitting on the same Wi-Fi as the desktop, and a LAN address learned once at
 * pairing that nothing ever refreshed.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  CACHE_MS,
  Reachability,
  isPrivateLanAddr,
  isTunnelAddr,
  makeProbe,
  viaOf,
} from '../src/lib/reach.ts';

const LAN = '192.168.88.10';
const STALE = '192.168.88.6';      // paired with this; the lease has since moved
const TUNNEL = '100.124.242.11';   // Tailscale — never moves
const PORT = 8792;

const url = (host) => `http://${host}:${PORT}`;

/** Answer /health only for `up`; `lan` is what a reachable desktop reports. */
function net(up, lan) {
  const calls = [];
  return {
    calls,
    probe: async (base) => {
      calls.push(base);
      const host = base.replace(/^https?:\/\//, '').split(':')[0];
      if (!up.includes(host)) return null;
      // The peer address is what the desktop SAW, which is the point.
      const peer = host === TUNNEL ? '100.107.166.26' : '192.168.88.3';
      return { ok: true, peer, ...(lan ? { lan } : {}) };
    },
  };
}

function build(up, lan, { lanHost = STALE, now } = {}) {
  const { probe, calls } = net(up, lan);
  const reach = new Reachability(
    { lanUrl: url(lanHost), tunnelUrl: url(TUNNEL), token: 'tok' },
    probe,
    now,
  );
  return { reach, calls };
}

describe('recognising a tunnel address', () => {
  test('matches the whole CGNAT range and nothing either side', () => {
    assert.equal(isTunnelAddr('100.64.0.1'), true);
    assert.equal(isTunnelAddr('100.124.242.11'), true);
    assert.equal(isTunnelAddr('100.127.255.254'), true);
    assert.equal(isTunnelAddr('100.63.0.1'), false);    // just below
    assert.equal(isTunnelAddr('100.128.0.1'), false);   // just above
    assert.equal(isTunnelAddr('192.168.88.3'), false);
    assert.equal(isTunnelAddr(''), false);
  });

  test('private ranges are recognised for what they are', () => {
    assert.equal(isPrivateLanAddr('192.168.1.5'), true);
    assert.equal(isPrivateLanAddr('10.0.0.5'), true);
    assert.equal(isPrivateLanAddr('172.16.0.1'), true);
    assert.equal(isPrivateLanAddr('172.32.0.1'), false);   // outside 16-31
    assert.equal(isPrivateLanAddr('100.124.242.11'), false);
    assert.equal(isPrivateLanAddr('8.8.8.8'), false);
  });
});

describe('which path did we take', () => {
  test('the peer address is believed over the URL we dialled', () => {
    // Dialling the LAN URL but arriving from CGNAT means the tunnel carried it.
    assert.equal(viaOf({ ok: true, peer: '100.107.166.26' }, url(LAN), TUNNEL),
                 'tunnel');
    assert.equal(viaOf({ ok: true, peer: '192.168.88.3' }, url(TUNNEL), TUNNEL),
                 'lan');
  });

  test('an exact host match is the fallback when no peer is reported', () => {
    assert.equal(viaOf({ ok: true }, url(TUNNEL), TUNNEL), 'tunnel');
    assert.equal(viaOf({ ok: true }, url(LAN), TUNNEL), 'lan');
  });

  test('Wi-Fi is not mislabelled when the tunnel address is a prefix', () => {
    // The substring test this replaced:
    // 'http://10.0.0.55:8792'.indexOf('10.0.0.5') === 7  ->  "tunnel"
    assert.equal(viaOf({ ok: true }, 'http://10.0.0.55:8792', '10.0.0.5'), 'lan');
  });

  test('nothing answered means nothing is claimed', () => {
    assert.equal(viaOf(null, url(LAN), TUNNEL), null);
  });
});

describe('choosing an address', () => {
  test('the LAN wins when it answers', async () => {
    const { reach } = build([LAN, TUNNEL], LAN, { lanHost: LAN });
    const result = await reach.resolve();
    assert.equal(result.url, url(LAN));
    assert.equal(result.via, 'lan');
  });

  test('the tunnel takes over when the LAN address is dead', async () => {
    const { reach } = build([TUNNEL]);          // the stale lease refuses
    const result = await reach.resolve();
    assert.equal(result.url, url(TUNNEL));
    assert.equal(result.via, 'tunnel');
  });

  test('a moved LAN address is adopted after reaching the desktop by tunnel', async () => {
    // The lease changed; without this the phone can never find its way home
    // again short of re-pairing.
    const { reach } = build([TUNNEL], LAN);
    const result = await reach.resolve();
    assert.equal(result.healedLanHost, LAN);
    assert.equal(reach.current().lanUrl, url(LAN));
  });

  test('it comes home on the next call, not after the cache expires', async () => {
    let clock = 1000;
    const { probe } = net([TUNNEL], LAN);
    const reach = new Reachability(
      { lanUrl: url(STALE), tunnelUrl: url(TUNNEL), token: 'tok' },
      probe, () => clock,
    );
    assert.equal((await reach.resolve()).url, url(TUNNEL));

    // Same instant — the phone is on Wi-Fi and the healed address answers now.
    const back = net([LAN, TUNNEL], LAN);
    reach.update({});                     // keep endpoints, they were healed
    const healed = new Reachability(
      { ...reach.current(), token: 'tok' }, back.probe, () => clock,
    );
    assert.equal((await healed.resolve()).url, url(LAN));
  });

  test('the winner is cached so a burst does not re-probe', async () => {
    const { reach, calls } = build([LAN, TUNNEL], LAN, { lanHost: LAN });
    await reach.resolve();
    const after = calls.length;
    await reach.resolve();
    await reach.resolve();
    assert.equal(calls.length, after);
  });

  test('the cache expires', async () => {
    let clock = 1000;
    const { probe, calls } = net([LAN, TUNNEL], LAN);
    const reach = new Reachability(
      { lanUrl: url(LAN), tunnelUrl: url(TUNNEL), token: 'tok' }, probe,
      () => clock,
    );
    await reach.resolve();
    const after = calls.length;
    clock += CACHE_MS + 1;
    await reach.resolve();
    assert.ok(calls.length > after);
  });

  test('nothing configured resolves to nothing', async () => {
    const reach = new Reachability({ token: 'tok' }, async () => null);
    assert.equal((await reach.resolve()).url, null);
  });

  test('offline still returns something to try', async () => {
    // So the caller reports a network error rather than a configuration one.
    const { reach } = build([]);
    const result = await reach.resolve();
    assert.equal(result.url, url(STALE));
    assert.equal(result.via, null);
  });
});

describe('do no harm', () => {
  test('a working LAN address is left alone', async () => {
    // Multi-NIC desktop: we reach it on the address we have, its default
    // route is another. Adopting that moves a working setup onto an
    // interface we may not be able to reach at all.
    const { reach } = build([LAN, TUNNEL], '10.9.9.9', { lanHost: LAN });
    const result = await reach.resolve();
    assert.equal(result.url, url(LAN));
    assert.equal(reach.current().lanUrl, url(LAN));
  });

  test('a loopback hint is refused', async () => {
    // Adopting 127.0.0.1 has the phone dialling itself, which fails in a way
    // that looks exactly like the desktop being down.
    const { reach } = build([TUNNEL], '127.0.0.1');
    await reach.resolve();
    assert.equal(reach.current().lanUrl, url(STALE));
  });

  test('a public address is refused as a LAN hint', async () => {
    const { reach } = build([TUNNEL], '8.8.8.8');
    await reach.resolve();
    assert.equal(reach.current().lanUrl, url(STALE));
  });

  test('a desktop that reports no hint changes nothing', async () => {
    const { reach } = build([TUNNEL]);
    await reach.resolve();
    assert.equal(reach.current().lanUrl, url(STALE));
  });
});

describe('the probe', () => {
  test('carries the token, which is what unlocks the LAN hint', async () => {
    const seen = [];
    const probe = makeProbe(async (u) => {
      seen.push(u);
      return { ok: true, json: async () => ({ ok: true }) };
    });
    await probe(url(LAN), 'tok');
    assert.ok(seen[0].includes('token=tok'), seen[0]);
  });

  test('a desktop that answers without JSON still counts as reachable', async () => {
    const probe = makeProbe(async () => ({
      ok: true,
      json: async () => { throw new Error('not json'); },
    }));
    assert.deepEqual(await probe(url(LAN), 'tok'), { ok: true });
  });

  test('an unreachable address is null, not an exception', async () => {
    const probe = makeProbe(async () => { throw new Error('ECONNREFUSED'); });
    assert.equal(await probe(url(LAN), 'tok'), null);
  });

  test('a hanging desktop does not hang the app', async () => {
    const probe = makeProbe(
      (_u, init) => new Promise((_res, rej) => {
        init.signal.addEventListener('abort', () => rej(new Error('aborted')));
      }),
      30,
    );
    assert.equal(await probe(url(LAN), 'tok'), null);
  });
});
