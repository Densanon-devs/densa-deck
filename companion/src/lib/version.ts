/**
 * Which build this is.
 *
 * Shown on the pairing screen because during an iteration loop the only thing
 * harder than fixing a bug is knowing whether the phone is running the fix. An
 * APK that looks identical to the broken one is how the wrong build gets
 * tested and the wrong conclusion drawn.
 *
 * Kept in step with app.json, package.json and build.gradle by a test — a
 * hardcoded version that has drifted is worse than none, because it is
 * believed.
 */
export const VERSION = '0.14.0';
