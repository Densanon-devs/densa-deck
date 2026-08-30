/**
 * Reading the text off a card, on the phone.
 *
 * ML Kit's text recogniser, which runs entirely on the device — no network,
 * no account, no per-call cost. That is the whole reason it is here: the
 * desktop's OCR is only reachable when the desktop is, and a box of cards is
 * sorted where the box is.
 *
 * Kept behind this seam for two reasons. The native module cannot run under
 * Node, so every test in this project would be locked out of the scan path
 * without it; and if ML Kit is ever swapped out, nothing above here changes.
 */

export interface TextReader {
  /** Every line of text in the picture, top to bottom. Empty if none. */
  read(imageUri: string): Promise<string>;
}

/**
 * The real one.
 *
 * Imported lazily. The module pulls in a native dependency that does not
 * exist under Node, so importing it at the top of the file would take the
 * whole test suite down with it — including the tests for the matcher this
 * feeds, which is exactly the code most worth testing.
 */
export const deviceTextReader: TextReader = {
  async read(imageUri: string): Promise<string> {
    const { default: TextRecognition } = await import(
      '@react-native-ml-kit/text-recognition'
    );
    const result = await TextRecognition.recognize(imageUri);
    // Block by block rather than the flat `.text`, so the newlines line up
    // with what is physically on the card. The matcher leans on that: a
    // footer key must be found on ONE line, because a whole-text parse will
    // pair a number from one line with a set code from another.
    const blocks = result?.blocks ?? [];
    const lines = blocks.flatMap((block) =>
      (block.lines ?? []).map((line) => (line.text ?? '').trim()),
    );
    return lines.filter(Boolean).join('\n') || (result?.text ?? '');
  },
};

/**
 * One that reads nothing, for when the recogniser is unavailable.
 *
 * Returning empty text is honest: the matcher then reports that it could not
 * read the card, and the photo goes to the queue for the PC — which is
 * exactly the right outcome, and better than a crash on a screen someone is
 * holding over a box of cards.
 */
export const noTextReader: TextReader = {
  async read(): Promise<string> {
    return '';
  },
};
