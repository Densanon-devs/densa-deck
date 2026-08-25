/**
 * Sticky-immersive, the way Table of War does it.
 *
 * Android 15 forces edge-to-edge on anything targeting SDK 35 or above, so the
 * app already draws underneath the status bar and the navigation buttons —
 * which is why the offline banner was sitting behind the clock and the tab bar
 * behind the gesture pill. Edge-to-edge is not optional any more; the only
 * choice is whether the app handles it.
 *
 * This hides both system bars outright. A swipe from either edge brings them
 * back transiently and they hide themselves again, which is what
 * IMMERSIVE_STICKY meant before it was deprecated. `WindowInsetsController` is
 * the replacement that still works on 35 and 36 — the old
 * `setSystemUiVisibility` flags are no-ops there.
 *
 * This is a config plugin rather than an edit to `android/` because that
 * directory is generated and git-ignored: a hand-edit survives exactly until
 * the next prebuild, and then vanishes with no sign it was ever there.
 */

const { withMainActivity } = require('expo/config-plugins');

const IMPORTS = [
  'import androidx.core.view.WindowCompat',
  'import androidx.core.view.WindowInsetsCompat',
  'import androidx.core.view.WindowInsetsControllerCompat',
].join('\n');

const METHODS = `
  // Hide the status and navigation bars. Re-applied on every focus gain
  // because the system shows them again after a swipe, after a dialog, and
  // after returning from the background.
  override fun onWindowFocusChanged(hasFocus: Boolean) {
    super.onWindowFocusChanged(hasFocus)
    if (hasFocus) goImmersive()
  }

  private fun goImmersive() {
    WindowCompat.setDecorFitsSystemWindows(window, false)
    val controller = WindowInsetsControllerCompat(window, window.decorView)
    controller.hide(WindowInsetsCompat.Type.systemBars())
    controller.systemBarsBehavior =
      WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
  }
`;

/** Fails loudly. A plugin that silently matched nothing is the worst outcome. */
function replaceOnce(source, anchor, replacement, what) {
  if (!source.includes(anchor)) {
    throw new Error(
      `with-immersive: could not find ${what} in MainActivity. The Expo ` +
        `template changed; update the anchor rather than shipping a plugin ` +
        `that quietly does nothing.`,
    );
  }
  return source.replace(anchor, replacement);
}

module.exports = function withImmersive(config) {
  return withMainActivity(config, (mod) => {
    let source = mod.modResults.contents;

    if (source.includes('goImmersive')) return mod;

    source = replaceOnce(
      source,
      'import com.facebook.react.ReactActivity',
      `${IMPORTS}\nimport com.facebook.react.ReactActivity`,
      'the ReactActivity import',
    );

    source = replaceOnce(
      source,
      'super.onCreate(null)',
      'super.onCreate(null)\n    goImmersive()',
      'the onCreate body',
    );

    source = replaceOnce(
      source,
      '  override fun getMainComponentName()',
      `${METHODS}\n  override fun getMainComponentName()`,
      'getMainComponentName',
    );

    mod.modResults.contents = source;
    return mod;
  });
};
