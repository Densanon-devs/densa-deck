/**
 * When to buzz while auto scan is running.
 *
 * Auto scan photographs the frame every second or so and most of those
 * pictures hold nothing — a hand, the edge of the box, empty table. The
 * camera shutter used to click on every one of them, which is a constant
 * rattle that reports nothing.
 *
 * A buzz is worth feeling when a CARD has been seen, whether or not it
 * could be placed: "I have got something" and "I looked at that and could
 * not read it" are both news, and both mean stop moving your hand. What
 * is not news is the same card still sitting in frame a second later,
 * which is the majority of frames during a normal scan and would put the
 * rattle straight back.
 *
 * So the rule is per SIGHTING, not per photograph: buzz when what the
 * camera is looking at changes into something, and stay quiet while it
 * stays that way.
 */

/** What one photograph turned out to contain. */
export type Sighting =
  /** No card, or nothing legible at all. */
  | { kind: 'nothing' }
  /** A card was there and was placed. */
  | { kind: 'card'; name: string }
  /** Something card-like was there and could not be placed. */
  | { kind: 'unreadable' };

/**
 * How long the same card stays "the one we just buzzed for".
 *
 * Long enough to cover a card held still while the loop ticks, short
 * enough that deliberately re-scanning the same card — the second copy of
 * a playset — still registers. Matches the repeat guard that stops the
 * same card filing twice, so the buzz and the filing agree about what
 * counts as a new card.
 */
export const SAME_CARD_MS = 4000;

export class BuzzGuard {
  private lastCard = '';
  private lastAt = 0;
  private buzzedUnreadable = false;

  /** Whether this sighting is worth a buzz. */
  consider(sighting: Sighting, now: number): boolean {
    if (sighting.kind === 'nothing') {
      // An empty frame is the reset. Taking the card away and putting
      // another unreadable one down should buzz again — otherwise the
      // second one is silent and reads as the app having stopped.
      this.buzzedUnreadable = false;
      this.lastCard = '';
      return false;
    }

    if (sighting.kind === 'unreadable') {
      // Once per run of failures. A card the phone cannot place sits
      // there failing every tick until the loop gives up, and buzzing
      // each time is the rattle with a motor.
      if (this.buzzedUnreadable) return false;
      this.buzzedUnreadable = true;
      return true;
    }

    // A placed card. Quiet while it is the same one still in frame.
    const same = sighting.name === this.lastCard
      && now - this.lastAt < SAME_CARD_MS;
    this.lastCard = sighting.name;
    this.lastAt = now;
    // Reset here too: a card that failed, then succeeded on the next
    // frame, is a new situation.
    this.buzzedUnreadable = false;
    return !same;
  }

  /** Start again, e.g. when the loop is turned off and on. */
  reset(): void {
    this.lastCard = '';
    this.lastAt = 0;
    this.buzzedUnreadable = false;
  }
}
