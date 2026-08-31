/**
 * Why it says Offline.
 *
 * That one word stood in for a dozen different problems, each with a different
 * fix: the wrong address, a firewall, Tailscale switched off on the phone, a
 * desktop that isn't serving, or — the one that actually happened — Android
 * refusing a plain-HTTP request before it ever left the handset, because a
 * release build blocks cleartext unless the manifest says otherwise.
 *
 * All of them looked identical from the outside. This shows what each address
 * did when asked, which is the difference between guessing and knowing.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { AppSnapshot, AppState } from '../lib/app-state.ts';
import type { TierSnapshot } from '../lib/protocol.ts';
import type { EndpointReport } from '../lib/client.ts';
import { checkArtReachable } from '../lib/images.ts';
import type { ArtReach } from '../lib/images.ts';
import { describeConnection } from '../lib/status.ts';
import { reporting } from './report.ts';

interface Props {
  state: AppState;
  onClose: () => void;
  /**
   * Running with no PC, deliberately.
   *
   * The screen changes shape rather than greying things out: on a
   * standalone phone the diagnostics are about a machine that does not
   * exist, and the only thing worth offering is the way to get one.
   */
  standalone?: boolean;
  /** Go and pair with a desktop. Absent when there is already one. */
  onConnectPc?: () => void;
  /**
   * Forget the paired desktop and go back to choosing.
   *
   * There was no way to do this from inside the app at all, and
   * uninstalling does not do it either: Android's auto-backup restores the
   * app's data on reinstall, pairing included. So a phone whose desktop
   * had revoked it came back paired to a machine that would never answer,
   * with nothing to press.
   */
  onDisconnectPc?: () => void;
}

/**
 * What free keeps, in one sentence.
 *
 * Read from the allowances the tier actually reported rather than written
 * out, so raising a limit changes this line instead of leaving it stating
 * last month's policy.
 */
function allowanceLine(tier: TierSnapshot): string {
  const decks = tier.allowances?.saved_decks;
  const groups = tier.allowances?.collections;
  const parts: string[] = [];
  if (typeof decks === 'number' && decks >= 0) {
    parts.push(`${decks} saved deck${decks === 1 ? '' : 's'}`);
  }
  if (typeof groups === 'number' && groups >= 0) {
    parts.push(`${groups} group${groups === 1 ? '' : 's'} of your own`);
  }
  return parts.length
    ? `Keeps ${parts.join(' and ')}. Scanning, filing and your whole `
      + 'collection are unlimited.'
    : 'Scanning, filing and your whole collection are unlimited.';
}

export function ConnectionScreen({
  state, onClose, standalone = false, onConnectPc, onDisconnectPc,
}: Props) {
  const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);
  /**
   * Which tier this phone is on, and what that allows.
   *
   * Shown because it was invisible: the limits are only met by bumping
   * into them, and "why did it stop me at three" is a question the app
   * should answer before it is asked rather than after.
   */
  const [tier, setTier] = useState<TierSnapshot | null>(null);

  useEffect(() => {
    void state.tier().then(setTier).catch(() => {});
  }, [state]);
  const [reports, setReports] = useState<EndpointReport[] | null>(null);
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null);
  const [art, setArt] = useState<ArtReach | null>(null);
  /**
   * What each machine thinks you own.
   *
   * The phone answers browsing from its own mirror; the PC answers analysis,
   * overlaps and anything catalogue-shaped. When those two disagree — cards
   * cleared here that have not reached the PC, or a phone reinstalled with an
   * empty mirror — every screen is individually telling the truth and the app
   * as a whole looks broken. Nowhere showed both numbers, so there was no way
   * to see that was what was happening.
   */
  const [counts, setCounts] = useState<
    { phone: number; desktop: number | null } | null
  >(null);

  useEffect(() => state.subscribe(setSnapshot), [state]);

  const run = useCallback(async () => {
    setBusy(true);
    setProblem('');
    try {
      setReports(await state.diagnose());
      // Deliberately separate. Card art comes from Scryfall over the public
      // internet; the collection comes from a machine on the tailnet. They
      // fail independently and the app used to report only one of them.
      setArt(await checkArtReachable());
      // A probe that answers proves nothing until a real request follows it,
      // so the sync runs too and the banner updates from the result.
      await state.sync();

      // Counted AFTER the sync, not before it. Read first, the phone's total
      // was the one from before this exchange — so the screen reported a
      // discrepancy it had just finished fixing, and pressing again appeared
      // to change the number by magic.
      const phone = (await state.totals()).cards;
      let desktop: number | null = null;
      try {
        const reply = await state.desktopCollections();
        desktop = reply.master?.cards ?? null;
      } catch {
        desktop = null;               // asleep or unreachable; say so, do not guess
      }
      setCounts({ phone, desktop });
    } finally {
      setBusy(false);
    }
  }, [state]);

  useEffect(() => {
    void run().catch(reporting('checking the connection', setProblem));
  }, [run]);

  const anyOk = reports?.some((r) => r.ok) ?? false;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.title}>Settings</Text>
        <Pressable onPress={onClose}>
          <Text style={styles.close}>Done</Text>
        </Pressable>
      </View>

      <Text style={styles.summary}>
        {standalone
          ? 'This phone is running on its own. Your collection, decks and '
            + 'groups all live here.'
          : describeConnection(
            snapshot ?? { connection: 'unknown', pendingEdits: 0 }).text}
      </Text>

      {/*
        What this phone may do. First, because it explains every limit
        below it, and because a paying customer opening settings on a
        phone that thinks it is free wants to see that immediately.
      */}
      {tier ? (
        <View style={[styles.tierBox, tier.is_pro && styles.tierBoxPro]}>
          <Text style={[styles.tierName, tier.is_pro && styles.tierNamePro]}>
            {tier.is_pro ? 'Densa Deck Pro' : 'Free'}
          </Text>
          <Text style={styles.muted}>
            {tier.is_pro
              ? 'Unlimited decks and groups, plus analysis and suggestions '
                + 'whenever your PC is in reach.'
              : allowanceLine(tier)}
          </Text>
          {!tier.is_pro ? (
            <Text style={styles.muted}>
              {standalone
                ? 'Pro is activated on the desktop app. Connect a PC to use it '
                  + 'here.'
                : 'Activate Pro on your desktop and this phone picks it up on '
                  + 'the next sync.'}
            </Text>
          ) : null}
        </View>
      ) : null}

      {/*
        The way back to a desktop, and the only PC-related thing a
        standalone phone should be shown. Framed as what it ADDS rather
        than what is missing: nothing here is broken without one.
      */}
      {standalone && onConnectPc ? (
        <View style={styles.pcOffer}>
          <Text style={styles.pcTitle}>Add a PC</Text>
          <Text style={styles.summary}>
            A desktop adds deck analysis, card suggestions and combo
            detection, and it fetches the card index in seconds instead of
            minutes. Everything already on this phone stays exactly as it is.
          </Text>
          <Pressable style={styles.pcButton} onPress={onConnectPc}>
            <Text style={styles.pcButtonText}>Connect a PC</Text>
          </Pressable>
        </View>
      ) : null}

      {/*
        Both sides' totals, side by side. Not a diagnostic curiosity: when the
        phone says nothing and the PC says four hundred, every screen in the
        app is correct and the app still looks wrong, and this is the only
        place that can say which is which.
      */}
      {/*
        Everything below is about reaching a desktop. On a phone with none,
        it is diagnostics for a machine that does not exist — a wall of
        red about a problem the user does not have.
      */}
      {!standalone && counts ? (
        <View style={styles.tally}>
          <Text style={styles.tallyRow}>
            This phone: <Text style={styles.tallyNum}>{counts.phone}</Text> cards
          </Text>
          <Text style={styles.tallyRow}>
            Your PC:{' '}
            <Text style={styles.tallyNum}>
              {counts.desktop === null ? '—' : counts.desktop}
            </Text>{' '}
            {counts.desktop === null ? '(couldn’t ask)' : 'cards'}
          </Text>
          {/*
            The repair, next to the numbers that show it is needed. A pulled
            event is remembered by uid so it is never applied twice — right,
            until one was recorded and NOT applied, after which the phone
            skips it forever and pulling to refresh can never help.
          */}
          {counts.desktop !== null && counts.desktop !== counts.phone ? (
            <Pressable
              style={styles.rebuild}
              onPress={() => {
                setBusy(true);
                void state
                  .rebuildFromDesktop()
                  .then(() => run())
                  .catch(reporting('rebuilding from your PC', setProblem))
                  .finally(() => setBusy(false));
              }}
            >
              <Text style={styles.rebuildText}>
                {busy ? 'Copying…' : '↻  Copy my PC’s cards down again'}
              </Text>
              <Text style={styles.muted}>
                Throws this phone’s copy away and asks for all of it fresh.
                Anything you changed here and haven’t sent is kept and goes
                first.
              </Text>
            </Pressable>
          ) : null}

          {counts.desktop !== null && counts.desktop !== counts.phone ? (
            <Text style={styles.tallyWarn}>
              {snapshot?.pendingEdits
                ? `They disagree because ${snapshot.pendingEdits} edit` +
                  `${snapshot.pendingEdits === 1 ? '' : 's'} from this phone ` +
                  'haven’t reached your PC. Syncing settles it.'
                : 'They disagree and there is nothing waiting to send, so one ' +
                  'of them has cards the other has never heard of. Syncing ' +
                  'copies the PC’s cards down; it will not delete anything.'}
            </Text>
          ) : null}
        </View>
      ) : null}

      {problem ? <Text style={styles.problem}>{problem}</Text> : null}

      {standalone ? null : reports === null ? (
        <Text style={styles.muted}>Trying every address…</Text>
      ) : (
        reports.map((report) => (
          <View key={report.label} style={styles.row}>
            <View style={styles.rowHead}>
              <Text style={[styles.label, report.ok && styles.labelOk]}>
                {report.ok ? '✓' : '✕'} {report.label}
              </Text>
              <Text style={styles.url} selectable numberOfLines={1}>
                {report.url || '—'}
              </Text>
            </View>
            <Text style={styles.detail} selectable>
              {report.detail}
            </Text>
          </View>
        ))
      )}

      {art ? (
        <View style={styles.row}>
          <View style={styles.rowHead}>
            <Text style={[styles.label, art.ok && styles.labelOk]}>
              {art.ok ? '✓' : '✗'} Card art
            </Text>
            <Text style={styles.url}>cards.scryfall.io</Text>
          </View>
          <Text style={styles.detail} selectable>{art.detail}</Text>
        </View>
      ) : null}

      {!standalone && reports && !anyOk ? (
        <View style={styles.advice}>
          <Text style={styles.adviceTitle}>Nothing answered</Text>
          <Text style={styles.muted}>
            Things worth checking, in the order they usually turn out to be
            wrong:
          </Text>
          <Text style={styles.muted}>
            1. Densa Deck is open on the PC and phone scanning is switched on
            in Settings.
          </Text>
          <Text style={styles.muted}>
            2. Tailscale is running on this phone, not just on the PC.
          </Text>
          <Text style={styles.muted}>
            3. The PC has moved to a different Wi-Fi address since you paired.
            The tunnel address is the one that survives that — if Tailscale
            answers and Wi-Fi does not, nothing is actually wrong.
          </Text>
          <Text style={styles.muted}>
            4. Windows Firewall is refusing the connection. It only ever asks
            once, and answering “Cancel” that one time is permanent.
          </Text>
        </View>
      ) : null}

      {!standalone ? (
        <Pressable
          style={styles.button}
          disabled={busy}
          onPress={() => {
            void run().catch(reporting('checking the connection', setProblem));
          }}
        >
          <Text style={styles.buttonText}>
            {busy ? 'Trying…' : 'Try again'}
          </Text>
        </Pressable>
      ) : null}

      {/*
        Letting go of a desktop.
        
        Two taps, because it is not undoable without the PC in front of
        you — but it takes nothing away: the collection, decks and groups
        are this phone's own and stay exactly as they are. Said out loud,
        because "disconnect" reads like "delete" to anyone who has not
        been told otherwise.
      */}
      {!standalone && onDisconnectPc ? (
        <View style={styles.disconnect}>
          {confirmingDisconnect ? (
            <>
              <Text style={styles.muted}>
                Your collection, decks and groups stay on this phone. You
                will lose deck analysis and suggestions until you connect a
                PC again.
              </Text>
              <Pressable style={styles.danger} onPress={onDisconnectPc}>
                <Text style={styles.dangerText}>
                  Yes, disconnect this PC
                </Text>
              </Pressable>
              <Pressable onPress={() => setConfirmingDisconnect(false)}>
                <Text style={styles.close}>Keep it</Text>
              </Pressable>
            </>
          ) : (
            <Pressable onPress={() => setConfirmingDisconnect(true)}>
              <Text style={styles.dangerQuiet}>Disconnect this PC</Text>
            </Pressable>
          )}
        </View>
      ) : null}

      <Text style={styles.footnote}>
        Your collection is on this phone either way. Nothing here is lost while
        the PC is out of reach — edits wait and go across when it comes back.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 16, gap: 14 },
  header: { flexDirection: 'row', alignItems: 'center' },
  tierBox: {
    borderColor: '#2b3040',
    borderRadius: 10,
    borderWidth: 1,
    gap: 6,
    marginTop: 16,
    padding: 14,
  },
  tierBoxPro: { borderColor: '#2f6b3f' },
  tierName: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  tierNamePro: { color: '#68d391' },
  disconnect: {
    alignItems: 'center',
    borderTopColor: '#2b3040',
    borderTopWidth: 1,
    gap: 10,
    marginTop: 22,
    paddingTop: 16,
  },
  danger: {
    alignItems: 'center',
    alignSelf: 'stretch',
    borderColor: '#8c2f2f',
    borderRadius: 8,
    borderWidth: 1,
    paddingVertical: 11,
  },
  dangerText: { color: '#e08b8b', fontSize: 15, fontWeight: '600' },
  dangerQuiet: { color: '#8a8f9c', fontSize: 14 },
  pcOffer: {
    borderColor: '#2b3040',
    borderRadius: 10,
    borderWidth: 1,
    gap: 8,
    marginTop: 16,
    padding: 14,
  },
  pcTitle: { color: '#e4e6eb', fontSize: 16, fontWeight: '700' },
  pcButton: {
    alignItems: 'center',
    borderColor: '#2f6f9f',
    borderRadius: 8,
    borderWidth: 1,
    paddingVertical: 11,
  },
  pcButtonText: { color: '#7db8e8', fontSize: 15, fontWeight: '600' },
  title: { color: '#e4e6eb', fontSize: 22, fontWeight: '700', flex: 1 },
  close: { color: '#e53e3e', fontSize: 16, fontWeight: '600' },
  tally: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 4,
  },
  tallyRow: { color: '#c9ced9', fontSize: 14 },
  tallyNum: { color: '#e4e6eb', fontWeight: '700' },
  tallyWarn: { color: '#ecc94b', fontSize: 13, lineHeight: 19, marginTop: 4 },
  // Filled, not outlined. Outlined, it sat among outlined panels and read as
  // one — the user pressed it on a guess, which is not a thing a control
  // should require.
  rebuild: {
    backgroundColor: '#276749',
    borderColor: '#38a169',
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    gap: 4,
    marginTop: 8,
  },
  rebuildText: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: '700',
    textAlign: 'center',
  },
  muted: { color: '#8a8f9c', fontSize: 13, lineHeight: 20 },
  summary: { color: '#e4e6eb', fontSize: 15, lineHeight: 22 },
  row: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 6,
  },
  rowHead: { gap: 2 },
  label: { color: '#e53e3e', fontSize: 15, fontWeight: '700' },
  labelOk: { color: '#38a169' },
  url: { color: '#8a8f9c', fontSize: 12 },
  detail: { color: '#e4e6eb', fontSize: 13, lineHeight: 19 },
  advice: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 8,
  },
  adviceTitle: { color: '#ecc94b', fontSize: 15, fontWeight: '700' },
  button: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  buttonText: { color: '#e4e6eb', fontSize: 16 },
  problem: { color: '#e53e3e', fontSize: 13, lineHeight: 19 },
  footnote: { color: '#8a8f9c', fontSize: 12, lineHeight: 18 },
});
