/**
 * Talking to the desktop.
 *
 * Everything is a POST carrying the pairing token, over the tailnet or the
 * LAN. The desktop's certificate is self-signed on purpose — that is what
 * gives the phone a secure context without publishing the machine's name to a
 * public Certificate Transparency log — so a plain `fetch` will reject it on
 * some platforms and the app has to opt in.
 *
 * The client is deliberately dumb: no retry policy, no queueing, no deciding
 * what a failure means. Those belong to the sync engine, which is the only
 * thing that knows whether an operation was safe to repeat.
 */

import { isApiError } from './protocol.ts';
import type { ApiError } from './protocol.ts';
import { checkHost, isAllowedHost } from './hosts.ts';
import { Reachability, makeProbe } from './reach.ts';
import type { Probe, Via } from './reach.ts';

export class Unreachable extends Error {
  /** True when the desktop is simply not there, as opposed to refusing us. */
  readonly offline = true;
  constructor(message: string) {
    super(message);
    this.name = 'Unreachable';
  }
}

/**
 * The desktop refused because the feature is Pro and this tier is not.
 *
 * Its own class so a screen can show "Pro" where the thing would be, rather
 * than an error: a paywall is not a fault.
 */
export class ProRequired extends Error {
  readonly proRequired = true;
  constructor(message: string) {
    super(message);
    this.name = 'ProRequired';
  }
}

export class Unpaired extends Error {
  constructor(message = 'This phone is no longer paired with the desktop.') {
    super(message);
    this.name = 'Unpaired';
  }
}

export interface Pairing {
  /** e.g. https://100.124.242.11:8791 — the tailnet address. */
  baseUrl: string;
  token: string;
  /** Optional LAN address, tried when the tailnet is unavailable. */
  lanUrl?: string;
  /**
   * The same desktop through the shared Densanon hub, e.g.
   * http://192.168.1.40:8770/deck — same `/api/...` paths, same token.
   * Tried first; the two addresses above are the fallback. Absent from
   * pairings made before the hub, which keep working exactly as they did.
   */
  hubUrl?: string;
  /** The hub on the tailnet, e.g. http://100.64.1.2:8770/deck. */
  hubTunnelUrl?: string;
  /** Set when the hub failed and the desktop's own port worked. */
  preferDirect?: boolean;
}

/** What one address did when asked. */
export interface EndpointReport {
  label: 'Wi-Fi' | 'Tailscale' | 'Wi-Fi (Densanon hub)' | 'Tailscale (Densanon hub)';
  url: string;
  ok: boolean;
  /** How the desktop saw this phone, which is the only honest path signal. */
  peer?: string;
  detail: string;
}

export interface ClientOptions {
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  /** Overridable so tests can drive resolution without a network. */
  probe?: Probe;
  /**
   * Told when the addresses change — a healed LAN address, a hub address
   * learned from /health, or which kind of address works — so the app can
   * save them and start the next launch from what worked.
   */
  onPairingChange?: (pairing: Pairing) => void;
}

export class DesktopClient {
  private pairing: Pairing;
  private timeoutMs: number;
  private fetchImpl: typeof fetch;
  private reach: Reachability;
  /** Which path the last successful call took, for the UI to show. */
  private lastVia: Via = null;
  private onPairingChange?: (pairing: Pairing) => void;

  constructor(pairing: Pairing, options: ClientOptions = {}) {
    this.pairing = pairing;
    this.timeoutMs = options.timeoutMs ?? 15000;
    this.fetchImpl = options.fetchImpl ?? fetch;
    // LAN first, tunnel when away — and the LAN address heals itself when the
    // desktop's DHCP lease moves, which it does.
    this.onPairingChange = options.onPairingChange;
    this.reach = new Reachability(
      {
        lanUrl: pairing.lanUrl,
        tunnelUrl: pairing.baseUrl,
        hubLanUrl: pairing.hubUrl,
        hubTunnelUrl: pairing.hubTunnelUrl,
        preferDirect: pairing.preferDirect,
        token: pairing.token,
      },
      options.probe ?? makeProbe(this.fetchImpl),
    );
  }

  /** The pairing as it stands now, with whatever the probes have learned. */
  currentPairing(): Pairing {
    const e = this.reach.current();
    const out: Pairing = { baseUrl: this.pairing.baseUrl, token: this.pairing.token };
    if (e.lanUrl) out.lanUrl = e.lanUrl;
    if (e.hubLanUrl) out.hubUrl = e.hubLanUrl;
    if (e.hubTunnelUrl) out.hubTunnelUrl = e.hubTunnelUrl;
    if (e.preferDirect) out.preferDirect = true;
    return out;
  }

  /** Which path the last call took: 'lan', 'tunnel', or null if unknown. */
  get via(): Via {
    return this.lastVia;
  }

  /** The addresses currently in use, so a healed LAN address can be saved. */
  endpoints() {
    return this.reach.current();
  }

  async call<T>(route: string, payload: Record<string, unknown> = {}): Promise<T> {
    const before = JSON.stringify(this.currentPairing());
    const resolved = await this.reach.resolve();
    this.reportChange(before);
    if (!resolved.url) throw new Unreachable('No desktop address configured.');
    this.lastVia = resolved.via;

    let response: Response;
    try {
      response = await this.withTimeout(`${resolved.url}/api/${route}`, payload);
    } catch (err) {
      // The address we were told to use has stopped answering. Drop it so the
      // next call re-probes rather than hammering a dead one.
      this.reach.clear();
      throw new Unreachable(`Desktop unreachable (${(err as Error).message})`);
    }

    if (response.status === 403) {
      // Being unreachable and being refused are different problems with
      // different fixes, so they are different exceptions. Retrying a 403
      // forever is exactly the wrong response to being unpaired.
      throw new Unpaired();
    }

    const data = (await response.json()) as T | ApiError;
    if (isApiError(data)) {
      if ((data as { error_type?: string }).error_type === 'ProRequired') {
        throw new ProRequired(data.error);
      }
      throw new Error(data.error);
    }
    return data as T;
  }

  private reportChange(before: string): void {
    if (!this.onPairingChange) return;
    const now = this.currentPairing();
    if (JSON.stringify(now) === before) return;
    try {
      this.onPairingChange(now);
    } catch {
      // Saving is a convenience for the next launch; it must never fail a call.
    }
  }

  private async withTimeout(
    url: string,
    payload: Record<string, unknown>,
  ): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      return await this.fetchImpl(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Densa-Token': this.pairing.token,
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Try every address in turn and say what each one did.
   *
   * "Offline" is one word for a dozen different problems — the wrong address,
   * a firewall, Tailscale switched off on the phone, a desktop that isn't
   * serving, a platform refusing the request before it left the handset. Each
   * has a different fix and the app was reporting all of them identically.
   *
   * This does not go through `Reachability`: the point is to report on EVERY
   * endpoint rather than stop at the first that answers.
   */
  async diagnose(): Promise<EndpointReport[]> {
    const { lanUrl, tunnelUrl, hubLanUrl, hubTunnelUrl } = this.reach.current();
    const targets: Array<{ label: EndpointReport['label']; url?: string; optional?: boolean }> = [
      { label: 'Wi-Fi', url: lanUrl },
      { label: 'Tailscale', url: tunnelUrl },
      // Only reported when known: a desktop that predates the hub, or has
      // not joined one, has no hub address, and that is not a fault.
      { label: 'Wi-Fi (Densanon hub)', url: hubLanUrl, optional: true },
      { label: 'Tailscale (Densanon hub)', url: hubTunnelUrl, optional: true },
    ];

    const reports: EndpointReport[] = [];
    for (const target of targets) {
      if (!target.url && target.optional) continue;
      if (!target.url) {
        reports.push({
          label: target.label,
          url: '',
          ok: false,
          detail: 'No address for this path. Pair again to pick one up.',
        });
        continue;
      }

      const verdict = checkHost(target.url);
      if (!verdict.allowed) {
        reports.push({
          label: target.label,
          url: target.url,
          ok: false,
          detail: verdict.reason ?? 'Refused as an address to talk to.',
        });
        continue;
      }

      reports.push(await this.probeOne(target.label, target.url));
    }
    return reports;
  }

  private async probeOne(
    label: EndpointReport['label'],
    url: string,
  ): Promise<EndpointReport> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const query = `?token=${encodeURIComponent(this.pairing.token)}`;
      const response = await this.fetchImpl(`${url}/health${query}`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        return {
          label,
          url,
          ok: false,
          detail: `Answered, but with ${response.status}.`,
        };
      }
      const health = (await response.json()) as { peer?: string };
      return {
        label,
        url,
        ok: true,
        peer: health.peer,
        detail: health.peer
          ? `Answered. It saw this phone as ${health.peer}.`
          : 'Answered.',
      };
    } catch (err) {
      const message = (err as Error).message || String(err);
      return {
        label,
        url,
        ok: false,
        // Android blocks plain HTTP unless the manifest allows it, and the
        // error for that is indistinguishable from a dead host without this
        // note. It cost a whole evening once.
        detail: /abort/i.test(message)
          ? 'No answer within four seconds.'
          : `Could not connect: ${message}`,
      };
    } finally {
      clearTimeout(timer);
    }
  }

  /** Whether the desktop is there at all, without changing anything. */
  async reachable(): Promise<boolean> {
    try {
      await this.call('sync/hello', {});
      return true;
    } catch {
      return false;
    }
  }

  /** Force the next call to re-probe rather than trust the cached winner. */
  forget(): void {
    this.reach.clear();
  }
}

/**
 * Pull a pairing out of the QR link the desktop shows.
 *
 * The token lives in the URL because that is the only transport that survives
 * being bookmarked or saved to a home screen — a lesson learned the hard way
 * on the web version, where stripping it for tidiness produced shortcuts that
 * could never pair.
 */
export function parsePairingUrl(raw: string): Pairing | null {
  try {
    const url = new URL(raw.trim());
    const token = url.searchParams.get('t');
    if (!token) return null;

    // The link carries an `api` endpoint for native clients, and it is NOT
    // the same address the browser uses. The desktop serves the web page over
    // TLS because a browser has no camera outside a secure context; that
    // certificate is self-signed, and Android refuses those outright with no
    // way to override it from JavaScript. So the app is told, in the same QR
    // code, where to talk instead. Falling back to the link's own origin
    // keeps older pairings working.
    const api = url.searchParams.get('api');
    const baseUrl = api ? api.replace(/\/+$/, '') : `${url.protocol}//${url.host}`;

    // The desktop's local address, when it has one. A starting point rather
    // than a fact: a DHCP lease moves, and the phone re-learns the current
    // one from /health on any successful contact — including over the tunnel,
    // which is the path that always works and so is the right one to carry
    // the news.
    // Held to the same rule as the main address, which Pair checks before
    // saving: the probe path sends the token to whatever this names, so a
    // link whose `lan` points off your own networks loses it here.
    const lan = url.searchParams.get('lan');
    const lanUrl = lan && isAllowedHost(lan) ? lan.replace(/\/+$/, '') : undefined;
    const pairing: Pairing = lanUrl ? { baseUrl, token, lanUrl } : { baseUrl, token };

    // The shared Densanon hub, from desktops new enough to have joined one.
    // Absent from older links, which therefore parse exactly as before. Held
    // to the same rule as everything that carries the token -- the tailnet,
    // the local network or this machine -- and dropped rather than refused
    // otherwise, since the addresses above still pair the phone.
    const hub = url.searchParams.get('hub');
    if (hub && isAllowedHost(hub)) pairing.hubUrl = hub.replace(/\/+$/, '');
    const hubts = url.searchParams.get('hubts');
    if (hubts && isAllowedHost(hubts)) pairing.hubTunnelUrl = hubts.replace(/\/+$/, '');
    return pairing;
  } catch {
    return null;
  }
}
