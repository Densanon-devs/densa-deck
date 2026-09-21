/**
 * A set's symbol, in a colour you can see.
 *
 * Fetched rather than bundled: there are about a thousand sets and a
 * new one every few weeks, so shipping them would be a thousand files
 * that go stale on a schedule. Hotlinking Scryfall is the rule for
 * their art anyway.
 *
 * Fetched rather than handed to `SvgUri`, which would be the obvious
 * thing, because the markup has to be rewritten before it renders: the
 * symbols are black, the screen is black, and neither the `color` nor
 * the `fill` prop can override a fill the path already carries. See
 * `recolourSvg`.
 *
 * Cached across the whole app, not per component. A pick list of
 * twenty-nine printings is often twenty-nine rows of a dozen distinct
 * sets, and re-fetching per row would be both slow and rude to a
 * service giving us the file for nothing.
 */

import React, { useEffect, useState } from 'react';
import { View } from 'react-native';
import { SvgXml } from 'react-native-svg';

import { recolourSvg } from '../lib/set-symbol.ts';

/** Markup by URI, once per session. */
const cache = new Map<string, string>();
/** Fetches in flight, so twenty rows of one set make one request. */
const inFlight = new Map<string, Promise<void>>();

interface Props {
  uri: string;
  size?: number;
  colour?: string;
}

export function SetSymbol({ uri, size = 26, colour = '#e4e6eb' }: Props) {
  const [xml, setXml] = useState<string>(() => cache.get(uri) ?? '');

  useEffect(() => {
    if (!uri) return;
    const held = cache.get(uri);
    if (held) {
      setXml(held);
      return;
    }
    let live = true;
    const load = inFlight.get(uri) ?? (async () => {
      try {
        const response = await fetch(uri, {
          headers: { Accept: 'image/svg+xml' },
        });
        if (!response.ok) return;
        const text = await response.text();
        if (text.includes('<svg')) cache.set(uri, text);
      } catch {
        // A missing symbol is a blank space in a row that still says
        // the set's name, the number and the year. Not worth a word.
      } finally {
        inFlight.delete(uri);
      }
    })();
    inFlight.set(uri, load);
    void load.then(() => {
      if (live) setXml(cache.get(uri) ?? '');
    });
    return () => { live = false; };
  }, [uri]);

  // The box exists whether or not a symbol arrives, so the names beside
  // it stay in a straight line instead of shuffling as icons load.
  if (!xml) return <View style={{ height: size, width: size }} />;
  return (
    <SvgXml
      xml={recolourSvg(xml, colour)}
      width={size}
      height={size}
    />
  );
}
