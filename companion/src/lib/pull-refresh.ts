/**
 * What pulling a list down means, on every tab.
 *
 * It used to mean something only on Cards: the other tabs had no
 * RefreshControl at all, so the gesture did nothing there and a change
 * made on the PC stayed invisible until the tab happened to reload.
 *
 * The meaning is the same everywhere: exchange changes with the PC, then
 * re-read this screen. A phone run on its own has no PC to ask, so for it
 * the pull just re-reads — it must still do SOMETHING, or the gesture reads
 * as broken. A failed sync is reported but does not stop the re-read: the
 * phone's own data is still worth showing fresh.
 */

export interface PullTarget {
  /** True when this phone is deliberately run without a PC. */
  solo: boolean;
  sync: () => Promise<unknown>;
  reload: () => Promise<unknown>;
}

export async function pullToSync(
  target: PullTarget,
  onProblem: (err: unknown) => void,
): Promise<void> {
  if (!target.solo) {
    try {
      await target.sync();
    } catch (err) {
      onProblem(err);
    }
  }
  try {
    await target.reload();
  } catch (err) {
    onProblem(err);
  }
}
