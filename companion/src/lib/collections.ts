/**
 * Naming a collection.
 *
 * Small rules, but every one of them is a way to end up with a collection you
 * cannot tell apart from another one, or cannot see the name of, or cannot
 * delete because you cannot pick it out of a list. Names are also the thing
 * the desktop shows, so they travel.
 */

export interface NameVerdict {
  ok: boolean;
  /** The name to actually use. Trimmed and collapsed. */
  name: string;
  reason?: string;
}

export const MAX_COLLECTION_NAME = 60;

/**
 * The name a collection should be created with, or why it cannot be.
 *
 * Whitespace is collapsed rather than merely trimmed: "Deck  box" and
 * "Deck box" are the same name to a person, and two collections that look
 * identical in a list are worse than a rejection.
 */
export function checkCollectionName(
  raw: string,
  existing: Array<{ name: string }> = [],
): NameVerdict {
  const name = (raw || '').replace(/\s+/g, ' ').trim();

  if (!name) {
    return { ok: false, name, reason: 'Give the collection a name.' };
  }
  if (name.length > MAX_COLLECTION_NAME) {
    return {
      ok: false,
      name,
      reason: `Keep it under ${MAX_COLLECTION_NAME} characters.`,
    };
  }
  const clash = existing.find(
    (c) => c.name.replace(/\s+/g, ' ').trim().toLowerCase() === name.toLowerCase(),
  );
  if (clash) {
    // Case-insensitively, because "trade binder" and "Trade Binder" are the
    // same shelf and nobody would remember which held what.
    return {
      ok: false,
      name,
      reason: `You already have a collection called ${clash.name}.`,
    };
  }
  return { ok: true, name };
}
