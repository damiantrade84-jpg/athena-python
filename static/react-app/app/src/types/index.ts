export interface Signal {
  id: string;
  pair: string;
  symbol?: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  sl: number;
  tp: number;
  tp2?: number;
  tp3?: number;
  /** Backend field name (0–1 scale). Prefer this when mapping from EngineASignal. */
  conviction?: number;
  /** Legacy frontend field name (0–100 scale). Kept for backward compat. */
  confidence?: number;
  engine: string;
  source?: string;
  model?: string;
  tf?: string;
  timeframe: string;
  /** Backend field name. Prefer this when mapping from EngineASignal. */
  rr?: number;
  /** Legacy frontend field name. Kept for backward compat. */
  rRatio?: number;
  votes?: number;
  timestamp: string;
  livePrice?: number;
  pnl?: number;
  status: 'active' | 'closed' | 'pending';
  factors?: string[];
  notes?: string;
  style?: string;
}

export interface Position {
  id: string;
  pair: string;
  symbol?: string;
  direction: 'LONG' | 'SHORT';
  entry: number;
  size: number;
  volume?: number;
  sl: number;
  tp: number;
  pnl: number;
  pnlPercent?: number;
  openTime: string;
  open_time?: string;
  broker: 'MT5' | 'Bybit';
  type: 'market' | 'limit' | 'stop';
  status: 'open' | 'closed';
  ticket?: string;
  swap?: number;
  closeTime?: string;
  close_price?: number;
  profit?: number;
  duration?: string;
}

export interface MarketData {
  pair: string;
  price: number;
  change24h: number;
  high24h: number;
  low24h: number;
  volume24h: number;
  spread: number;
  bid?: number;
  ask?: number;
}

export interface BacktestResult {
  id: string;
  name: string;
  pair: string;
  period: string;
  totalReturn: number;
  maxDrawdown: number;
  sharpe: number;
  winRate: number;
  profitFactor: number;
  trades: number;
  avgTrade: number;
  sqn: number;
  sortino?: number;
  equityCurve: { date: string; equity: number }[];
  dailyPnl?: { date: string; pnl: number }[];
}

export interface GuardianStatus {
  overall: 'healthy' | 'warning' | 'critical' | 'unknown';
  bootChecks: {
    name: string;
    status: 'pass' | 'fail' | 'pending';
    message: string;
  }[];
  circuitBreaker: boolean;
  dailyLoss: number;
  dailyLossLimit: number;
  openRisk: number;
  maxOpenRisk: number;
  divergence: boolean;
}

export interface ScreenerResult {
  pair: string;
  trend: 'bullish' | 'bearish' | 'neutral';
  strength: number;
  rsi: number;
  macd: number;
  volume: number;
  atr: number;
  sentiment: 'risk_on' | 'risk_off' | 'neutral';
}

/** Row returned by Flask POST /api/screener-scan (EODHD momentum discovery). */
export interface ScreenerCandidate {
  symbol: string;
  name: string;
  exchange: string;
  marketCap: number | null;
  '52wHigh': number | null;
  '52wLow': number | null;
  price: number | null;
  '200dNewHi': number | null;
}

export interface ScreenerScanResponse {
  success: boolean;
  newCandidates: ScreenerCandidate[];
  alreadyTracked: ScreenerCandidate[];
  totalScanned: number;
  scannedAt: string;
}

/**
 * Real shape returned by Flask /api/performance.
 * - win_rate, max_drawdown_pct already in PERCENT units (0..100).
 * - equity_curve is a flat number[] (cumulative R); chart wrapper converts to {idx, equity}.
 * - profit_factor / sharpe / sortino can be null (no decisive trades).
 * - performance_by_engine is the actual key (legacy by_engine kept optional for back-compat).
 */
export interface PerformanceEngineRow {
  trades?: number;
  breakevens?: number;
  win_rate_pct?: number;
  avg_r?: number | null;
  total_r?: number;
  sqn?: number | null;
  avg_slippage_bps?: number | null;
}

export interface AuditClosedTrade {
  pair?: string;
  direction?: string;
  entry_price?: number;
  exit_price?: number;
  pnl?: number;
  r_multiple?: number;
  exit_reason?: string;
  engine?: string;
  engine_resolved?: string;
}

export interface PerformanceMetrics {
  total_trades: number;
  win_count?: number;
  loss_count?: number;
  breakeven_count?: number;
  win_rate?: number;            // 0..100 already
  average_r_multiple?: number;
  total_r?: number;
  profit_factor?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  sqn?: number | null;
  max_drawdown_pct?: number;    // 0..100 already
  max_drawdown?: number;        // legacy fallback
  /** Backend returns win-rate percentage as a scalar per bucket; legacy rows may nest `win_rate`. */
  win_rate_by_regime?: Record<string, number | { win_rate?: number; trades?: number }>;
  win_rate_by_score_band?: Record<string, number | { win_rate?: number; trades?: number }>;
  win_rate_by_asset_type?: Record<string, number | { win_rate?: number; trades?: number }>;
  best_pair?: string | null;
  worst_pair?: string | null;
  average_holding_period_hours?: number | null;
  equity_curve?: number[] | { date: string; equity: number }[];
  /** Aggregated realised P&amp;L per close date (from audit_log rows with parseable exit timestamps). */
  daily_pnl?: { date: string; pnl: number }[];
  /** Human-readable notes on how headline metrics are computed (e.g. Sharpe annualiser). */
  metric_interpretation_notes?: string[];
  last_20_trades?: AuditClosedTrade[];
  execution_quality?: {
    trades_with_slippage?: number;
    median_abs_slippage_bps?: number | null;
    mean_abs_slippage_bps?: number | null;
  };
  attribution_by_source?: Record<string, { trades?: number; win_rate_pct?: number; total_r?: number }>;
  performance_by_engine?: Record<string, PerformanceEngineRow>;
  by_engine?: Record<string, PerformanceEngineRow & { win_rate?: number; profit_factor?: number; pnl?: number }>;
  by_asset_class?: Record<string, { trades?: number; win_rate?: number; profit_factor?: number; pnl?: number }>;
  total_pnl?: number;
  avg_win?: number;
  avg_loss?: number;
  message?: string;
}

export type PanelId =
  | 'dashboard'
  | 'signals'
  | 'pairBrowser'
  | 'engineBHotBench'
  | 'liveCockpit'
  | 'scanConfig'
  | 'trades'
  | 'engineC'
  | 'scalpLab'
  | 'scalpWorkbench'
  | 'tvChart'
  | 'backtest'
  | 'screener'
  | 'lotteryLab'
  | 'researchLab'
  | 'performance'
  | 'markets'
  | 'guardian'
  | 'aiPerformance'
  | 'exitStrategy'
  | 'cascadeScan'
  | 'bulkAiReview'
  | 'suggestedTrades'
  | 'ase'
  | 'tsmom'
  | 'forexFactor'
  | 'edgeLab';

/**
 * Derived data-fidelity diagnostic for a Cascade Scan candidate.
 * Always an OBJECT (never a string) — render score%, pairSource, and freshness.
 */
export interface CascadeSourceFidelity {
  score: number;
  pairSource: string;
  dataFreshness: string;
  derived: boolean;
}

export type CascadeTriageVerdict = 'FULL_CHART_REVIEW' | 'WATCHLIST_ONLY' | 'SKIP';
export type CascadeActionState =
  | 'ENGINE_A_ONLY'
  | 'WATCHLIST_ONLY'
  | 'READY_FOR_CHART_REVIEW'
  | 'BLOCKED';

/** Single candidate from POST /api/cascade-scan (mirrors docs/cascade_scan_contract.md). */
export interface CascadeCandidate {
  symbol: string;
  analysisTimeframes: string[];
  displayTimeframe: string;
  reviewTimeframe: string;
  direction: string;
  confluenceScore: number;
  engineAVerdict: string;
  shortlistRank: number;
  shortlistPassed: boolean;
  shortlistReasons: string[];
  shortlistBlockers: string[];
  rr: number | null;
  regime: string | null;
  session: string | null;
  /** Optional ranking diagnostics (present in the contract, neutral for forex). */
  sessionQualityScore?: number;
  sessionQualityReason?: string;
  freshnessDiagnostics: unknown;
  sourceFidelity: CascadeSourceFidelity;
  coherenceScore: number;
  coherenceReason: string;
  triageVerdict: CascadeTriageVerdict | null;
  triageReason: string | null;
  triageConfidence: number | null;
  triagePriority?: number | null;
  readyForChartReview: boolean;
  reviewAction: 'OPEN_CHART_REVIEW';
  chartAiStatus: 'NOT_RUN';
  actionState: CascadeActionState;
}

/** Persisted cascade scan session metadata (survives panel navigation). */
export interface CascadeScanMeta {
  scannedAt: string;
  assetClass: string;
  enableTriage: boolean;
  topN: number;
  lastError?: string | null;
}

/** POST /api/cascade-scan response (run + busy shapes). */
export interface CascadeHardBlockedSummary {
  symbol: string;
  blockers: string[];
}

/** POST /api/cascade-scan response (run + busy shapes). */
export interface CascadeScanResponse {
  success?: boolean;
  busy?: boolean;
  error?: string;
  scanRunId: string;
  assetClass: string;
  universeCount: number;
  engineACandidateCount: number;
  shortlistCount: number;
  hardBlockedCount?: number;
  hardBlockedSummary?: CascadeHardBlockedSummary[];
  triageEnabled: boolean;
  candidates: CascadeCandidate[];
}

export interface LotteryNumber {
  numbers: number[];
  date: string;
  game: string;
}

export interface LotteryAnalysis {
  hotNumbers: { number: number; frequency: number }[];
  coldNumbers: { number: number; frequency: number }[];
  overdueNumbers: { number: number; lastSeen: number }[];
  sumStats: { avg: number; min: number; max: number };
  oddEvenRatio: { odd: number; even: number };
}

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  timestamp: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  impact: 'high' | 'medium' | 'low';
  currency: string;
}

export interface SessionHours {
  session: string;
  start: string;
  end: string;
  active: boolean;
  overlap?: string;
}

export interface HealthStatus {
  status: string;
  paper_mode?: boolean;
  real_orders_allowed?: boolean;
  uptime_seconds?: number;
  scan_count?: number;
  activePairs?: number;
  pairs?: number;
  aiKey?: boolean;
  xaiKey?: boolean;
  killSwitch?: boolean;
  dataSource?: string;
  dataSources?: string[];
}

export interface MT5Account {
  balance?: number;
  equity?: number;
  margin?: number;
  freeMargin?: number;
  profit?: number;
  currency?: string;
  login?: number;
  server?: string;
  symbol?: string;
}

export interface MT5Status {
  connected: boolean;
  executionEnabled?: boolean;
  openPositions?: number;
  account?: MT5Account;
  // Legacy/flat fields kept optional for back-compat
  account_balance?: number;
  account_equity?: number;
  margin_level?: number;
  server?: string;
}

export interface BybitAccount {
  balance?: number;
  equity?: number;
  freeBalance?: number;
  currency?: string;
  testnet?: boolean;
  exchange?: string;
  symbol?: string;
}

export interface BybitStatus {
  connected: boolean;
  openPositions?: number;
  account?: BybitAccount;
  // Legacy/flat field kept optional for back-compat
  balance_usdt?: number;
}

export interface GuardianApiStatus {
  overall: 'healthy' | 'warning' | 'critical';
  guardian: { passed: boolean };
  shield: { circuit_breaker_open: boolean };
  divergence: { divergence_count: number };
  forensics: Record<string, unknown>;
}

export interface OpenTrade {
  ticket: string;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  volume: number;
  open_price: number;
  sl: number;
  tp: number;
  profit: number;
  open_time: string;
  duration: string;
  swap?: number;
}

export interface LotteryDraw {
  date: string;
  numbers: number[];
  powerball?: number;
}

export interface FactorAttribution {
  factor: string;
  ic_score: number;
  win_rate: number;
  trades: number;
}

export type {
  SuggestedTradePlan,
  SuggestedTradeWatch,
  TvChartIntent,
  ScalpWorkbenchIntent,
} from './athena';
