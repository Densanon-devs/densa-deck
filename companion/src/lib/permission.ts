/**
 * When to look at a permission again.
 *
 * Android grants live in Settings, not in the app. Sending someone there and
 * then not looking again when they come back leaves the screen insisting the
 * permission is missing after they have just granted it — which reads as the
 * app being broken, and is only escaped by knowing to go somewhere else and
 * pull to refresh. Nobody knows that.
 *
 * The rule is small enough to state exactly, which is why it lives here
 * rather than inline: re-read when the app comes BACK to the foreground, and
 * only while the answer could still change.
 */

export type Phase = 'active' | 'background' | 'inactive' | 'unknown';

/**
 * Whether a foreground transition should re-read the permission.
 *
 * `granted` short-circuits it: once the answer is yes it cannot become no
 * without the process being killed, so there is nothing to learn and every
 * resume would spend a bridge call finding that out.
 *
 * Only on the way IN. Android also reports 'inactive' on the way out and as
 * a transient state behind the permission dialog itself, and re-reading on
 * those is a call whose answer is already stale.
 */
export function shouldRecheck(
  previous: Phase,
  next: Phase,
  granted: boolean,
): boolean {
  if (granted) return false;
  return cameBackToForeground(previous, next);
}

/**
 * Whether the app has just come BACK from somewhere.
 *
 * The permission is not the only thing that goes stale while the app is
 * away. Whether the PC is reachable is decided by a sync, and a sync only
 * happens when a screen asks for one — so walking back into range and
 * reopening the app left it insisting it was offline until you found a
 * screen with a pull-to-refresh. Same shape of bug, same signal.
 */
export function cameBackToForeground(previous: Phase, next: Phase): boolean {
  if (next !== 'active') return false;
  // active -> active is not a return from anywhere.
  return previous !== 'active';
}
