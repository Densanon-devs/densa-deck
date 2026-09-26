/**
 * Pulling a list down does the same thing on every tab.
 *
 * Reported as: swipe-down refresh only worked on Cards. Decks, PC and Scan
 * had no RefreshControl, so the gesture did nothing there.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { pullToSync } from '../src/lib/pull-refresh.ts';

function target({ solo = false, syncFails = false, reloadFails = false } = {}) {
  const calls = [];
  return {
    calls,
    solo,
    sync: async () => {
      calls.push('sync');
      if (syncFails) throw new Error('PC unreachable');
    },
    reload: async () => {
      calls.push('reload');
      if (reloadFails) throw new Error('db gone');
    },
  };
}

describe('pullToSync', () => {
  test('syncs with the PC, then re-reads the screen', async () => {
    const t = target();
    await pullToSync(t, () => assert.fail('no problem expected'));
    assert.deepEqual(t.calls, ['sync', 'reload']);
  });

  test('a phone run on its own only re-reads — there is no PC to ask', async () => {
    const t = target({ solo: true });
    await pullToSync(t, () => assert.fail('no problem expected'));
    assert.deepEqual(t.calls, ['reload']);
  });

  test('a failed sync is reported and the screen is still re-read', async () => {
    const t = target({ syncFails: true });
    const problems = [];
    await pullToSync(t, (err) => problems.push(err.message));
    assert.deepEqual(t.calls, ['sync', 'reload']);
    assert.deepEqual(problems, ['PC unreachable']);
  });

  test('a failed re-read is reported, never thrown at the gesture', async () => {
    const t = target({ reloadFails: true });
    const problems = [];
    await pullToSync(t, (err) => problems.push(err.message));
    assert.deepEqual(problems, ['db gone']);
  });
});
