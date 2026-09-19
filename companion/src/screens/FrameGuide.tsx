/**
 * Where to hold the card.
 *
 * The scanner matches on the footer — the collector number and set code
 * along the bottom edge — and nothing on screen ever said so. People line
 * a card up by its art, because the art is the part that looks like the
 * card, and the art is the half that is ignored. One tester's photo showed
 * a beautifully centred card whose footer was off under the shutter.
 *
 * So this draws the shape being looked for and then says which end of it
 * matters. Ghosted on purpose: it is a hint over a live image, and a
 * heavy overlay fights the thing it is meant to help you see.
 *
 * Corner brackets rather than a full rectangle, for the same reason every
 * camera does it that way — four short marks read as "put it here" while
 * a closed box reads as a thing on the screen, and they leave the middle
 * of the picture alone.
 */

import React from 'react';
import { StyleSheet, Text, View } from 'react-native';

/**
 * A Magic card is 63 × 88 mm.
 *
 * The real ratio, not a guess, so a card held to fill the outline is held
 * square to the camera — which is most of what makes a footer readable.
 */
const CARD_RATIO = 63 / 88;

interface Props {
  /**
   * Dimmed right down once a capture is in flight.
   *
   * The guide has done its job by then, and leaving it at full strength
   * over a frozen frame reads as though it is still asking for something.
   */
  busy?: boolean;
}

export function FrameGuide({ busy = false }: Props) {
  return (
    <View
      // Never in the way of the shutter or the tap-to-focus underneath.
      pointerEvents="none"
      style={[styles.wrap, busy && styles.faded]}
    >
      <View style={styles.card}>
        <View style={[styles.corner, styles.tl]} />
        <View style={[styles.corner, styles.tr]} />
        <View style={[styles.corner, styles.bl]} />
        <View style={[styles.corner, styles.br]} />

        {/*
          The band that actually decides the answer. Brighter than the
          corners because it is the part worth aiming, and it sits inside
          the card outline rather than on the preview edge so it moves
          with the shape rather than with the screen.
        */}
        <View style={styles.footer}>
          <Text style={styles.hint}>set code · number</Text>
        </View>
      </View>
    </View>
  );
}

const GHOST = '#ffffff55';
const KEY = '#7db8e8aa';

const styles = StyleSheet.create({
  wrap: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 14,
  },
  faded: { opacity: 0.25 },
  card: {
    aspectRatio: CARD_RATIO,
    // Tall rather than wide: the preview is a landscape box and a card is
    // portrait, so height is what runs out first.
    height: '92%',
    maxWidth: '92%',
  },
  corner: {
    borderColor: GHOST,
    height: 26,
    position: 'absolute',
    width: 26,
  },
  tl: { borderLeftWidth: 2, borderTopWidth: 2, left: 0, top: 0,
    borderTopLeftRadius: 6 },
  tr: { borderRightWidth: 2, borderTopWidth: 2, right: 0, top: 0,
    borderTopRightRadius: 6 },
  bl: { borderBottomWidth: 2, borderLeftWidth: 2, bottom: 0, left: 0,
    borderBottomLeftRadius: 6 },
  br: { borderBottomWidth: 2, borderRightWidth: 2, bottom: 0, right: 0,
    borderBottomRightRadius: 6 },
  footer: {
    borderColor: KEY,
    borderRadius: 4,
    borderWidth: 1,
    bottom: '3%',
    height: '9%',
    justifyContent: 'center',
    left: '6%',
    position: 'absolute',
    width: '52%',
  },
  hint: {
    color: KEY,
    fontSize: 9,
    letterSpacing: 0.6,
    paddingLeft: 5,
    textTransform: 'uppercase',
  },
});
