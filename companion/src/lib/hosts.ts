/**
 * Where this app is allowed to send a pairing token.
 *
 * The Android manifest grants cleartext HTTP app-wide, because Android's
 * network security config matches domains and literal hosts, not CIDR blocks,
 * and the desktop's address is a DHCP lease. That permission has to be bounded
 * by something, and the only place it can be bounded *and tested* is here.
 *
 * The rule: a token only ever goes to an address that cannot be routed off the
 * tailnet or the local network.
 *
 *   * `100.64.0.0/10` — the Tailscale range. WireGuard has already encrypted
 *     the hop by the time HTTP is involved.
 *   * `10/8`, `172.16/12`, `192.168/16` — the local network.
 *   * loopback, for a desktop and phone that are the same machine (tests).
 *
 * Everything else is refused. A QR code is a thing a stranger can put in front
 * of a camera; without this, a link naming a public host would hand over a
 * token that grants read and write access to someone's whole collection, over
 * plain HTTP, from a phone that was told to trust cleartext.
 */

import { isPrivateLanAddr, isTunnelAddr } from './reach.ts';

export interface HostVerdict {
  allowed: boolean;
  /** Said plainly enough to show a user. */
  reason?: string;
}

/** The host part of a URL, without scheme, port, credentials or path. */
export function hostOf(url: string): string {
  const withoutScheme = (url || '').trim().replace(/^[a-zA-Z][\w+.-]*:\/\//, '');
  const withoutCredentials = withoutScheme.split('@').pop() ?? '';
  const authority = withoutCredentials.split('/')[0] ?? '';
  // IPv6 literals arrive bracketed; the brackets are not part of the host.
  if (authority.startsWith('[')) {
    return authority.slice(1, authority.indexOf(']')).toLowerCase();
  }
  return (authority.split(':')[0] ?? '').toLowerCase();
}

function isLoopback(host: string): boolean {
  return host === 'localhost' || host === '127.0.0.1' || host === '::1';
}

export function checkHost(url: string): HostVerdict {
  const host = hostOf(url);
  if (!host) {
    return { allowed: false, reason: 'That link has no address in it.' };
  }
  if (isLoopback(host) || isTunnelAddr(host) || isPrivateLanAddr(host)) {
    return { allowed: true };
  }
  return {
    allowed: false,
    reason:
      `Densa Deck only talks to your own PC — on your Tailscale network or ` +
      `your home Wi-Fi. ${host} is neither, so nothing was sent to it.`,
  };
}

export function isAllowedHost(url: string): boolean {
  return checkHost(url).allowed;
}
