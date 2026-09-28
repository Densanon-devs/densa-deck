import { useEffect, useRef } from 'react';
import { BackHandler } from 'react-native';

/**
 * Android's back button closes this panel while it is open.
 *
 * Nothing handled back at all, so on a full-screen panel -- an open deck, a
 * card, Settings, the scanner inside a deck, the set picker -- it left the
 * app instead of going back a step. Real Modals already get this through
 * onRequestClose; these panels are ordinary views and need it said.
 *
 * React Native calls the most recently added handler first, and an inner
 * panel is always opened after the one it sits in, so the innermost open
 * panel is the one that closes.
 */
export function useBackClose(open: boolean, close: () => void): void {
  // The latest close, without re-registering (and so re-ordering) the
  // handler every render.
  const latest = useRef(close);
  latest.current = close;
  useEffect(() => {
    if (!open) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      latest.current();
      return true;   // handled: do not also leave the app
    });
    return () => sub.remove();
  }, [open]);
}
