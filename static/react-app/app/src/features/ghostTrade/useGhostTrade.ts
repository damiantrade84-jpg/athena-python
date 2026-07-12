import { useCallback, useEffect, useState } from 'react';
import {
  closeGhostPosition, dismissGhostSignal, executeGhostSignal, getCurrentGhostScan,
  getGhostGroups, getGhostHealth, getGhostPerformance, getGhostPositions,
  getGhostSignals, runGhostScan, setGhostAutoEnabled,
} from './api';
import type { GhostPerformance, GhostSignal, GhostTradeState } from './types';

const emptyPerformance: GhostPerformance = {
  totalClosedTrades: 0, meanR: null, medianR: null, winRate: null,
  profitFactor: null, totalR: 0,
};

export function useGhostTrade() {
  const [state, setState] = useState<GhostTradeState>({
    health: null, groups: [], signals: [], positions: [],
    performance: emptyPerformance, currentScan: null,
  });
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<GhostSignal | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [health, groups, signals, positions, performance, currentScan] = await Promise.all([
        getGhostHealth(), getGhostGroups(), getGhostSignals(), getGhostPositions(),
        getGhostPerformance(), getCurrentGhostScan(),
      ]);
      setState({ health, groups, signals, positions, performance, currentScan });
      setSelectedSignal((current) => current
        ? signals.find((item) => item.signalId === current.signalId) || null
        : null);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Ghost Trade request failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const intervalMs = state.health?.scanRunning ? 2_000 : 15_000;
    const timer = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(timer);
  }, [refresh, state.health?.scanRunning]);

  const mutate = useCallback(async (operation: () => Promise<unknown>) => {
    setMutating(true);
    try {
      await operation();
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Ghost Trade action failed');
    } finally {
      setMutating(false);
    }
  }, [refresh]);

  return {
    ...state, loading, mutating, error, selectedSignal,
    setSelectedSignal,
    refresh,
    scan: () => mutate(runGhostScan),
    execute: (id: string) => mutate(() => executeGhostSignal(id)),
    dismiss: (id: string) => mutate(() => dismissGhostSignal(id)),
    closePosition: (id: string) => mutate(() => closeGhostPosition(id)),
    toggleAuto: (enabled: boolean) => mutate(() => setGhostAutoEnabled(enabled)),
  };
}
