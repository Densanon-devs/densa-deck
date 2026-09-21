/**
 * Looking harder at the one line that identifies a card.
 *
 * A whole card photographed at arm's length gives the recogniser a name
 * in 40pt and a collector line in about 6. The name comes back perfectly
 * and the footer does not, which is the worst possible split: the app
 * can see there is a card and cannot say which one.
 *
 * Five real scans in a row failed exactly that way — Extract from
 * Darkness, Go for the Throat, Deadly Dispute, Royal Assassin,
 * Assassinate. Every one of them returned the artist and the copyright
 * line, which sit alongside the set code, and missed the set code
 * itself.
 *
 * The desktop has never had this problem because it crops to the footer
 * and enlarges it before reading. This is that, on the phone: a second
 * look at the bottom of the picture, blown up, and only when the first
 * read came back without a key. The common path pays nothing.
 */

/** Where the collector line lives, as a fraction of the picture. */
export interface Region {
  originX: number;
  originY: number;
  width: number;
  height: number;
}

/**
 * How much of the bottom to take.
 *
 * Generous on purpose. The frame guide asks for a card filling most of
 * the height, but people hold cards at angles and in sleeves, and a
 * crop that clips the very line it exists to read is worse than one
 * carrying some rules text along with it.
 */
export const FOOTER_FRACTION = 0.3;

/**
 * The bottom strip of a picture, full width.
 *
 * Full width rather than the left corner where the set code sits,
 * because a card photographed at an angle puts its bottom-left a long
 * way from the picture's bottom-left, and the collector number on the
 * right has to come along anyway — a set code with no number is not a
 * key.
 */
export function footerRegion(width: number, height: number): Region | null {
  if (!(width > 0) || !(height > 0)) return null;
  const strip = Math.max(1, Math.round(height * FOOTER_FRACTION));
  return {
    originX: 0,
    originY: Math.max(0, height - strip),
    width,
    height: strip,
  };
}

/**
 * How much to enlarge the strip before reading it.
 *
 * ML Kit wants glyphs with enough pixels to have a shape. Six-point text
 * in a photo of a whole card is a handful of pixels tall; doubling it
 * costs a few milliseconds and is the difference between a set code and
 * nothing. Capped so a high-resolution photo does not produce a bitmap
 * larger than the one it came from.
 */
export function enlargedWidth(width: number, cap = 2600): number {
  if (!(width > 0)) return 0;
  return Math.min(cap, Math.round(width * 2));
}

/**
 * Whether a second, closer look is worth taking.
 *
 * Only when the first read produced no usable key. A card that already
 * identified must not pay for a second OCR pass, and neither must an
 * empty frame during auto scan — which is most frames.
 */
export function needsCloserLook(
  text: string,
  keys: Array<[string, string]>,
): boolean {
  if (keys.length) return false;
  // Nothing at all read means the picture is the problem, not the
  // size of the lettering: no card, no focus, or a lens cap. Enlarging
  // a blank strip finds nothing and costs a pass on every idle frame.
  return (text || '').trim().length > 0;
}

/**
 * The bottom of a picture, cropped out and enlarged, as a file URI.
 *
 * Lazily imported for the same reason the recogniser is: the native
 * module cannot exist under Node, and importing it at the top would
 * lock every test in this project out of the scan path.
 *
 * Returns '' rather than throwing. A failed second look means the scan
 * behaves exactly as it did before there was one, which is the correct
 * outcome and not worth a message.
 */
export async function closerLookAtFooter(imageUri: string): Promise<string> {
  try {
    const { ImageManipulator, SaveFormat } = await import(
      'expo-image-manipulator'
    );
    const context = ImageManipulator.manipulate(imageUri);
    const first = await context.renderAsync();
    const region = footerRegion(first.width, first.height);
    if (!region) return '';

    const cropped = ImageManipulator.manipulate(imageUri);
    cropped.crop(region);
    // Enlarged AFTER cropping, so the pixels being added are spent on
    // the strip rather than on the artwork above it.
    cropped.resize({ width: enlargedWidth(region.width) });
    const out = await (await cropped.renderAsync()).saveAsync({
      compress: 1,
      format: SaveFormat.JPEG,
    });
    return out.uri || '';
  } catch {
    return '';
  }
}
