/**
 * The camera levers, and remembering where they were left.
 *
 * The scan screen told people to "switch lens" and gave them nothing to switch
 * it with. Worse, on Android there is nothing to switch: expo-camera's
 * `selectedLens` and `getAvailableLensesAsync` are both marked `@platform ios`.
 * Android has no way to name the telephoto.
 *
 * What Android does have is `zoom`, and on any modern phone that IS the lens
 * switch — CameraX moves from the wide to the telephoto by itself as the zoom
 * passes the point where the longer lens is better. Which is exactly the "my
 * designated x2 camera was far sharper" effect, reached by the only control
 * that exists.
 *
 * `zoom` is a fraction of the device's maximum, not a magnification, so it
 * cannot honestly be labelled "2x" — the maximum differs per phone and is not
 * reported. It is shown as a percentage of the range, which is what it is.
 *
 * Settings persist because finding the setting that works and then losing it
 * on every visit to the screen is worse than not having the control.
 */

import type { LocalStore } from './store.ts';

export type FocusMode = 'on' | 'off';

export interface CameraSettings {
  /** 0 = no zoom, 1 = the device's maximum. */
  zoom: number;
  torch: boolean;
  /** `on` autofocuses continuously; `off` locks focus where it is. */
  autofocus: FocusMode;
}

export const DEFAULT_CAMERA_SETTINGS: CameraSettings = {
  zoom: 0,
  torch: false,
  autofocus: 'on',
};

/** How far one press of the zoom control moves. */
export const ZOOM_STEP = 0.05;

const KEY = 'camera.settings';

export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 0;
  // Rounded because repeated stepping otherwise accumulates float dust and
  // the readout shows 45.000000000000004%.
  return Math.round(Math.min(1, Math.max(0, zoom)) * 1000) / 1000;
}

export function stepZoom(zoom: number, direction: number): number {
  return clampZoom(zoom + direction * ZOOM_STEP);
}

/** What to show next to the control. Not a magnification; see above. */
export function zoomLabel(zoom: number): string {
  return zoom <= 0 ? 'none' : `${Math.round(clampZoom(zoom) * 100)}%`;
}

/**
 * Read stored settings, falling back field by field.
 *
 * Anything unreadable is replaced rather than thrown: a corrupt preference
 * must not be able to stop the camera opening.
 */
export function parseCameraSettings(raw: string | undefined): CameraSettings {
  if (!raw) return { ...DEFAULT_CAMERA_SETTINGS };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { ...DEFAULT_CAMERA_SETTINGS };
  }
  if (!parsed || typeof parsed !== 'object') {
    return { ...DEFAULT_CAMERA_SETTINGS };
  }
  const source = parsed as Partial<Record<keyof CameraSettings, unknown>>;
  return {
    zoom:
      typeof source.zoom === 'number'
        ? clampZoom(source.zoom)
        : DEFAULT_CAMERA_SETTINGS.zoom,
    torch:
      typeof source.torch === 'boolean'
        ? source.torch
        : DEFAULT_CAMERA_SETTINGS.torch,
    autofocus:
      source.autofocus === 'off' || source.autofocus === 'on'
        ? source.autofocus
        : DEFAULT_CAMERA_SETTINGS.autofocus,
  };
}

export async function loadCameraSettings(
  store: LocalStore,
): Promise<CameraSettings> {
  return parseCameraSettings(await store.getMeta(KEY));
}

export async function saveCameraSettings(
  store: LocalStore,
  settings: CameraSettings,
): Promise<void> {
  await store.setMeta(KEY, JSON.stringify(settings));
}
