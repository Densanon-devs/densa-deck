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

export class Unreachable extends Error {
  /** True when the desktop is simply not there, as opposed to refusing us. */
  readonly offline = true;
  constructor(message: string) {
    super(message);
    this.name = 'Unreachable';
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
}

export interface ClientOptions {
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}

export class DesktopClient {
  private pairing: Pairing;
  private timeoutMs: number;
  private fetchImpl: typeof fetch;
  /** Which address answered last, so the working one is tried first. */
  private preferred?: string;

  constructor(pairing: Pairing, options: ClientOptions = {}) {
    this.pairing = pairing;
    this.timeoutMs = options.timeoutMs ?? 15000;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  /** Addresses to try, best guess first. */
  private candidates(): string[] {
    const all = [this.pairing.baseUrl, this.pairing.lanUrl].filter(
      (u): u is string => Boolean(u),
    );
    if (this.preferred) {
      return [this.preferred, ...all.filter((u) => u !== this.preferred)];
    }
    return all;
  }

  async call<T>(route: string, payload: Record<string, unknown> = {}): Promise<T> {
    const problems: string[] = [];

    for (const base of this.candidates()) {
      let response: Response;
      try {
        response = await this.withTimeout(`${base}/api/${route}`, payload);
      } catch (err) {
        // A dead address is worth trying the next one for. Note it and move
        // on rather than failing the whole call on the first miss.
        problems.push(`${base}: ${(err as Error).message}`);
        continue;
      }

      if (response.status === 403) {
        // Being unreachable and being refused are different problems with
        // different fixes, so they are different exceptions. Retrying a 403
        // forever is exactly the wrong response to being unpaired.
        throw new Unpaired();
      }

      this.preferred = base;
      const data = (await response.json()) as T | ApiError;
      if (isApiError(data)) throw new Error(data.error);
      return data as T;
    }

    throw new Unreachable(
      problems.length
        ? `Desktop unreachable (${problems.join('; ')})`
        : 'No desktop address configured.',
    );
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

  /** Whether the desktop is there at all, without changing anything. */
  async reachable(): Promise<boolean> {
    try {
      await this.call('sync/hello', {});
      return true;
    } catch {
      return false;
    }
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
    return { baseUrl, token };
  } catch {
    return null;
  }
}
