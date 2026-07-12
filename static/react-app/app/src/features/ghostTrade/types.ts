export type GhostMode = 'SHADOW' | 'DEMO_MANUAL' | 'DEMO_AUTO';
export type GhostExecutionStatus = 'DEMO VERIFIED' | 'DEMO NOT VERIFIED' | 'SHADOW ONLY';
export type GhostAssetGroup =
  | 'forex' | 'crypto' | 'metals' | 'energy' | 'commodities_other'
  | 'indices' | 'equities' | 'other';
export type GhostDirection = 'LONG' | 'SHORT' | 'NEUTRAL';

export interface GhostHealth {
  enabled: boolean;
  mode: GhostMode;
  executionStatus: GhostExecutionStatus;
  liveTradingAllowed: false;
  demoAutoEnabled: boolean;
  scanRunning: boolean;
  mt5Status?: GhostExecutionStatus;
  bybitStatus?: GhostExecutionStatus;
}

export interface GhostInstrument {
  source: 'MT5' | 'BYBIT';
  brokerSymbol: string;
  canonicalSymbol: string;
  assetGroup: GhostAssetGroup;
  assetSubgroup: string;
  baseAsset: string;
  quoteAsset: string;
  metadata: Record<string, unknown>;
  tradeEnabled: boolean;
  eligibleForScoring: boolean;
  skipReasons: string[];
}

export interface GhostSignal {
  signalId: string;
  signalVersion: string;
  scanId: string;
  instrument: GhostInstrument;
  style: 'intraday' | 'swing';
  direction: GhostDirection;
  decisionTime: string;
  confirmedScore: number;
  liveAdjustment: number;
  displayScore: number;
  directionConfidence: number;
  entryQuality: number;
  entry: number | null;
  stop: number | null;
  target: number | null;
  structuralRR: number | null;
  volatilityRegime: string;
  canExecute: boolean;
  status: string;
  reasons: string[];
  components: Record<string, Record<string, unknown>>;
  confirmedTimes: Record<string, string>;
  dataFreshness: string;
  spread: number | null;
  groupRank?: number | null;
  groupCount?: number | null;
  globalRank?: number | null;
  globalCount?: number | null;
}

export interface GhostGroup {
  assetGroup: GhostAssetGroup;
  instrumentsDiscovered: number;
  instrumentsScored: number;
  signals: number;
  bullish: number;
  bearish: number;
  neutral: number;
  executableDemo: number;
  averageScore: number | null;
  highestScore: number | null;
}

export interface GhostPosition {
  positionId: string;
  signalId: string;
  source: 'MT5' | 'BYBIT';
  canonicalSymbol: string;
  assetGroup: GhostAssetGroup;
  mode: 'SHADOW' | 'DEMO';
  status: string;
  direction: GhostDirection;
  entry: number;
  stop: number;
  target: number;
  initialRisk: number;
  quantity: number | null;
  openedAt: string | null;
  closedAt: string | null;
  volatilityRegime: string;
  entryQuality: number;
  signalScore: number;
}

export interface GhostPerformance {
  totalClosedTrades: number;
  meanR: number | null;
  medianR: number | null;
  winRate: number | null;
  profitFactor: number | null;
  totalR: number;
  [key: string]: unknown;
}

export interface GhostScan {
  scanId: string;
  startedAt: string;
  completedAt: string | null;
  status?: string;
  discoveredCount: number;
  scoredCount: number;
  skippedCount: number;
  errorCount?: number;
  errors: string[];
}

export interface GhostTradeState {
  health: GhostHealth | null;
  groups: GhostGroup[];
  signals: GhostSignal[];
  positions: GhostPosition[];
  performance: GhostPerformance;
  currentScan: GhostScan | null;
}
