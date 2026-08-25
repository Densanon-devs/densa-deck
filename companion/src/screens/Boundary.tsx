/**
 * Showing a failure instead of vanishing.
 *
 * An app that closes tells you nothing. An app that shows the error tells you
 * everything, and on a phone that isn't plugged into a computer it is the only
 * channel there is — a screenshot of this screen is a bug report.
 *
 * The text is selectable on purpose.
 */

import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { Crash } from '../lib/crash.ts';
import { recordCrash } from '../lib/crash.ts';

export function CrashScreen({
  crash,
  onDismiss,
}: {
  crash: Crash;
  onDismiss?: () => void;
}) {
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Something broke</Text>
      <Text style={styles.muted}>
        Densa Deck kept itself open so you can see what happened. Your
        collection is untouched.
      </Text>

      <View style={styles.box}>
        <Text style={styles.where} selectable>
          in {crash.where}
        </Text>
        <Text style={styles.message} selectable>
          {crash.message}
        </Text>
        {crash.stack ? (
          <Text style={styles.stack} selectable>
            {crash.stack.split('\n').slice(0, 12).join('\n')}
          </Text>
        ) : null}
      </View>

      {onDismiss ? (
        <Pressable style={styles.button} onPress={onDismiss}>
          <Text style={styles.buttonText}>Back to the app</Text>
        </Pressable>
      ) : null}
    </ScrollView>
  );
}

interface BoundaryProps {
  children: React.ReactNode;
  /** Named so the report says which part of the app failed. */
  where: string;
}

interface BoundaryState {
  crash: Crash | null;
}

/**
 * Catches errors thrown while rendering.
 *
 * React unmounts the whole tree below a boundary that has no handler, which in
 * a release build means a blank screen — arguably worse than a crash, because
 * nothing is reported at all.
 */
export class ErrorBoundary extends React.Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { crash: null };

  static getDerivedStateFromError() {
    // The real recording happens in componentDidCatch, which is the only one
    // of the two given the component stack.
    return {};
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    const crash = recordCrash(error, this.props.where);
    this.setState({
      crash: info.componentStack
        ? { ...crash, stack: crash.stack || info.componentStack }
        : crash,
    });
  }

  render() {
    if (this.state.crash) {
      return (
        <CrashScreen
          crash={this.state.crash}
          onDismiss={() => this.setState({ crash: null })}
        />
      );
    }
    return this.props.children;
  }
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0f1117' },
  content: { padding: 20, gap: 14 },
  title: { color: '#e53e3e', fontSize: 22, fontWeight: '700' },
  muted: { color: '#8a8f9c', lineHeight: 20 },
  box: {
    backgroundColor: '#1a1d27',
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 8,
  },
  where: { color: '#8a8f9c', fontSize: 12 },
  message: { color: '#e4e6eb', fontSize: 15, lineHeight: 21 },
  stack: { color: '#8a8f9c', fontSize: 11, lineHeight: 16 },
  button: {
    borderColor: '#2d3142',
    borderWidth: 1,
    borderRadius: 10,
    padding: 14,
    alignItems: 'center',
  },
  buttonText: { color: '#e4e6eb', fontSize: 16 },
});
