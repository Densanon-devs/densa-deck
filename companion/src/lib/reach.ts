/**
 * Finding the desktop: LAN when you're home, tunnel when you're not.
 *
 * "Connect like LAN from anywhere." The desktop is one machine at two
 * addresses — a local one on your Wi-Fi and a Tailscale one reachable from
 * anywhere — and the phone uses whichever answers, preferring the local one
 * because it is faster and keeps the traffic in the house.
 *
 * This is a port of the logic DensAssistant already ships, scars included.
 * Three of them are the whole reason this file is not four lines long:
 *
 *   1. **The peer address is the only honest signal of which path was taken.**
 *      Dialling a LAN URL and arriving from CGNAT means the packets went
 *      through the tunnel — the URL you dialled tells you what you *intended*,
 *      not what happened.
 *
 *   2. **A LAN address is a DHCP lease and it moves.** A phone that learned it
 *      once at pairing had no way home when the lease changed: the LAN probe
 *      failed forever and every connection silently fell to the tunnel. Any
 *      successful contact by EITHER path now repairs it, which is the right
 *      topology — the tunnel is the path that always works, so let it carry
 *      the news.
 *
 *   3. **A substring test mislabels Wi-Fi as tunnel** when the tunnel address
 *      happens to be a prefix of the LAN one:
 *      `'http://10.0.0.55:8791'.indexOf('10.0.0.5') === 7`. The comparison is
 *      an exact host match.
 */

export type Via = 'lan' | 'tunnel' | null;

/** What `/health` answers with. Both extra fields may be absent. */
export interface Health {
  ok: boolean;
  /** Our own source address, as the desktop saw it. */
  peer?: string;
  /** The desktop's CURRENT LAN address. */
  lan?: string;
}

/**
 * Tailscale hands out 100.64.0.0/10 (CGNAT).
 *
 * A range check, not a prefix match: 100.63 and 100.128 sit either side of it
 * and are ordinary public addresses.
 */
export function isTunnelAddr(ip: string): boolean {
  const match = /^100\.(\d{1,3})\./.exec(ip || '');
  if (!match) return false;
  const second = Number(match[1]);
  return second >= 64 && second <= 127;
}

export function isPrivateLanAddr(ip: string): boolean {
  const parts = (ip || '').split('.');
  if (parts.length !== 4) return false;
  const a = Number(parts[0]);
  const b = Number(parts[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return false;
  if (a === 10) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  return a === 192 && b === 168;
}

/**
 * Which path a reachable desktop answered on.
 *
 * Believes the peer address over the URL we thought we dialled, because that
 * is a measurement rather than a guess. Falls back to an exact host match when
 * the desktop is too old to report one.
 */
export function viaOf(health: Health | null, base: string, tunnelHost: string): Via {
  if (!health) return null;
  if (health.peer) return isTunnelAddr(health.peer) ? 'tunnel' : 'lan';
  if (!tunnelHost) return 'lan';
  const host = base.replace(/^https?:\/\//, '').split(':')[0];
  return host === tunnelHost ? 'tunnel' : 'lan';
}

export interface Endpoints {
  /** The address on the local network, if one is known. */
  lanUrl?: string;
  /** The tailnet address, reachable from anywhere. */
  tunnelUrl?: string;
  token: string;
}

export interface Probe {
  (url: string, token: string): Promise<Health | null>;
}

export interface Resolution {
  url: string | null;
  via: Via;
  /** Set when the desktop told us its LAN address had moved. */
  healedLanHost?: string;
}

/** How long a winning address is trusted before probing again. */
export const CACHE_MS = 25000;

export class Reachability {
  private endpoints: Endpoints;
  private probe: Probe;
  private now: () => number;
  private active: { url: string; via: Via; at: number } | null = null;

  constructor(endpoints: Endpoints, probe: Probe, now: () => number = Date.now) {
    this.endpoints = endpoints;
    this.probe = probe;
    this.now = now;
  }

  /** Forget the cached winner — call after a request fails. */
  clear(): void {
    this.active = null;
  }

  update(endpoints: Partial<Endpoints>): void {
    this.endpoints = { ...this.endpoints, ...endpoints };
    this.clear();
  }

  current(): Endpoints {
    return { ...this.endpoints };
  }

  /**
   * The reachable base URL, LAN first.
   *
   * Returns a best guess rather than null when nothing answers, so a caller
   * offline still has something to try and can report a real network error
   * instead of a configuration one.
   */
  async resolve(): Promise<Resolution> {
    const { lanUrl, tunnelUrl, token } = this.endpoints;
    if (!lanUrl && !tunnelUrl) return { url: null, via: null };

    if (this.active && this.now() - this.active.at < CACHE_MS) {
      return { url: this.active.url, via: this.active.via };
    }

    const tunnelHost = hostOf(tunnelUrl);

    for (const url of [lanUrl, tunnelUrl]) {
      if (!url) continue;
      const health = await this.probe(url, token);
      if (!health) continue;
      const via = viaOf(health, url, tunnelHost);

      if (url === lanUrl) {
        // The stored LAN address works — leave it alone. Adopting the
        // desktop's own idea of its address here would move a working
        // multi-NIC setup onto an interface we may not be able to reach.
        this.active = { url, via, at: this.now() };
        return { url, via };
      }

      // Reached over the tunnel, so the stored LAN address is dead. This is
      // the one case where the desktop's hint beats what we have.
      const healed = this.adoptLanHint(health);
      // Having just learned a new LAN address, drop the cache so the very
      // next call re-probes and comes home, rather than sitting on the tunnel
      // for the full window.
      this.active = healed ? null : { url, via, at: this.now() };
      return { url, via, healedLanHost: healed || undefined };
    }

    this.active = null;
    return { url: lanUrl || tunnelUrl || null, via: null };
  }

  /**
   * Take the desktop's current LAN address when ours has gone stale.
   *
   * Loopback and empty hints are refused outright: adopting `127.0.0.1` would
   * have the phone dialling itself, which fails in a way that looks like the
   * desktop being down.
   */
  private adoptLanHint(health: Health): string {
    const fresh = (health.lan || '').trim();
    if (!fresh || fresh.startsWith('127.') || !isPrivateLanAddr(fresh)) return '';
    const port = portOf(this.endpoints.lanUrl || this.endpoints.tunnelUrl || '');
    const url = `http://${fresh}${port ? `:${port}` : ''}`;
    if (url === this.endpoints.lanUrl) return '';
    this.endpoints = { ...this.endpoints, lanUrl: url };
    return fresh;
  }
}

function hostOf(url?: string): string {
  return (url || '').replace(/^https?:\/\//, '').split(':')[0] ?? '';
}

function portOf(url: string): string {
  const rest = (url || '').replace(/^https?:\/\//, '');
  const parts = rest.split(':');
  return parts.length > 1 ? (parts[1] ?? '').split('/')[0] ?? '' : '';
}

/** The standard probe: ask `/health`, briefly. */
export function makeProbe(
  fetchImpl: typeof fetch,
  timeoutMs = 2500,
): Probe {
  return async (url: string, token: string) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      // The token is what unlocks the `lan` self-heal hint; /health itself is
      // open, because a phone has to be able to ask "are you there" before it
      // can prove anything.
      const query = token ? `?token=${encodeURIComponent(token)}` : '';
      const response = await fetchImpl(`${url}/health${query}`, {
        signal: controller.signal,
      });
      if (!response.ok) return null;
      try {
        return (await response.json()) as Health;
      } catch {
        return { ok: true }; // reachable, just terser than expected
      }
    } catch {
      return null;
    } finally {
      clearTimeout(timer);
    }
  };
}
