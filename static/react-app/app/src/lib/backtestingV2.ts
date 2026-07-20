export type BacktestV2Engine = 'A' | 'B';
export type BacktestV2Mode = 'CERTIFIED' | 'EXPLORATORY';
export type BacktestV2Status =
  | 'QUEUED'
  | 'PREPARING_DATA'
  | 'RUNNING'
  | 'VALIDATING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'INTERRUPTED';

export interface ApiEnvelope<T> {
  success: boolean;
  data: T;
  code?: string;
  error?: string;
}

export interface BacktestUniverseItem {
  symbol: string;
  providerSymbol: string;
  assetType: string;
  enabled: boolean;
}

export interface BacktestCapabilities {
  enabled: boolean;
  platformVersion: string;
  simulatorVersion: string;
  engines: Record<BacktestV2Engine, { styles: string[] }>;
  certificationStates: string[];
  runStatuses: BacktestV2Status[];
  universe: BacktestUniverseItem[];
  assetTypes: string[];
  workerPolicy: {
    model: string;
    oneActiveJob: boolean;
    reservedLogicalCores: number;
    maxWorkers: number;
    memoryCeilingBytes: number;
  };
  assurance: {
    legacyRulesUsed: boolean;
    aiIncluded: boolean;
    enginesBlended: boolean;
    simulationFetchesLiveData: boolean;
  };
}

export interface PreflightInstrument {
  symbol: string;
  display: string;
  assetType: string;
  policies: Record<string, { policy: Record<string, unknown>; timeframes: string[] }>;
  providers: Record<string, { provider: string; providerSymbol: string }>;
}

export interface PreflightResult {
  certification: 'CERTIFIED' | 'PARTIAL_CERTIFIED' | 'EXPLORATORY';
  instruments: PreflightInstrument[];
  exclusions: Array<{ symbol: string; engine?: string; code: string; message: string }>;
  estimatedSeries: number;
  estimatedTasks: number;
  estimatedBars: number;
}

export interface BacktestMetrics {
  tradeCount: number;
  winRate: number;
  meanR: number;
  medianR: number;
  totalR: number;
  profitFactor: number;
  sqn: number;
  sharpe: number;
  maxDrawdownR: number;
  totalCostR: number;
}

export interface BacktestTaskResult {
  success: boolean;
  task: { task_id?: string; engine: BacktestV2Engine; symbol: string; style: string };
  metrics: BacktestMetrics;
  validation: {
    metrics: Record<string, BacktestMetrics>;
    confidenceIntervals: { meanR: { lower: number; estimate: number; upper: number } };
    psr: number | null;
    dsr: number | null;
    trialCount: number;
    pbo: number | null;
  };
  funnel: {
    decisionCount: number;
    qualifiedCount?: number;
    passedCount?: number;
    uniqueIntentCount: number;
    rejectionFunnel: Record<string, number>;
    lifecycleFunnel?: Record<string, number>;
    executionTimeframe: string;
  };
  simulation: {
    signalIntentCount: number;
    tradeCount: number;
    sameBarAmbiguityCount: number;
    sameBarAmbiguityRate: number;
    sameBarPolicy: string;
  };
  timing: { computeSec: number; dataAcquisitionSec: number; jitCompilationSec: number };
  peakMemoryBytes: number;
}

export interface BacktestResult {
  runId: string;
  status: BacktestV2Status;
  certification: string;
  datasetId: string;
  configSnapshotId: string;
  engines: Record<string, {
    metrics: BacktestMetrics;
    taskCount: number;
    tradeCount: number;
    equityCurve: Array<{ idx: number; time: string; equity: number }>;
    underwaterCurve: Array<{ idx: number; time: string; equity: number; drawdown: number }>;
    analysis: {
      direction: Record<string, BacktestMetrics>;
      symbol: Record<string, BacktestMetrics>;
      regime: Record<string, BacktestMetrics>;
    };
  }>;
  tasks: BacktestTaskResult[];
  exclusions: Array<Record<string, unknown>>;
  failures: Array<Record<string, unknown>>;
  portfolio?: {
    enabled: boolean;
    ledgers: Record<string, { initialCapital: number; finalCapital: number; returnPct: number; maxDrawdownAmount: number; equityCurve: Array<{ time: string | null; equity: number }> }>;
  } | null;
  costModel: Record<string, unknown>;
  validationPlan: Record<string, unknown>;
  timing: { dataAcquisitionSec: number; computeSec: number; jitCompilationSec: number };
  peakWorkerMemoryBytes: number;
  assurance: Record<string, unknown>;
  reused?: boolean;
  reusedFromRunId?: string;
}

export interface BacktestRun {
  run_id: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  status: BacktestV2Status;
  phase: string;
  progress_completed: number;
  progress_total: number;
  dataset_id?: string | null;
  config_snapshot_id?: string | null;
  certification: string;
  request: {
    engines: BacktestV2Engine[];
    styles: Record<BacktestV2Engine, string>;
    symbols: string[];
    start_date: string;
    end_date: string;
    mode: BacktestV2Mode;
    comparison_family?: string | null;
    variant_id?: string | null;
    cost_model?: Record<string, unknown>;
    validation?: Record<string, unknown>;
    portfolio?: Record<string, unknown>;
    random_seed?: number;
  };
  result?: BacktestResult | null;
  error?: { code?: string; error?: string } | null;
  cancel_requested: number;
  recovered_count: number;
  tasks?: Array<Record<string, unknown>>;
}

export interface TradeRow {
  tradeId: number;
  engine: BacktestV2Engine;
  symbol: string;
  direction: string;
  style: string;
  signal_time: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  stop_price: number;
  targets: number[];
  gross_r: number;
  cost_r: number;
  net_r: number;
  exit_reason: string;
  ambiguous_same_bar: boolean;
  duration_bars: number;
  split: string;
}

export interface TradePage {
  items: TradeRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface DatasetManifest {
  datasetId: string;
  createdAt: string;
  status: string;
  certification: string;
  requestedMode: string;
  startDate: string;
  endDate: string;
  symbols: string[];
  engines: BacktestV2Engine[];
  manifestHash: string;
  series: Array<{
    series_id: string;
    symbol: string;
    provider: string;
    provider_symbol: string;
    timeframe: string;
    row_count: number;
    first_timestamp?: string | null;
    last_timestamp?: string | null;
    gap_count: number;
    provider_no_quote_count?: number;
    duplicate_count: number;
    quality_state: string;
    limitations: string[];
    observations?: string[];
  }>;
  exclusions: Array<Record<string, unknown>>;
}

export interface ComparisonResult {
  comparisonId: string;
  compatible: boolean;
  incompatibilityReasons: string[];
  trialCount?: number;
  pbo?: { value: number; folds: number; selectedVariantRunId: string; method: string } | null;
  runs?: Array<{
    runId: string;
    variantId?: string | null;
    metrics: BacktestMetrics;
    deltasFromBaseline: Record<string, number>;
    confidenceIntervals: { meanR: { lower: number; estimate: number; upper: number } };
    dsr: number | null;
  }>;
}

export function hasExplicitUniverse(symbols: Iterable<string>): boolean {
  for (const symbol of symbols) {
    if (symbol.trim().length > 0) return true;
  }
  return false;
}

export function statusTone(status: string): string {
  if (status === 'COMPLETED') return 'border-emerald-400/35 bg-emerald-400/10 text-emerald-300';
  if (status === 'FAILED' || status === 'CANCELLED') return 'border-rose-400/35 bg-rose-400/10 text-rose-300';
  if (status === 'VALIDATING') return 'border-sky-400/35 bg-sky-400/10 text-sky-300';
  return 'border-violet-400/35 bg-violet-400/10 text-violet-200';
}

export function assuranceTone(value: string): string {
  if (value === 'CERTIFIED') return 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300';
  if (value === 'PARTIAL_CERTIFIED') return 'border-amber-400/40 bg-amber-400/10 text-amber-300';
  return 'border-fuchsia-400/40 bg-fuchsia-400/10 text-fuchsia-300';
}
