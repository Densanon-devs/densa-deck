/**
 * A short sound when a card is actually filed.
 *
 * Auto scan photographs the frame every second or so, and most of those
 * pictures contain nothing — a hand moving, the edge of the box, the same
 * card still sitting there. The camera's own shutter sound fired on every
 * one of them, so sorting a box was a continuous rattle of clicks that
 * said nothing about whether anything had been recognised.
 *
 * So the shutter is silenced at the source and this replaces it, on the
 * one event worth hearing: a card went in. That makes the sound mean
 * something, and it means you can sort with your eyes on the cards rather
 * than on the screen — which is the whole point of a scanner that runs on
 * its own.
 *
 * Deliberately not played for a duplicate, a failed read, or a photo
 * queued for the PC. None of those are a card going into the collection,
 * and a sound that fires on nearly-everything is the rattle again.
 */

/** What the screen calls. Swappable, so the decision is testable. */
export interface Chime {
  play(): void;
}

/** Silence, for tests and for when audio is unavailable. */
export const silentChime: Chime = { play() {} };

/**
 * The real one.
 *
 * Loaded on first use and never awaited by the caller: a sound is a
 * courtesy, and a scan must not wait on the audio stack — nor fail
 * because of it. Every error is swallowed on purpose, because there is
 * no version of "the click did not play" that a person sorting a box
 * needs to be told about, and an unhandled rejection here would surface
 * as a crash over an entirely working scan.
 *
 * The module and the decoded sound are both cached. Creating a player
 * per card would allocate several hundred of them through one box.
 */
let player: { play(): void; seekTo(s: number): Promise<void> } | null = null;
let loading: Promise<void> | null = null;

function load(): Promise<void> {
  if (loading) return loading;
  loading = (async () => {
    const audio = await import('expo-audio');
    // Silent mode on iOS is a statement about notifications, not about a
    // sound the user asked an app to make in the foreground. Android
    // ignores this.
    await audio.setAudioModeAsync?.({ playsInSilentMode: true });
    player = audio.createAudioPlayer(require('../../assets/filed.wav'));
  })().catch(() => {
    // Leave `player` null. `play` becomes a no-op rather than throwing on
    // every card for the rest of the session.
  });
  return loading;
}

export const deviceChime: Chime = {
  play() {
    if (!player) {
      void load();
      return;
    }
    // Rewound first: a player left at the end of the clip plays nothing,
    // which would make the sound work once and then stop.
    void player.seekTo(0).then(() => player?.play()).catch(() => {});
  },
};
