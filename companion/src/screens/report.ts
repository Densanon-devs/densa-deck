/**
 * What to do with a promise a screen fires and forgets.
 *
 * There are a lot of them — every screen loads itself in an effect — and on a
 * phone a rejected one is completely silent: React Native only tracks
 * unhandled rejections under `__DEV__`. The screen stays empty, and empty is
 * indistinguishable from "you own nothing yet".
 *
 * So every one of them gets this, and the failure lands somewhere a person
 * can read it.
 */

import { recordCrash } from '../lib/crash.ts';

export function reporting(
  where: string,
  show: (message: string) => void,
): (error: unknown) => void {
  return (error) => show(recordCrash(error, where, false).message);
}
