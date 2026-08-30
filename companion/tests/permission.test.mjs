/**
 * Looking at a permission again after the user has been to settings.
 *
 * Reported from a real phone: the app said camera access was needed, the
 * button opened Android's settings, the switch was turned on — and coming
 * back the screen still said access was needed. The only escape was to open
 * another tab, pull to refresh, and come back. That works, and nobody would
 * ever guess it.
 *
 * The grant is made in a different app, so the only signal on this side is
 * the process returning to the foreground.
 */

import assert from 'node:assert/strict';
import { describe, test } from 'node:test';

import { cameBackToForeground, shouldRecheck } from '../src/lib/permission.ts';

describe('when to look again', () => {
  test('coming back to the foreground after being sent to settings', () => {
    assert.equal(shouldRecheck('background', 'active', false), true);
  });

  test('and from the transient state the permission dialog leaves behind', () => {
    assert.equal(shouldRecheck('inactive', 'active', false), true);
  });

  test('not on the way out', () => {
    assert.equal(shouldRecheck('active', 'background', false), false);
    assert.equal(shouldRecheck('active', 'inactive', false), false);
  });

  test('and not on a shuffle between two states that are both away', () => {
    // Android reports background -> inactive during transitions. Neither of
    // those cases starts from 'active', so the "did we come back" check
    // cannot catch them — only asking whether we have ARRIVED does.
    assert.equal(shouldRecheck('background', 'inactive', false), false);
    assert.equal(shouldRecheck('inactive', 'background', false), false);
    assert.equal(shouldRecheck('unknown', 'background', false), false);
  });

  test('not when it is already granted', () => {
    // It cannot become false without the process being killed, so every
    // resume would spend a bridge call learning nothing.
    assert.equal(shouldRecheck('background', 'active', true), false);
  });

  test('active to active is not a return from anywhere', () => {
    assert.equal(shouldRecheck('active', 'active', false), false);
  });

  test('an unknown first phase still re-reads', () => {
    // The first transition after mount has no meaningful previous state,
    // and reading once too often costs nothing next to a screen that lies.
    assert.equal(shouldRecheck('unknown', 'active', false), true);
  });
});

describe('coming back from anywhere', () => {
  /**
   * The permission was not the only thing going stale while the app was
   * away. Whether the PC is reachable is decided by a sync, and a sync only
   * ran when a screen asked for one — so walking back into range and
   * reopening left the app insisting it was offline until you found a screen
   * with a pull-to-refresh. Same signal, so the same rule, minus the
   * permission-specific short circuit.
   */
  test('a return to the foreground is worth acting on', () => {
    assert.equal(cameBackToForeground('background', 'active'), true);
    assert.equal(cameBackToForeground('inactive', 'active'), true);
  });

  test('leaving is not', () => {
    assert.equal(cameBackToForeground('active', 'background'), false);
    assert.equal(cameBackToForeground('background', 'inactive'), false);
  });

  test('and it does not care whether a permission was granted', () => {
    // The reachability of a PC has nothing to do with the camera, so this
    // one must fire even in the state where shouldRecheck deliberately
    // stays quiet.
    assert.equal(shouldRecheck('background', 'active', true), false);
    assert.equal(cameBackToForeground('background', 'active'), true);
  });
});
