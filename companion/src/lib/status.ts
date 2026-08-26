/**
 * What the connection strip says.
 *
 * There was no connection status anywhere in the app. A banner appeared when
 * something was wrong and nothing at all when things were fine — which sounds
 * reasonable until you are standing in a shop wondering whether the card you
 * just scanned reached the PC, and the app's answer is a blank space.
 *
 * "Nothing is wrong" and "I have not checked" look identical when both are
 * silence, and they are completely different situations. So the strip is
 * always there, and it says which of the two it is.
 *
 * Kept out of the screen because it is a pile of small judgements about
 * wording and precedence, and every one of them is a sentence somebody reads
 * while deciding whether to trust the app with a box of cards.
 */

import type { AppSnapshot } from './app-state.ts';
import type { Via } from './reach.ts';

export type Tone = 'good' | 'warn' | 'bad' | 'idle';

export interface Status {
  text: string;
  tone: Tone;
  /** The shorter half, for the strip. */
  headline: string;
}

/**
 * How long ago, in words.
 *
 * Deliberately vague past an hour. "Synced 73 minutes ago" invites arithmetic
 * nobody wants to do; what matters is whether it was recent.
 */
export function agoInWords(then: string | undefined, now: number): string {
  if (!then) return '';
  const at = Date.parse(then);
  if (!Number.isFinite(at)) return '';
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 45) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? 'yesterday' : `${days} days ago`;
}

function pathName(via: Via): string {
  if (via === 'lan') return 'Wi-Fi';
  if (via === 'tunnel') return 'Tailscale';
  return '';
}

export function describeConnection(
  snapshot: AppSnapshot,
  now: number = Date.now(),
): Status {
  const waiting = snapshot.pendingEdits > 0
    ? `${snapshot.pendingEdits} change${snapshot.pendingEdits === 1 ? '' : 's'} waiting`
    : '';

  if (snapshot.connection === 'unpaired') {
    // The worst state and the only one with a single obvious action, so it
    // says the action rather than the diagnosis.
    return {
      tone: 'bad',
      headline: 'Not paired',
      text: 'Your PC no longer recognises this phone. Scan the QR code again.',
    };
  }

  if (snapshot.connection === 'offline') {
    return {
      tone: 'warn',
      headline: waiting ? `Offline · ${waiting}` : 'Offline',
      text: waiting
        ? `Can't reach your PC. ${waiting} — they go across when it comes back.`
        : "Can't reach your PC. Your collection is still here.",
    };
  }

  if (snapshot.connection === 'connected') {
    const path = pathName(snapshot.via ?? null);
    const when = agoInWords(snapshot.lastSyncAt, now);
    // Pending edits while connected means a sync is mid-flight or one failed
    // partway. Saying "Connected" alone there would be a small lie.
    const headline = waiting
      ? `Syncing · ${waiting}`
      : path
        ? `Connected over ${path}`
        : 'Connected';
    return {
      tone: waiting ? 'warn' : 'good',
      headline,
      text: [
        path ? `Talking to your PC over ${path}.` : 'Talking to your PC.',
        when ? `Last synced ${when}.` : '',
        waiting,
      ]
        .filter(Boolean)
        .join(' '),
    };
  }

  return {
    tone: 'idle',
    headline: 'Checking…',
    text: 'Looking for your PC.',
  };
}
