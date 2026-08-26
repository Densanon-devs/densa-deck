/**
 * Pulling the collection's art onto the phone ahead of time.
 *
 * This renders a hidden `<Image>` and walks a queue through it, one at a time,
 * advancing on load or on failure. That looks like a strange way to download
 * something, and it is — but it is the only way that works.
 *
 * `Image.prefetch` takes a URL and nothing else on Android. No headers. And an
 * art request that does not identify itself is answered **HTTP 400** by
 * Scryfall's CDN — reproduced against the live service: `okhttp/4.9.2` gets
 * 400, `DensaDeck/0.2.2` gets 200. React Native's image loader sends exactly
 * that okhttp User-Agent by default, which is why every card in the app failed
 * while the same URL worked from a browser.
 *
 * A real `<Image source={{uri, headers}}>` can send them, and it fills the
 * same Fresco cache that the collection list and the card screen read from.
 * So the download goes through the component that can ask properly.
 *
 * One at a time on purpose: a queue of several hundred fired at once is rude
 * to Scryfall, gets throttled anyway, and would saturate the connection the
 * app also needs for syncing.
 */

import React, { useEffect, useState } from 'react';
import { Image, StyleSheet, View } from 'react-native';

import { artSource } from '../lib/images.ts';

interface Props {
  /** Printing ids, already deduplicated by `artQueue`. */
  queue: string[];
  onProgress: (done: number, total: number, failed: number) => void;
  onDone: (failed: number) => void;
}

export function ArtWarmer({ queue, onProgress, onDone }: Props) {
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState(0);

  useEffect(() => {
    setIndex(0);
    setFailed(0);
  }, [queue]);

  useEffect(() => {
    if (queue.length && index >= queue.length) onDone(failed);
    // `failed` is deliberately not a dependency: it changes on the way past
    // and would report done early.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, queue.length]);

  if (!queue.length || index >= queue.length) return null;

  const current = queue[index];
  if (!current) return null;

  const advance = (didFail: boolean) => {
    const nextFailed = didFail ? failed + 1 : failed;
    if (didFail) setFailed(nextFailed);
    onProgress(index + 1, queue.length, nextFailed);
    setIndex((i) => i + 1);
  };

  return (
    <View style={styles.hidden} pointerEvents="none">
      <Image
        // Keyed so React mounts a NEW Image per card rather than reusing one
        // whose load already settled — without this the queue stops after the
        // first card.
        key={current}
        source={artSource(current, 'normal')}
        style={styles.tiny}
        onLoad={() => advance(false)}
        onError={() => advance(true)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  // Off screen rather than `display: none`: a view that is not laid out may
  // never trigger the load at all.
  hidden: { position: 'absolute', left: -9999, top: -9999, opacity: 0 },
  tiny: { width: 1, height: 1 },
});
