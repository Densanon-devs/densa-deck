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
  ZOOM_DEADZONE,
  ZOOM_STEP,
  clampZoom,
  loadCameraSettings,
  parseCameraSettings,
  saveCameraSettings,
  stepZoom,
  zoomAt,
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

  test('stepping moves by one step once past the dead band', () => {
    assert.equal(stepZoom(0.5, 1), 0.55);
    assert.equal(stepZoom(0.5, -1), 0.45);
  });

  test('stepping repeatedly does not accumulate float dust', () => {
    // Without rounding the readout reaches 45.000000000000004%.
    let zoom = ZOOM_DEADZONE;
    for (let i = 0; i < 6; i += 1) zoom = stepZoom(zoom, 1);
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
    assert.equal(zoomLabel(0), '1x');
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

describe('the part of the range that cannot work', () => {
  // expo-camera multiplies the requested fraction by the device maximum and
  // then clamps UP to 1x:
  //     targetZoomRatio = max(1f, min(maxZoomRatio, value * maxZoomRatio))
  // So everything below 1/maxZoomRatio asks for less than 1x, is rounded back
  // to 1x, and changes nothing. That is the reported "camera doesn't move
  // until 15%", and a - or + that lands inside it looks like a dead control.

  test('the first press up clears it in one go', () => {
    assert.equal(stepZoom(0, 1), ZOOM_DEADZONE);
  });

  test('coming back down returns to a true 1x rather than stopping inside', () => {
    assert.equal(stepZoom(ZOOM_DEADZONE, -1), 0);
    assert.equal(stepZoom(ZOOM_DEADZONE / 2, -1), 0);
  });

  test('stepping never comes to rest inside it', () => {
    for (const start of [0, 0.02, 0.05, 0.1, ZOOM_DEADZONE, 0.2, 1]) {
      for (const direction of [-1, 1]) {
        const landed = stepZoom(start, direction);
        assert.ok(
          landed === 0 || landed >= ZOOM_DEADZONE,
          `${start} ${direction > 0 ? 'up' : 'down'} landed on ${landed}`,
        );
      }
    }
  });

  test('a tap inside it snaps to whichever end is nearer', () => {
    assert.equal(zoomAt(0.01), 0);
    assert.equal(zoomAt(ZOOM_DEADZONE - 0.01), ZOOM_DEADZONE);
  });

  test('a tap past it lands where it was aimed', () => {
    assert.equal(zoomAt(0.5), 0.5);
    assert.equal(zoomAt(1), 1);
  });

  test('a tap outside the bar is still a legal zoom', () => {
    // locationX can report slightly outside the view on a sloppy tap.
    assert.equal(zoomAt(-0.2), 0);
    assert.equal(zoomAt(1.4), 1);
  });

  test('no zoom is labelled as the magnification it actually is', () => {
    assert.equal(zoomLabel(0), '1x');
  });
});
