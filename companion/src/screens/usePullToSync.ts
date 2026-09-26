import { useCallback, useState } from 'react';

import type { AppState } from '../lib/app-state.ts';
import { pullToSync } from '../lib/pull-refresh.ts';

/**
 * Pull-to-refresh for a screen: sync with the PC, then run `reload`.
 * Spread the result into a RefreshControl — see lib/pull-refresh.ts.
 */
export function usePullToSync(
  state: AppState,
  reload: () => Promise<unknown>,
  onProblem: (err: unknown) => void,
): { refreshing: boolean; onRefresh: () => void } {
  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = useCallback(() => {
    setRefreshing(true);
    void pullToSync(
      { solo: state.soloForever, sync: () => state.sync(), reload },
      onProblem,
    ).finally(() => setRefreshing(false));
  }, [state, reload, onProblem]);
  return { refreshing, onRefresh };
}
