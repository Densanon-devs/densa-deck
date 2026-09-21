/**
 * The flag that stuck on.
 *
 * Auto scan filed one card and then went quiet — no error, no status, the
 * chip still lit. The loop was asking "is a capture in flight?" and being
 * told yes for ever, because the photo handler cleared its busy flag in a
 * `finally` attached to the second of two `try` blocks, and a successful
 * on-device identification returned from the first.
 *
 * It survived unnoticed because that return was unreachable: local
 * identification was itself broken, so every photo fell through to the
 * second block and cleared the flag on the way past. Fixing the reader
 * made the leak live, and the symptom appeared somewhere else entirely.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { whileBusy } from '../src/lib/busy.ts';

describe('raising and lowering a busy flag', () => {
  test('it is up during the work and down after', async () => {
    const flag = { current: false };
    let seen = null;
    await whileBusy(flag, async () => { seen = flag.current; });
    assert.equal(seen, true, 'up while working');
    assert.equal(flag.current, false, 'down afterwards');
  });

  test('an early return still lowers it', async () => {
    // The actual bug. A handler that returns from the middle is the
    // normal shape of "identified it, nothing more to do".
    const flag = { current: false };
    const out = await whileBusy(flag, async () => 'done early');
    assert.equal(out, 'done early');
    assert.equal(flag.current, false);
  });

  test('a thrown error still lowers it', async () => {
    const flag = { current: false };
    await assert.rejects(
      () => whileBusy(flag, async () => { throw new Error('camera died'); }),
      /camera died/);
    assert.equal(flag.current, false,
      'a crash must not wedge the screen shut');
  });

  test('and the work can be run again straight afterwards', async () => {
    // What "auto scan does nothing" actually looked like: the second tick
    // and every tick after it declined to run.
    const flag = { current: false };
    let runs = 0;
    for (let i = 0; i < 3; i += 1) {
      await whileBusy(flag, async () => { runs += 1; });
    }
    assert.equal(runs, 3);
  });

  test('a second call while one is in flight is refused, not queued',
    async () => {
      // Two captures at once race to file the same card twice, and the
      // loser reports on the winner's result.
      const flag = { current: false };
      let started = 0;
      let release;
      const held = new Promise((r) => { release = r; });

      const first = whileBusy(flag, async () => { started += 1; await held; });
      const second = await whileBusy(flag, async () => { started += 1; });

      assert.equal(second, undefined, 'the second one did not run');
      assert.equal(started, 1);
      release();
      await first;
      assert.equal(flag.current, false);
    });

  test('the change is announced both ways', async () => {
    // The screen dims its shutter off this, so a missed edge is a button
    // that never comes back.
    const flag = { current: false };
    const seen = [];
    await whileBusy(flag, async () => {}, (busy) => seen.push(busy));
    assert.deepEqual(seen, [true, false]);
  });

  test('and announced down even when the work throws', async () => {
    const flag = { current: false };
    const seen = [];
    await assert.rejects(() => whileBusy(
      flag, async () => { throw new Error('nope'); }, (b) => seen.push(b)));
    assert.deepEqual(seen, [true, false]);
  });
});
