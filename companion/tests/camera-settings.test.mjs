/**
 * The camera levers.
 *
 * The screen told people to switch lens and gave them nothing to switch it
 * with. On Android there is nothing to switch: expo-camera's `selectedLens`
 * and `getAvailableLensesAsync` are both iOS-only. Zoom is the whole control
 * surface, and on a modern phone it is enough — CameraX moves to the
 * telephoto by itself once the zoom passes the point where the longer lens
 * wins.
 *
 * Persisting it matters as much as having it. Finding the setting that focuses
 * on your cards and losing it every time the tab changes is worse than not
 * having the control.
 */

import { strict as assert } from 'node:assert';
import { describe, test } from 'node:test';

import {
  DEFAULT_CAMERA_SETTINGS,
  ZOOM_STEP,
  clampZoom,
  loadCameraSettings,
  parseCameraSettings,
  saveCameraSettings,
  stepZoom,
  zoomLabel,
} from '../src/lib/camera-settings.ts';
import { LocalStore } from '../src/lib/store.ts';
import { MemoryDatabase } from './harness.mjs';

describe('the zoom control', () => {
  test('it cannot be pushed past either end', () => {
    // expo-camera takes 0..1. Anything outside is undefined behaviour on the
    // native side rather than a clamp.
    assert.equal(clampZoom(-1), 0);
    assert.equal(clampZoom(2), 1);
    assert.equal(stepZoom(0, -1), 0);
    assert.equal(stepZoom(1, 1), 1);
  });

  test('stepping moves by one step', () => {
    assert.equal(stepZoom(0, 1), ZOOM_STEP);
    assert.equal(stepZoom(ZOOM_STEP, -1), 0);
  });

  test('stepping repeatedly does not accumulate float dust', () => {
    // Without rounding the readout reaches 45.000000000000004%.
    let zoom = 0;
    for (let i = 0; i < 9; i += 1) zoom = stepZoom(zoom, 1);
    assert.equal(zoom, 0.45);
    assert.equal(zoomLabel(zoom), '45%');
  });

  test('rubbish is treated as no zoom rather than crashing the camera', () => {
    assert.equal(clampZoom(Number.NaN), 0);
    assert.equal(clampZoom(Number.POSITIVE_INFINITY), 0);
  });

  test('it is labelled as a percentage of the range, not a magnification', () => {
    // The device maximum is not reported, so "2x" would be a guess. Saying
    // 50% is at least true.
    assert.equal(zoomLabel(0), 'none');
    assert.equal(zoomLabel(0.5), '50%');
  });
});

describe('reading stored settings', () => {
  test('nothing stored gives the defaults', () => {
    assert.deepEqual(parseCameraSettings(undefined), DEFAULT_CAMERA_SETTINGS);
  });

  test('unparseable text does not stop the camera opening', () => {
    // A corrupt preference throwing here would take the whole scan screen
    // with it, which is a spectacular way to lose a feature to a stray byte.
    assert.deepEqual(parseCameraSettings('{oh no'), DEFAULT_CAMERA_SETTINGS);
    assert.deepEqual(parseCameraSettings('null'), DEFAULT_CAMERA_SETTINGS);
    assert.deepEqual(parseCameraSettings('42'), DEFAULT_CAMERA_SETTINGS);
  });

  test('a field of the wrong type falls back on its own', () => {
    const parsed = parseCameraSettings(
      JSON.stringify({ zoom: 'lots', torch: 'yes', autofocus: 'maybe' }),
    );
    assert.deepEqual(parsed, DEFAULT_CAMERA_SETTINGS);
  });

  test('a stored zoom outside the range is clamped on the way in', () => {
    assert.equal(parseCameraSettings(JSON.stringify({ zoom: 9 })).zoom, 1);
  });

  test('good values survive', () => {
    const parsed = parseCameraSettings(
      JSON.stringify({ zoom: 0.4, torch: true, autofocus: 'off' }),
    );
    assert.deepEqual(parsed, { zoom: 0.4, torch: true, autofocus: 'off' });
  });
});

describe('keeping them', () => {
  test('what was saved is what comes back', async () => {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();

    await saveCameraSettings(store, {
      zoom: 0.55,
      torch: true,
      autofocus: 'off',
    });

    assert.deepEqual(await loadCameraSettings(store), {
      zoom: 0.55,
      torch: true,
      autofocus: 'off',
    });
  });

  test('a fresh install gets the defaults', async () => {
    const store = new LocalStore(new MemoryDatabase());
    await store.init();
    assert.deepEqual(await loadCameraSettings(store), DEFAULT_CAMERA_SETTINGS);
  });
});
