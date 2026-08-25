/**
 * What happens when something throws where nobody was catching.
 *
 * React Native's default behaviour for an uncaught error in a release build is
 * to kill the process. From the outside that is indistinguishable from any
 * other crash: the app vanishes, and the one piece of information that would
 * have explained it — the message — goes with it. On a phone with no debugger
 * attached that is the whole diagnosis gone.
 *
 * So this takes the global handler and keeps the error instead. The app shows
 * what broke and stays open. That is better for the user (they can still reach
 * the rest of the app) and it is the only way a failure on a device that isn't
 * plugged into anything ever gets reported back.
 *
 * Deliberately free of React and of React Native imports so the Node suite can
 * exercise it directly.
 */

export interface Crash {
  message: string;
  stack: string;
  /** Which part of the app it came out of, for when the stack is minified. */
  where: string;
  /** Whether React Native would have terminated the process. */
  fatal: boolean;
  when: number;
}

type Listener = (crash: Crash) => void;

const listeners = new Set<Listener>();
let last: Crash | null = null;

/** Everything that went wrong this session, newest last. Bounded. */
const history: Crash[] = [];
const KEEP = 20;

export function describe(error: unknown): { message: string; stack: string } {
  if (error instanceof Error) {
    return { message: error.message || String(error), stack: error.stack ?? '' };
  }
  // A thrown string, a rejected object, `undefined` from a rejected promise
  // with no reason — all of which happen, and all of which used to render as
  // "[object Object]" with nothing else.
  if (error && typeof error === 'object') {
    try {
      return { message: JSON.stringify(error), stack: '' };
    } catch {
      return { message: Object.prototype.toString.call(error), stack: '' };
    }
  }
  return { message: String(error), stack: '' };
}

export function recordCrash(error: unknown, where: string, fatal = true): Crash {
  const { message, stack } = describe(error);
  const crash: Crash = { message, stack, where, fatal, when: Date.now() };
  last = crash;
  history.push(crash);
  if (history.length > KEEP) history.shift();
  for (const listener of [...listeners]) {
    // A listener that throws must not stop the others, and must not re-enter
    // the trap that called us.
    try {
      listener(crash);
    } catch {
      /* nothing sensible to do here */
    }
  }
  return crash;
}

export function lastCrash(): Crash | null {
  return last;
}

export function crashHistory(): Crash[] {
  return [...history];
}

export function onCrash(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** For tests. */
export function forgetCrashes(): void {
  last = null;
  history.length = 0;
}

interface ErrorUtilsLike {
  getGlobalHandler?: () => ((error: unknown, fatal?: boolean) => void) | undefined;
  setGlobalHandler: (handler: (error: unknown, fatal?: boolean) => void) => void;
}

let installed = false;

/**
 * Take over the global handler.
 *
 * The previous handler is called for non-fatal errors only. Calling it for a
 * fatal one is what terminates the process, which is the behaviour being
 * replaced — an error the user can read beats an app that disappears.
 */
export function installGlobalErrorTrap(
  host: { ErrorUtils?: ErrorUtilsLike } = globalThis as never,
): boolean {
  if (installed) return false;
  const utils = host.ErrorUtils;
  if (!utils || typeof utils.setGlobalHandler !== 'function') return false;

  const previous = utils.getGlobalHandler?.();
  utils.setGlobalHandler((error: unknown, fatal?: boolean) => {
    recordCrash(error, 'uncaught', Boolean(fatal));
    if (!fatal && typeof previous === 'function') previous(error, fatal);
  });
  installed = true;
  return true;
}

/** For tests, which install more than once. */
export function resetGlobalErrorTrap(): void {
  installed = false;
}
