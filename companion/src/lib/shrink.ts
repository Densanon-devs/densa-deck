/**
 * Making a photo small enough to keep.
 *
 * A queued scan has to sit on the phone until the PC is reachable, and a
 * frame straight off the camera is several megabytes. A box of three hundred
 * cards would be a gigabyte of SQLite, which is not a queue so much as a
 * disk-space incident.
 *
 * The size was chosen by measurement, not taste. Running the desktop's real
 * capture pipeline over the same card at descending sizes, identification —
 * card outline, name, and the collector line the match actually turns on —
 * was unchanged from full resolution all the way down to 1200px on the long
 * edge. 2000px is that floor with a wide margin for real photographs, which
 * are noisier and worse-lit than a test image, and lands around 80-150KB.
 *
 * Only for the QUEUE. A live scan still sends the full frame: the PC is
 * right there, nothing is being stored, and there is no reason to hand the
 * identifier less than it could have had.
 */

import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';

/** Long edge, in pixels, for a photo that has to be stored. */
export const QUEUE_LONG_EDGE = 2000;

/**
 * Shrink a captured frame for storage.
 *
 * Returns a `data:` URI, the same shape the live path sends, so a drained
 * scan travels the identical route to one identified on the spot.
 *
 * Never throws. If shrinking fails the original is kept: a large queued
 * photo is worse than a small one and far better than a lost card.
 */
export async function shrinkForQueue(
  base64Jpeg: string,
  longEdge = QUEUE_LONG_EDGE,
): Promise<string> {
  const uri = base64Jpeg.startsWith('data:')
    ? base64Jpeg
    : `data:image/jpeg;base64,${base64Jpeg}`;
  try {
    const context = ImageManipulator.manipulate(uri);
    // Height alone keeps the aspect ratio, and a card photographed in
    // portrait is taller than it is wide — which is the orientation the
    // camera screen asks for.
    context.resize({ height: longEdge });
    const image = await context.renderAsync();
    const out = await image.saveAsync({
      base64: true,
      compress: 0.8,
      format: SaveFormat.JPEG,
    });
    return out.base64 ? `data:image/jpeg;base64,${out.base64}` : uri;
  } catch {
    return uri;
  }
}
