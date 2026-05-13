// Athena backend payload shapes — modelled directly off the Flask responses
// in `athena.py`. These are deliberately permissive (`unknown`/optional) because
// `_json_safe()` scrubs NaN→null and many fields are absent depending on engine
// path, regime, and pair type.

export type Direction = 'LONG' | 'SHORT';

/** Engine A v2 signal — emitted by `analyze_pair()` (camelCase keys). */
export interface EngineASignal {
  id?: string;
  timestamp?: string;
  pair?: string;
  display?: string;
  symbol?: string;
  type?: string; // forex | crypto | commodity | index | stock | etf
  scoreGroup?: string;
  direction?: Direction;
  price?: number;

  // Scoring (Engine A v2: 0–3.0 scale, all asset classes)
  score?: number;
  maxScore?: number;
  threshold?: number;
  confluenceScore?: number;
  confluencePct?: number;
  conviction?: number; // 0–1
  signalClass?: string; // CRITERIA / WATCHLIST / SKIP / etc.
  grade?: string;

  // Factor diagnostics (factor_scoring.py)
  factorScores?: {
    trend?: number;
    momentum?: number;
    addon?: number;
    research_lab?: number;
    [k: string]: number | undefined;
  };
  factorWeights?: Record<string, number>;
  factorDiagnostics?: {
    trendDirection?: Direction | string;
    trendCoherence?: number;
    momentumQuality?: number;
    adxValue?: number;
    adxGate?: string;
    sessionMultiplier?: number;
    [k: string]: unknown;
  };
  /**
   * Raw output of confidence_engine.compute_confidence (kept snake_case in payload).
   * Top-level: confidence (0..1), components { indicator_agreement, timeframe_alignment, regime_fit, liquidity_quality },
   * weights_used { ... }, available_count, degraded, session_quality.
   */
  confidenceDetail?: {
    confidence?: number;
    components?: {
      indicator_agreement?: number | null;
      timeframe_alignment?: number | null;
      regime_fit?: number | null;
      liquidity_quality?: number | null;
    };
    weights_used?: Record<string, number>;
    available_count?: number;
    degraded?: boolean;
    session_quality?: string;
    [k: string]: unknown;
  };

  // Levels / risk
  entry?: number;
  sl?: number;
  tp?: number;
  tp1?: number;
  tp2?: number;
  tp3?: number;
  rr?: number;
  rr1?: number;
  rr2?: number;
  slPct?: number;
  atr?: number;
  scalp_sl?: number;
  scalp_tp?: number;
  intraday_sl?: number;
  intraday_tp?: number;
  swing_sl?: number;
  swing_tp?: number;

  // Regime & context
  regime?: { label?: string; smoothed?: string } | string;
  session?: { name?: string; quality?: string } | string;
  warnings?: string[];
  votes?: { bull?: number; bear?: number; tally?: Record<string, unknown> };

  // Engine B attached when present (analyze_pair stores under "engine_b")
  engine_b?: EngineBNakedResult | null;
  naked_data?: EngineBNakedResult | null; // Engine B scan signals expose this

  // News / intermarket / vision context
  newsContext?: unknown;
  intermarketConfirmation?: unknown;
  chartVision?: unknown;
  aiAnalysis?: unknown;

  // Misc passthroughs for the live cockpit
  isForming?: boolean;
  is_naked?: boolean;
  style?: string;
  requestedStyle?: string;
  edgeProbability?: number;
  riskLevel?: string;
  trendState?: string;
  paperMode?: boolean;
  [k: string]: unknown;
}

/** Engine B (NakedEngine) result — `_compute_naked_analysis()` payload. */
export interface EngineBNakedResult {
  symbol?: string;
  pair?: string;
  display?: string;
  direction?: Direction;
  type?: string;
  style?: string;

  // Structural verdict from `analyze_structure()`
  structural_verdict?: string; // CLEAR / NO_STRUCTURE / etc.
  structural_data_valid?: boolean;
  current_swing_sequence?: string; // HH_HL / LH_LL / MIXED
  macro_swing_sequence?: string;
  zone_type?: string; // FVG / OB / BREAKER / SR
  zone_quality?: string;

  // Confidence / checklist
  confidence?: {
    passed?: boolean;
    score?: number;
    max_score?: number;
    checklist?: EngineBChecklist;
    hard_fail_reasons?: string[];
    soft_warnings?: string[];
    diagnostic_notes?: string[];
    research_lab_detail?: unknown;
    [k: string]: unknown;
  };
  checklist?: EngineBChecklist;
  passed?: boolean;
  confidence_score?: number;
  confidence_max?: number;
  score?: number;
  max_score?: number;
  min_score?: number;
  min_rr?: number;

  // Levels
  entry?: number;
  sl?: number;
  tp?: number;
  rr?: number;

  // Detail
  active_fvgs?: Array<{
    top: number;
    bottom: number;
    size: number;
    mitigated: boolean;
    strength: number;
    direction?: string;
    [k: string]: unknown;
  }>;
  zones?: Array<{ type: string; price: number; [k: string]: unknown }>;
  swing_points?: Array<{ type: string; price: number; time?: string }>;

  // AI review (advisory only — not a gate)
  ai_review?: unknown;
  ai_analysis?: unknown;

  // Diagnostic flags
  hard_fail_reasons?: string[];
  soft_warnings?: string[];
  diagnostic_notes?: string[];
  no_trigger_classification?: string;
  d1_conflict?: boolean | string;
  [k: string]: unknown;
}

export interface EngineBChecklist {
  structure_ok?: boolean;
  location_ok?: boolean;
  entry_ok?: boolean;
  trigger_ok?: boolean;
  room_ok?: boolean;
  room_rr_ok?: boolean;
  rr_ok?: boolean;
  macro_ok?: boolean;
  d1_conflict?: boolean | string;
  [k: string]: unknown;
}

/** /api/scan response. */
export interface ScanResponse {
  success?: boolean;
  signals?: EngineASignal[];
  scanned?: number;
  scannedAt?: string;
  scan_time?: number;
  pairs_scanned?: number;
  asset_class?: string;
  style?: string;
  available?: boolean;
  reason?: string;
  payloadVersion?: string;
  [k: string]: unknown;
}

/** /api/scan-naked response. */
export interface NakedScanResponse {
  success?: boolean;
  signals?: EngineASignal[]; // each carries naked_data
  debugRows?: unknown[];
  scanFunnel?: Record<string, unknown>;
  totalPairs?: number;
  activePairs?: number;
  [k: string]: unknown;
}

/** /api/pair-scan response. */
export interface PairScanResponse {
  pair?: { symbol?: string; display?: string; type?: string; enabled?: boolean; source?: string };
  signal?: EngineASignal | null;
  style?: string;
  intermarketIncluded?: boolean;
  error?: string;
}

/** /api/compare-engines response. */
export interface CompareResponse {
  engineA?: EngineASignal | null;
  engineB?: EngineBNakedResult | null;
  summary?: {
    sameDirection?: boolean;
    structureAligned?: boolean;
    macroAligned?: boolean;
    aiReviewIncluded?: boolean;
    engineAStyle?: string;
    engineBStyle?: string;
    engineBMinScore?: number;
    verdict?: 'ALIGNED' | 'CONFLICT' | string;
  };
  aiCalibrationContextA?: unknown;
  aiCalibrationContextB?: unknown;
  error?: string;
}

/** /api/pairs response. */
export interface PairListEntry {
  symbol: string;
  display: string;
  type: string;
  enabled?: boolean;
  source?: string;
  [k: string]: unknown;
}
export interface PairsResponse {
  pairs?: PairListEntry[];
  // Current /api/pairs format (grouped by asset class)
  groups?: Record<string, Array<{ sym: string; label: string; enabled?: boolean }>>;
  total?: number;
  active?: number;
  // Legacy bucketed shape some endpoints emit:
  forex?: string[];
  crypto?: string[];
  stocks?: string[];
  indices?: string[];
  commodities?: string[];
  etf?: string[];
  jse?: string[];
}

/** /api/news-sentiment response (per-pair). */
export interface NewsSentimentResponse {
  symbol?: string;
  sentiment?: string;
  score?: number;
  headlines?: Array<{ title?: string; source?: string; sentiment?: string; url?: string; date?: string }>;
  error?: string;
}

/** /api/chart-analysis structured block (from _extract_vision_structured). */
export interface ChartVisionStructured {
  rating?: 'STRONG' | 'MODERATE' | 'WEAK' | 'AVOID' | 'CONTRADICTS' | string;
  confirms_direction?: boolean;
  sl_flag?: 'ok' | 'too_tight' | string;
  tp_flag?: 'ok' | 'too_far' | string;
  style_ratings?: Partial<Record<'scalp' | 'intraday' | 'swing', string>>;
  level_suggestions?: Record<string, number | string>;
  right_edge_status?: 'CONFIRMS' | 'REVIEW' | 'POTENTIAL_REVERSAL' | string;
  final_verdict?: 'HOLD' | 'CLOSE' | 'ADJUST' | string;
  ema_reclaim_flag?: boolean | null;
  countertrend_volume_flag?: boolean | null;
  reviewSource?: string;
  selectedStyleGrade?: string;
  executionRisk?: string;
  structuralRisk?: string;
}

/** /api/chart-analysis response. */
export interface ChartAnalysisResponse {
  analysis?: string;
  body?: string;
  structured?: ChartVisionStructured;
  model?: string;
  symbol?: string;
  tf?: string;
  dual_tf?: boolean;
  triple_tf?: boolean;
  chart_image?: string;           // base64 PNG of the H4 chart the AI analyzed
  chart_image_h1?: string;        // base64 PNG of H1 chart (triple mode)
  chart_image_d1?: string;        // base64 PNG of D1 chart (triple mode)
  chart_timestamp_warnings?: string[];
  latest_candle_ts?: string | number;
  error?: string;
  // Convenience fields surfaced by some callers/legacy code
  right_edge?: string;
  tf_alignment?: string;
  scalp_rating?: string;
  intraday_rating?: string;
  swing_rating?: string;
  [k: string]: unknown;
}

/**
 * /api/analyze (Marcus Reid text analysis) response.
 * Note: edgeProbability is 0-100 scale (percentage).
 */
export interface AiTextReviewResponse {
  reviewSource?: string;
  resolvedStyle?: string;
  scannerReadiness?: string;
  factorQuality?: number;
  structuralRisk?: string;
  executionRisk?: string;
  selectedStyleGrade?: string;
  grade?: string;
  verdict?: string;
  narrative?: string;
  entryZone?: string;
  invalidation?: string;
  keyLevels?: string;
  positionSizing?: string;
  tradeStyle?: string;
  tradeStyleReason?: string;
  warnings?: string[];
  edgeProbability?: number;
  riskLevel?: string;
  style_ratings?: {
    scalp?: { grade?: string; edgeProbability?: number; riskLevel?: string };
    intraday?: { grade?: string; edgeProbability?: number; riskLevel?: string };
    swing?: { grade?: string; edgeProbability?: number; riskLevel?: string };
  };
  error?: string;
  [k: string]: unknown;
}

// ───────────────────────── Live Dashboard (Cyber Cockpit) ───────────────────

export interface LdEngineARow {
  score: number | null;
  maxScore: number | null;
  threshold: number | null;
  direction: Direction | null;
  passed: boolean;
  factorScores: { trend: number | null; momentum: number | null; addon: number | null };
  trendScore: number | null;
  momentumScore: number | null;
  addonScore: number | null;
  adxValue: number | null;
  adxGate: string | null;
  sessionMultiplier: number | null;
  conviction: number | null;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  tp1: number | null;
  tp2: number | null;
  rr: number | null;
  failReasons: string[];
  freshnessPolicyStatus: string | null;
}

export interface LdEngineBRow {
  score: number | null;
  maxScore: number | null;
  threshold: number | null;
  direction: Direction | null;
  structuralVerdict: string | null;
  structuralDataValid: boolean;
  confidencePassed: boolean;
  structure_ok: boolean;
  location_ok: boolean;
  entry_ok: boolean;
  room_rr_ok: boolean;
  d1_conflict: boolean | string | null;
  hardFailReasons: string[];
  softWarnings: string[];
  diagnosticNotes: string[];
  noTriggerClassification: string | null;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  rr: number | null;
}

export interface LdEngineCRow {
  decisionState: 'ALIGNED' | 'A_ONLY' | 'B_ONLY' | 'CONFLICT' | 'WATCHLIST' | 'BLOCKED' | 'NO_SETUP' | string;
  consensusType: string | null;
  conviction: number | null;
  tier: 'HIGH' | 'MEDIUM' | 'LOW' | 'SKIP' | 'WATCHLIST' | string | null;
  trade: boolean;
  reason?: string | null;
  engineAContribution: number | null;
  engineBContribution: number | null;
  engineBChecklistPassed: boolean;
  watchlistReason?: string | null;
  blockReason?: string | null;
}

export interface LdEngineDRow {
  enabled: boolean;
  gateResult: 'PASS' | 'WATCHLIST' | 'BLOCKED' | 'DATA_MISSING' | string;
  grade: 'A' | 'B' | 'C' | 'D' | string | null;
  score: number | null;
  setupType: string | null;
  spread: number | null;
  rr: number | null;
  direction: Direction | null;
  failReasons: string[];
  softWarnings: string[];
  diagnosticNotes: string[];
  missingData: string[];
  vp: { vah: number | null; poc: number | null; val: number | null };
  nearPoc: boolean | null;
  nearVah: boolean | null;
  nearVal: boolean | null;
  cvdAvailable: boolean | null;
  cvdBias: string | null;
  absorptionDetected: boolean | null;
}

export interface LdFreshness {
  consistencyStatus?: string;
  policyStatus?: string | null;
  gateDecision?: 'ALLOW' | 'BLOCK' | string;
  blockReason?: string | null;
  stalenessSeconds?: number;
  expectedConfirmedIso?: string;
  observedConfirmedIso?: string;
}

export interface LdLevels {
  entry?: number | null;
  sl?: number | null;
  tp?: number | null;
  tp1?: number | null;
  tp2?: number | null;
  rr?: number | null;
  source?: 'engineA' | 'engineB' | 'request_fallback' | string;
}

export interface LdExecutableState {
  canPaperExecute: boolean;
  canRealExecute: boolean;
  disabledReason: string | null;
  riskStatus: string;
  freshnessStatus: string;
  paperMode: boolean;
  realOrdersAllowed: boolean;
}

export interface LdAiReview {
  marcusReid?: unknown;
  engineBAI?: unknown;
  signalDebate?: unknown;
  chartVision?: unknown;
  reviewState?: string;
  confidence?: number | null;
  contradictions?: string[];
  missingInformation?: string[];
  downgradeOnly?: boolean;
  affectedExecutionPermission?: boolean;
}

export interface LdSymbolRow {
  symbol: string;
  asset_type: string | null;
  source: string | null;
  timeframe: string;
  latest_price: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  change_pct: number | null;
  chart: {
    candles?: Array<{ time?: string; open?: number; high?: number; low?: number; close?: number; volume?: number }>;
    latestConfirmedCandleIso?: string;
    latestFormingCandleIso?: string | null;
    candlePolicy?: string;
    h4GridOffsetHours?: number | null;
    levels?: Record<string, number | null>;
  } | null;
  freshness: LdFreshness;
  engineA: LdEngineARow;
  engineB: LdEngineBRow;
  engineC: LdEngineCRow;
  engineD: LdEngineDRow;
  aiReview: LdAiReview;
  paperPosition: { hasOpenPaperPosition?: boolean; entry?: number | null; sl?: number | null; tp?: number | null; pnl?: number | null };
  levels: LdLevels;
  finalState: 'PAPER CANDIDATE' | 'WATCHLIST' | 'BLOCKED' | 'NO SETUP' | string;
  mainReason: string | null;
  blockReason: string | null;
  executableState: LdExecutableState;
  error?: string;
}

export interface LdEvent {
  timestamp: string;
  symbol: string;
  severity: 'pass' | 'watch' | 'block' | string;
  message: string;
}

export interface LdSnapshot {
  payloadVersion?: string;
  contract?: Record<string, string>;
  generated_at?: string;
  paperMode?: { enabled?: boolean; realOrdersAllowed?: boolean };
  connections?: { mt5?: string; binanceWs?: string };
  truncatedSymbols?: boolean;
  freshnessAllOk?: boolean;
  symbols?: LdSymbolRow[];
  events?: LdEvent[];
  error?: string;
}

// ───────────────────────── Engine C consensus (POST /api/engine-c-scan) ─────

/**
 * Single consensus row produced by engine_c.compute_consensus()
 * (verdict + decision_state + components + sl/tp/rr + diagnosis).
 * Routed into one of {aligned, a_only, b_only, conflict, skipped} buckets.
 */
export interface EngineCConsensusRow {
  display?: string;
  symbol?: string;
  pair?: string;
  type?: string;
  scoreGroup?: string;
  style?: string | null;
  atr?: number;
  trade?: boolean;
  verdict?: 'ALIGNED' | 'A_ONLY' | 'B_ONLY' | 'B_ONLY_SCORED' | 'B_ONLY_VISION_CONFIRMED' | 'B_OVERRIDE_CONFLICT'
    | 'DIRECTION_CONFLICT' | 'OPPOSING_HIGH_CONFIDENCE' | 'REGIME_CHANGE_DETECTED' | 'NO_SIGNAL' | string;
  direction?: Direction | null;
  entry?: number | null;
  sl?: number | null;
  sl_method?: string;
  sl_a?: number | null;
  sl_b?: number | null;
  tp?: number | null;
  tp_method?: string;
  rr?: number;
  conviction?: number;
  tier?: 'HIGH' | 'MEDIUM' | 'LOW' | 'SKIP' | 'WATCHLIST' | string;
  signalTier?: string;
  watchlistReason?: string;
  decision_state?: 'execute' | 'reduced_risk' | 'watchlist' | 'blocked' | string;
  decision_state_reason?: string | null;
  sizing_override?: number;
  engine_weights?: Record<string, number>;
  engine_base_weights?: Record<string, number>;
  regime?: string;
  opposing_high_confidence?: boolean;
  components?: {
    a_norm?: number;
    a_direction?: Direction | null;
    a_has_signal?: boolean;
    a_cot_active?: boolean;
    b_norm?: number;
    b_direction?: Direction | null;
    b_has_signal?: boolean;
    b_checklist_passed?: boolean;
    b_signal_diagnostic?: string;
    b_bos?: boolean;
    b_ob_at_zone?: boolean;
    b_sequence?: string;
  };
  vision_applied?: boolean;
  vision_action?: string | null;
  vision_rating?: string | null;
  vision_sl_flag?: string | null;
  vision_tp_flag?: string | null;
  intermarket_confirmation?: Record<string, unknown>;
  intermarket_multiplier?: number;
  disagreement_diagnosis?: Record<string, unknown>;
  a_is_weak?: boolean | null;
  reason?: string;
  code?: string;
  detail?: string;
  skipCode?: string;
  skipDetail?: string;
  engine_a_raw?: Record<string, unknown>;
  engine_b_raw?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface EngineCScanResponse {
  aligned?: EngineCConsensusRow[];
  a_only?: EngineCConsensusRow[];
  b_only?: EngineCConsensusRow[];
  conflict?: EngineCConsensusRow[];
  skipped?: EngineCConsensusRow[];
  error?: string;
}

/** /api/backtest-consensus response. */
export interface EngineCBacktestResponse {
  pair?: string;
  trades?: number;
  win_rate?: number;
  profit_factor?: number | null;
  expectancy?: number | null;
  sqn?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  max_dd_pct?: number | null;
  total_pnl?: number;
  equity_curve?: number[] | { idx?: number; equity: number; date?: string }[];
  trade_history?: Array<Record<string, unknown>>;
  notes?: string;
  error?: string;
  [k: string]: unknown;
}
