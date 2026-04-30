import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import type { PanelId, Signal, Position, GuardianStatus, NewsItem, SessionHours } from '@/types';
import { syncPricesToGlobal, syncSignalsToGlobal } from '@/lib/globalState';

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
  getLivePrice: (pair: string) => number;
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
    dailyLossLimit: 500,
    openRisk: 0,
    maxOpenRisk: 2000,
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

  const toastTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info') => {
    if (toastTimeout.current !== null) clearTimeout(toastTimeout.current);
    setToast({ message, type });
    toastTimeout.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const refreshSignals = useCallback(() => {
    showToast('Signals refreshed', 'success');
  }, [showToast]);

  const refreshPositions = useCallback(() => {
    showToast('Positions refreshed', 'success');
  }, [showToast]);

  const refreshGuardian = useCallback(() => {
    showToast('Guardian refreshed', 'success');
  }, [showToast]);

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

  const livePriceGetter = useCallback((pair: string) => {
    // Will be populated by real price data via API
    return 0;
  }, []);

  return (
    <StoreContext.Provider value={{
      activePanel, signals, positions, guardian, news, sessions,
      isAutoTrade, isTestMode, isLoading, toast,
      setActivePanel, refreshSignals, refreshPositions,
      refreshGuardian, toggleAutoTrade, toggleTestMode, executeSignal,
      closePosition, showToast, getLivePrice: livePriceGetter,
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
