/**
 * Scanning without pressing the button every time.
 *
 * Filing a box of cards one tap at a time is the thing that makes people stop
 * doing it, so the camera takes its own pictures on a timer. Everything that
 * decides whether to take the next one lives here rather than in the screen,
 * because the failures worth guarding against are all timing and none of them
 * can be reproduced on a device by hand.
 *
 * What it has to survive:
 *
 *   * **The desktop being unreachable.** The picture is read on the PC. With
 *     no route there, a loop that keeps firing takes a photograph every second
 *     and a half forever, drains the battery and reports nothing. It stops,
 *     and says why.
 *   * **A frozen camera.** Measured once on the web version: twenty
 *     byte-identical captures over forty-one seconds, so every card after the
 *     first was read from a stale image of the first. Pixels are not reachable
 *     from here, but an identical payload is proof enough.
 *   * **Its own latency.** A round trip can outlast the interval. Firing on a
 *     fixed timer regardless would queue photographs faster than the PC can
 *     read them.
 */

/** Long enough to move a card, short enough not to feel like waiting. */
export const SCAN_INTERVAL_MS = 1500;

/** Consecutive failures before giving up rather than hammering. */
export const FAILURE_LIMIT = 3;

export type StopReason =
  | 'offline'
  | 'frozen-camera'
  | 'too-many-failures'
  | 'stopped';

export type Decision =
  | { act: 'wait' }
  | { act: 'capture' }
  | { act: 'stop'; reason: StopReason };

export interface AutoScanInput {
  running: boolean;
  busy: boolean;
  connection: 'connected' | 'offline' | 'unpaired' | 'unknown';
  now: number;
}

export class AutoScanner {
  private lastAt = 0;
  private failures = 0;
  private lastPayload = '';
  private identicalRun = 0;
  private intervalMs: number;

  constructor(intervalMs = SCAN_INTERVAL_MS) {
    this.intervalMs = intervalMs;
  }

  /** What the screen should do at this instant. */
  next(input: AutoScanInput): Decision {
    if (!input.running) return { act: 'stop', reason: 'stopped' };

    // 'unknown' is the state before the first sync has finished. Refusing to
    // start there would mean auto-scan never works on a cold open, which is
    // exactly when a box of cards is about to be filed.
    if (input.connection === 'offline' || input.connection === 'unpaired') {
      return { act: 'stop', reason: 'offline' };
    }
    if (this.failures >= FAILURE_LIMIT) {
      return { act: 'stop', reason: 'too-many-failures' };
    }
    if (this.identicalRun >= 3) {
      return { act: 'stop', reason: 'frozen-camera' };
    }
    // Busy is checked after the stop conditions so a request in flight cannot
    // hold the loop open past the point it should have given up.
    if (input.busy) return { act: 'wait' };
    if (input.now - this.lastAt < this.intervalMs) return { act: 'wait' };

    this.lastAt = input.now;
    return { act: 'capture' };
  }

  /**
   * A capture came back.
   *
   * The payload is compared rather than inspected: identical bytes twice over
   * means the camera handed back a still, not that the card did not move.
   */
  captured(payload: string): void {
    if (payload && payload === this.lastPayload) this.identicalRun += 1;
    else this.identicalRun = 0;
    this.lastPayload = payload;
  }

  /** The PC answered, whatever it said. */
  succeeded(): void {
    this.failures = 0;
  }

  /** The round trip threw. */
  failed(): void {
    this.failures += 1;
  }

  /** Starting again clears everything that made it stop. */
  reset(now = 0): void {
    this.failures = 0;
    this.identicalRun = 0;
    this.lastPayload = '';
    // Backdated so turning it on takes a picture immediately rather than
    // staring at a still preview for the first interval.
    this.lastAt = now - this.intervalMs;
  }
}

export function explain(reason: StopReason): string {
  switch (reason) {
    case 'offline':
      return 'Auto scan needs your PC — the picture is read there. Reconnect and start it again.';
    case 'frozen-camera':
      return 'The camera is handing back the same picture every time. Close and reopen this screen.';
    case 'too-many-failures':
      return 'Your PC stopped answering, so auto scan stopped rather than keep trying.';
    case 'stopped':
      return '';
  }
}
