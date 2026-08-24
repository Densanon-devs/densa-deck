/**
 * Scanning cards from the app.
 *
 * The phone sends pixels; the PC does the reading. That is not laziness — the
 * identification pipeline on the desktop has been tuned against real
 * photographs and knows things this app should not have to relearn: which
 * enhancement recovers a footer, that a saturation pass finds a dark card on a
 * dark table where brightness cannot, that "Silumgar" parses as a set code if
 * you let it.
 *
 * What this file owns is the part that only the phone can know: whether the
 * picture is worth sending at all.
 */

import type { DesktopClient } from './client.ts';

export interface ScanCandidate {
  printing_id: string;
  name: string;
  set_code: string;
  set_name: string;
  collector_number: string;
  finishes: string[];
  price_usd?: number | null;
  price_usd_foil?: number | null;
}

export interface ScanResult {
  confidence: 'exact' | 'likely' | 'ambiguous' | 'unknown';
  auto_addable: boolean;
  suggested_finish: string;
  foil_detected: boolean;
  candidates: ScanCandidate[];
  capture?: { text?: string; card_detected?: boolean };
}

/**
 * How long the same card is held off after being filed.
 *
 * A card sits in frame for several exposures while the camera refocuses. The
 * web version cleared its guard on any frame that read nothing, so one blurred
 * frame between two good ones filed the card twice — six copies of one card
 * went in that way before anyone noticed.
 */
export const REPEAT_HOLD_MS = 4000;

/**
 * How much the scene may change between frames before it counts as movement.
 *
 * Deliberately a motion test rather than a sharpness one. Measured on real
 * scans, the preview sharpness of frames holding a card (157-536) overlapped
 * completely with frames of the floor taken while walking (244-435), so no
 * threshold on sharpness can separate them. Frame-to-frame change can: a
 * still scene differs only by sensor noise.
 */
export const MOTION_CEILING = 12;

export class RepeatGuard {
  private lastName = '';
  private lastAt = 0;
  private count = 0;
  private holdMs: number;

  constructor(holdMs = REPEAT_HOLD_MS) {
    this.holdMs = holdMs;
  }

  /**
   * Whether this card should be filed, and if so which copy it is.
   *
   * A frame that read NOTHING deliberately does not clear the guard: it is
   * usually the same card mid-refocus, not a new one.
   */
  consider(name: string, now: number): { file: boolean; copy: number } {
    if (!name) return { file: false, copy: 0 };

    const isRepeat = name === this.lastName && now - this.lastAt < this.holdMs;
    if (isRepeat) {
      // Still in frame; keep holding it off rather than letting the window
      // expire under a card nobody has moved.
      this.lastAt = now;
      return { file: false, copy: 0 };
    }

    this.count = name === this.lastName ? this.count + 1 : 1;
    this.lastName = name;
    this.lastAt = now;
    return { file: true, copy: this.count };
  }

  reset(): void {
    this.lastName = '';
    this.lastAt = 0;
    this.count = 0;
  }
}

/**
 * Mean per-pixel change between two greyscale frames.
 *
 * A phone resting on a table differs only by sensor noise, a few levels at
 * most; a phone being carried changes most of the frame.
 */
export function motionBetween(
  previous: Float32Array | null,
  current: Float32Array,
): number {
  if (!previous || previous.length !== current.length) return 0;
  let total = 0;
  for (let i = 0; i < current.length; i += 1) {
    total += Math.abs((current[i] ?? 0) - (previous[i] ?? 0));
  }
  return total / current.length;
}

/** Whether two captures are the same image, to catch a frozen camera. */
export function sameImage(
  a: Float32Array | null,
  b: Float32Array | null,
): boolean {
  if (!a || !b || a.length !== b.length) return false;
  let total = 0;
  for (let i = 0; i < a.length; i += 1) {
    total += Math.abs((a[i] ?? 0) - (b[i] ?? 0));
  }
  // Two captures of a real scene always differ by at least sensor noise.
  return total / a.length < 0.5;
}

export interface ScanDecision {
  send: boolean;
  reason?: 'moving' | 'frozen-camera' | 'busy';
}

/**
 * Should this frame be uploaded?
 *
 * Guards against the two failures seen in the field: uploading a 2 MB
 * photograph of the floor every 1.3s while walking between cards, and a
 * camera that hands back the SAME still every time — measured once as twenty
 * byte-identical captures over 41 seconds, which meant every card after the
 * first was read from a frozen image of the first.
 */
export function shouldSend(options: {
  busy: boolean;
  motion: number;
  previousStill: Float32Array | null;
  currentStill: Float32Array | null;
}): ScanDecision {
  if (options.busy) return { send: false, reason: 'busy' };
  if (options.motion > MOTION_CEILING) return { send: false, reason: 'moving' };
  if (
    options.currentStill &&
    sameImage(options.previousStill, options.currentStill)
  ) {
    return { send: false, reason: 'frozen-camera' };
  }
  return { send: true };
}

/** Send a photo to the PC and get back what it thinks the card is. */
export async function identifyPhoto(
  client: DesktopClient,
  base64Jpeg: string,
): Promise<ScanResult> {
  return client.call<ScanResult>('capture', {
    image: base64Jpeg.startsWith('data:')
      ? base64Jpeg
      : `data:image/jpeg;base64,${base64Jpeg}`,
  });
}

/**
 * Which finish to preselect.
 *
 * The desktop reads a star in the corner as a foil hint, but only suggests it
 * when the printing actually HAS a foil — a misread star must not file a
 * finish that was never printed.
 */
export function defaultFinish(
  candidate: ScanCandidate,
  result: ScanResult,
): string {
  const finishes = candidate.finishes ?? ['nonfoil'];
  if (result.suggested_finish === 'foil' && finishes.includes('foil')) {
    return 'foil';
  }
  return finishes.includes('nonfoil') ? 'nonfoil' : (finishes[0] ?? 'nonfoil');
}
