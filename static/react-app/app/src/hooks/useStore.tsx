import { createContext, useContext, useState, useCallback, useRef, useEffect, useMemo } from 'react';
import type { PanelId, Signal, Position, GuardianStatus, NewsItem, SessionHours, TvChartIntent, ScalpWorkbenchIntent } from '@/types';
import { syncSignalsToGlobal } from '@/lib/globalState';
import apiClient from '@/lib/apiClient';
import { useLivePrices } from '@/hooks/useLivePrices';

interface AppState {
  activePanel: PanelId;
  signals: Signal[];
  positions: Position[];
  guardian: GuardianStatus;
  news: NewsItem[];
  sessions: SessionHours[];
  isAutoTrade: boolean;
  isTestMode: boolean;
  isLoading: boolean;
  toast: { message: string; type: 'success' | 'error' | 'info' } | null;
  /** Persistent scan result caches — survive panel navigation */
  scanCacheA: unknown[] | null;
  scanCacheB: unknown[] | null;
  scalpLabScanCache: unknown | null;
  scalpLabSelectedCache: unknown | null;
  scanCacheAMeta: { count: number; scannedAt: string } | null;
  /** Engine B: `pairsScanned` comes from API `totalPairs` (universe size for this scan). */
  scanCacheBMeta: { count: number; scannedAt: string; pairsScanned?: number; scanFunnel?: Record<string, number> } | null;
  /** Workflow intents — local only, not persisted */
  tvChartIntent: TvChartIntent | null;
  scalpWorkbenchIntent: ScalpWorkbenchIntent | null;
}

interface AppActions {
  setActivePanel: (panel: PanelId) => void;
  refreshSignals: () => void;
  refreshPositions: () => void;
  refreshGuardian: () => void;
  toggleAutoTrade: () => void;
  toggleTestMode: () => void;
  executeSignal: (signalId: string) => void;
  closePosition: (positionId: string) => void;
  showToast: (message: string, type: 'success' | 'error' | 'info') => void;
  getLivePrice: (pair: string) => number | undefined;
  setScanCacheA: (signals: unknown[], meta?: { count: number; scannedAt: string }) => void;
  setScanCacheB: (signals: unknown[], meta?: { count: number; scannedAt: string; pairsScanned?: number; scanFunnel?: Record<string, number> }) => void;
  setScalpLabScanCache: (result: unknown | null) => void;
  setScalpLabSelectedCache: (signal: unknown | null) => void;
  setTvChartIntent: (intent: TvChartIntent) => void;
  clearTvChartIntent: () => void;
  setScalpWorkbenchIntent: (intent: ScalpWorkbenchIntent) => void;
  clearScalpWorkbenchIntent: () => void;
}

const StoreContext = createContext<(AppState & AppActions) | null>(null);

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const [activePanel, setActivePanel] = useState<PanelId>('dashboard');
  const [signals, setSignals] = useState<Signal[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [guardian, setGuardian] = useState<GuardianStatus>({
    overall: 'healthy',
    bootChecks: [],
    circuitBreaker: false,
    dailyLoss: 0,
    dailyLossLimit: 0,
    openRisk: 0,
    maxOpenRisk: 0,
    divergence: false,
  });
  const [news] = useState<NewsItem[]>([]);
  const [sessions] = useState<SessionHours[]>([
    { session: 'London', start: '08:00', end: '16:00', active: false },
    { session: 'New York', start: '13:00', end: '21:00', active: false },
    { session: 'Tokyo', start: '00:00', end: '08:00', active: false },
    { session: 'Sydney', start: '22:00', end: '06:00', active: false },
  ]);
  const [isAutoTrade, setIsAutoTrade] = useState(false);
  const [isTestMode, setIsTestMode] = useState(false);
  const [isLoading] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);

  // Persistent scan caches — keyed by engine, survive panel navigation
  const [scanCacheA, setScanCacheAState] = useState<unknown[] | null>(null);
  const [scanCacheB, setScanCacheBState] = useState<unknown[] | null>(null);
  const [scalpLabScanCache, setScalpLabScanCache] = useState<unknown | null>(null);
  const [scalpLabSelectedCache, setScalpLabSelectedCache] = useState<unknown | null>(null);
  const [scanCacheAMeta, setScanCacheAMeta] = useState<{ count: number; scannedAt: string } | null>(null);
  const [scanCacheBMeta, setScanCacheBMeta] = useState<{ count: number; scannedAt: string; pairsScanned?: number; scanFunnel?: Record<string, number> } | null>(null);
  const [tvChartIntent, setTvChartIntentState] = useState<TvChartIntent | null>(null);
  const [scalpWorkbenchIntent, setScalpWorkbenchIntentState] = useState<ScalpWorkbenchIntent | null>(null);

  const setTvChartIntent = useCallback((intent: TvChartIntent) => {
    setTvChartIntentState(intent);
  }, []);

  const clearTvChartIntent = useCallback(() => {
    setTvChartIntentState(null);
  }, []);

  const setScalpWorkbenchIntent = useCallback((intent: ScalpWorkbenchIntent) => {
    setScalpWorkbenchIntentState(intent);
  }, []);

  const clearScalpWorkbenchIntent = useCallback(() => {
    setScalpWorkbenchIntentState(null);
  }, []);

  const setScanCacheA = useCallback((signals: unknown[], meta?: { count: number; scannedAt: string }) => {
    setScanCacheAState(signals);
    if (meta) setScanCacheAMeta(meta);
  }, []);

  const setScanCacheB = useCallback((signals: unknown[], meta?: { count: number; scannedAt: string; pairsScanned?: number; scanFunnel?: Record<string, number> }) => {
    setScanCacheBState(signals);
    if (meta) setScanCacheBMeta(meta);
  }, []);

  const toastTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info') => {
    if (toastTimeout.current !== null) clearTimeout(toastTimeout.current);
    setToast({ message, type });
    toastTimeout.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const refreshSignals = useCallback(async () => {
    try {
      const res = await apiClient.getJson('/api/last-scan') as { signals?: Signal[] };
      if (res && Array.isArray(res.signals)) {
        setSignals(res.signals);
        showToast('Signals refreshed', 'success');
      }
    } catch {
      showToast('Failed to refresh signals', 'error');
    }
  }, [showToast]);

  const refreshPositions = useCallback(async () => {
    try {
      const res = await apiClient.getJson('/api/open-trades-timed') as { positions?: Position[] } | Position[];
      let arr: Position[] = [];
      if (Array.isArray(res)) arr = res;
      else if (res && Array.isArray((res as { positions?: Position[] }).positions)) {
        arr = (res as { positions: Position[] }).positions;
      }
      setPositions(arr);
      showToast('Positions refreshed', 'success');
    } catch {
      showToast('Failed to refresh positions', 'error');
    }
  }, [showToast]);

  const refreshGuardian = useCallback(async () => {
    try {
      const res = await apiClient.getJson('/api/guardian/status') as GuardianStatus | null;
      if (res && typeof res === 'object') {
        setGuardian(res);
        showToast('Guardian refreshed', 'success');
      }
    } catch {
      showToast('Failed to refresh guardian', 'error');
    }
  }, [showToast]);

  // Initial fetch on mount
  useEffect(() => {
    refreshSignals();
    refreshPositions();
    refreshGuardian();
  }, [refreshSignals, refreshPositions, refreshGuardian]);

  const toggleAutoTrade = useCallback(() => {
    setIsAutoTrade(prev => {
      showToast(!prev ? 'Auto-trade enabled' : 'Auto-trade disabled', 'info');
      return !prev;
    });
  }, [showToast]);

  const toggleTestMode = useCallback(() => {
    setIsTestMode(prev => {
      showToast(!prev ? 'Test mode enabled' : 'Test mode disabled', 'info');
      return !prev;
    });
  }, [showToast]);

  const executeSignal = useCallback((signalId: string) => {
    const signal = signals.find(s => s.id === signalId);
    if (!signal) return;
    const newPosition: Position = {
      id: `pos_${Date.now()}`,
      pair: signal.pair,
      direction: signal.direction,
      entry: signal.entry,
      size: 0.5,
      sl: signal.sl,
      tp: signal.tp,
      pnl: 0,
      pnlPercent: 0,
      openTime: new Date().toISOString(),
      broker: 'MT5',
      type: 'market',
      status: 'open',
    };
    setPositions(prev => [newPosition, ...prev]);
    showToast(`Executed ${signal.direction} ${signal.pair}`, 'success');
  }, [signals, showToast]);

  const closePosition = useCallback((positionId: string) => {
    setPositions(prev => prev.filter(p => p.id !== positionId));
    showToast('Position closed', 'info');
  }, [showToast]);

  // Sync React state to global window.AppState
  useEffect(() => {
    syncSignalsToGlobal(signals);
    window.AppState.activePanel = activePanel;
  }, [signals, activePanel]);

  const { prices } = useLivePrices();
  const livePrices = useMemo(() => {
    const map: Record<string, number> = {};
    for (const [key, entry] of Object.entries(prices)) {
      const price = typeof entry === 'object' && entry != null ? (entry as { price?: number }).price : undefined;
      if (typeof price === 'number' && Number.isFinite(price) && price > 0) {
        map[key.toUpperCase()] = price;
      }
    }
    return map;
  }, [prices]);

  const livePriceGetter = useCallback((pair: string) => {
    if (!pair) return undefined;
    const key = pair.toUpperCase().trim();
    return livePrices[key] ?? livePrices[key.replace(/[\s/_:-]/g, '')];
  }, [livePrices]);

  return (
    <StoreContext.Provider value={{
      activePanel, signals, positions, guardian, news, sessions,
      isAutoTrade, isTestMode, isLoading, toast,
      scanCacheA, scanCacheB, scalpLabScanCache, scalpLabSelectedCache, scanCacheAMeta, scanCacheBMeta,
      tvChartIntent, scalpWorkbenchIntent,
      setActivePanel, refreshSignals, refreshPositions,
      refreshGuardian, toggleAutoTrade, toggleTestMode, executeSignal,
      closePosition, showToast, getLivePrice: livePriceGetter,
      setScanCacheA, setScanCacheB, setScalpLabScanCache, setScalpLabSelectedCache,
      setTvChartIntent, clearTvChartIntent, setScalpWorkbenchIntent, clearScalpWorkbenchIntent,
    }}>
      {children}
    </StoreContext.Provider>
  );
}

export function useStore() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error('useStore must be used within StoreProvider');
  return ctx;
}
