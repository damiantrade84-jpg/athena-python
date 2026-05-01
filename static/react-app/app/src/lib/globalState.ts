// Core state compatibility layer — initializes global window.AppState
// for legacy integrations and inter-module communication.

interface PriceData {
  price: number;
  change24h: number;
  high24h: number;
  low24h: number;
  volume24h: number;
  spread: number;
}

interface SignalData {
  id: string;
  pair: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  sl: number;
  tp: number;
  tp2?: number;
  tp3?: number;
  confidence?: number;
  engine: string;
  timeframe: string;
  rRatio?: number;
  votes?: number;
  timestamp: string;
  status: 'active' | 'closed' | 'pending';
  factors?: string[];
  notes?: string;
  livePrice?: number;
  pnl?: number;
}

interface ECResultData {
  consensusSignals: SignalData[];
  blendedWeights: { engineA: number; engineB: number; gating: number };
  lastUpdated: string;
}

declare global {
  interface Window {
    AppState: {
      latestPrices: Record<string, PriceData>;
      allSignals: SignalData[];
      ecResults: ECResultData | null;
      positions?: Array<{
        id: string;
        pair: string;
        direction: 'LONG' | 'SHORT';
        entry: number;
        size: number;
        sl: number;
        tp: number;
        pnl: number;
        pnlPercent: number;
        openTime: string;
        broker: 'MT5' | 'Bybit';
        type: 'market' | 'limit' | 'stop';
        status: 'open' | 'closed';
      }>;
      guardian?: {
        overall: 'healthy' | 'warning' | 'critical' | 'unknown';
        circuitBreaker: boolean;
        dailyLoss: number;
        dailyLossLimit: number;
        openRisk: number;
        maxOpenRisk: number;
      };
      autoTradeEnabled?: boolean;
      testModeEnabled?: boolean;
      activePanel?: string;
    };
    // Pair Browser legacy globals
    _pairBrowser?: Record<string, unknown>;
    runPairBrowserEngineA?: () => Promise<unknown>;
    runPairBrowserEngineB?: () => Promise<void>;
    runPairBrowserCompare?: () => Promise<void>;
    runPairBrowserNews?: () => Promise<void>;
    runPairBrowserVision?: () => Promise<void>;
    loadPairBrowserPairs?: (force?: boolean) => Promise<void>;
    refreshPairBrowserPairs?: () => Promise<void>;
    loadPairBrowserIntermarket?: () => Promise<void>;
    selectPairBrowserPair?: (symbol: string) => void;
    pairBrowserUpdateAssetFilter?: (value: string) => void;
    pairBrowserUpdateStyle?: (value: string) => void;
    pairBrowserUpdateDirection?: (value: string) => void;
    pairBrowserUpdateQuery?: (value: string) => void;
    togglePairBrowserSection?: (name: string) => void;
    pairBrowserSetManualDir?: (val: string) => void;
    pairBrowserSetManualStyle?: (val: string) => void;
    pairBrowserManualExecute?: (btnId: string) => Promise<void>;
    openPairBrowserChart?: (tf?: string) => void;
    openPairBrowserTV?: () => void;
    // Signal helpers
    getSignalLivePrice?: (sig: unknown) => number | null;
    buildLiveSignalPayload?: (sig: unknown) => Record<string, unknown>;
    escSigHtml?: (value: unknown) => string;
    fmtPriceForSig?: (value: number | string, ctx?: unknown) => string;
    formatConfluenceText?: (sig: unknown) => string;
    showToast?: (message: string, isError?: boolean) => void;
    // Execution feature
    ExecutionFeature?: {
      postQuickExecute: (payload: unknown, btn: HTMLElement | null, successMsg?: string) => Promise<unknown>;
      executeSignalStyle: (signalRef: string, pipMode?: string, buttonId?: string) => Promise<void>;
      quickExecute: (signalRef: string, engineBData?: unknown, buttonId?: string, pipMode?: string) => Promise<void>;
      executeEngineC: (sid: string, pipMode?: string) => Promise<void>;
      getExecutionSizingOverride: () => number;
    };
    // Execution state
    _ecResults?: Record<string, unknown[]>;
    [key: string]: unknown;
  }
}

export function initGlobalState() {
  if (!window.AppState) {
    window.AppState = {
      latestPrices: {},
      allSignals: [],
      ecResults: null,
    };
  }
  return window.AppState;
}

export function syncPricesToGlobal(prices: Record<string, PriceData>) {
  window.AppState.latestPrices = { ...window.AppState.latestPrices, ...prices };
}

export function syncSignalsToGlobal(signals: SignalData[]) {
  window.AppState.allSignals = signals;
}

export function syncECResultsToGlobal(results: ECResultData) {
  window.AppState.ecResults = results;
}

export function getGlobalState() {
  return window.AppState;
}
