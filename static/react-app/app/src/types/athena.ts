// Athena backend payload shapes — modelled directly off the Flask responses
// in `athena.py`. These are deliberately permissive (`unknown`/optional) because
// `_json_safe()` scrubs NaN→null and many fields are absent depending on engine
// path, regime, and pair type.

export type Direction = 'LONG' | 'SHORT';

export type TradeSkillDecision =
  | 'ENTRY_NOW'
  | 'WAIT_FOR_PULLBACK'
  | 'WAIT_FOR_ACCEPTANCE'
  | 'WATCH_ONLY'
  | 'NO_TRADE'
  | 'INVALIDATED';

export interface TradeSkillReview {
  tradeSkillVersion?: string;
  reviewType?: 'engine_a_chart' | 'engine_b_chart' | 'engine_d_scalp';
  decision?: TradeSkillDecision | string;
  direction?: Direction | 'NEUTRAL' | string;
  confidence?: number;
  entryAllowedNow?: boolean;
  marketState?: string;
  locationAssessment?: string;
  aggressionAssessment?: string;
  entryModel?: string;
  invalidationLevel?: number | null;
  invalidationReason?: string;
  waitReason?: string;
  noTradeReason?: string;
  requiredConfirmation?: string[];
  riskNotes?: string[];
  chartReadSummary?: string;
  tradeSkillWarnings?: string[];
  suggestedTradePlan?: SuggestedTradePlan | null;
}

/** Advisory-only structured wait plan from AI chart/scalp review. */
export interface SuggestedTradePlan {
  schemaVersion?: string;
  armable?: boolean;
  source?: 'ai_chart_review' | 'ai_scalp_chart_review' | 'engine_b_hotbench' | 'engine_b_candidate' | string;
  symbol?: string;
  direction?: Direction;
  action?: 'WAIT_FOR_LEVEL' | 'WAIT_FOR_ZONE' | 'WATCH_ONLY' | 'NO_TRADE' | 'ENTRY_NOW' | string;
  triggerType?: string;
  level?: number;
  zoneLow?: number;
  zoneHigh?: number;
  contextTf?: string;
  entryTf?: string;
  executionTf?: string;
  invalidateAbove?: number;
  invalidateBelow?: number;
  expiresInSeconds?: number;
  reason?: string;
}

export interface SuggestedTradeWatch {
  watch_id?: string;
  symbol?: string;
  display?: string;
  direction?: string;
  action?: string;
  trigger_type?: string;
  triggerType?: string;
  status?: string;
  source?: string;
  level?: number;
  zone_low?: number;
  zone_high?: number;
  zoneLow?: number;
  zoneHigh?: number;
  context_tf?: string;
  contextTf?: string;
  entry_tf?: string;
  entryTf?: string;
  execution_tf?: string;
  executionTf?: string;
  created_at?: string;
  createdAt?: string;
  expires_at?: string | null;
  expiresAt?: string | null;
  reached_at?: string | null;
  reachedAt?: string | null;
  last_evaluated_at?: string | null;
  lastCheckedAt?: string | null;
  cancelled_at?: string | null;
  notes?: string | null;
  playbook_warnings?: string[];
  signal?: unknown;
  suggested_trade_plan?: SuggestedTradePlan;
  suggestedTradePlan?: SuggestedTradePlan;
  ai_review_summary?: Record<string, unknown>;
}

export interface SuggestedTradeRunnerStatus {
  enabled?: boolean;
  alertOnly?: boolean;
  mode?: 'background_thread' | 'frontend_poll' | 'manual_only' | string;
  active?: boolean;
  lastTickAt?: string | null;
  lastTickAgeSeconds?: number | null;
  nextPollSeconds?: number | null;
  pollSeconds?: number;
  error?: string | null;
}

export interface SuggestedTradesListResponse {
  ok?: boolean;
  watches?: SuggestedTradeWatch[];
  count?: number;
  counts?: {
    active?: number;
    ready?: number;
    expired?: number;
    cancelled?: number;
    invalidated?: number;
  };
  runner?: SuggestedTradeRunnerStatus;
  alert_only?: boolean;
}

export interface TvChartIntent {
  id: string;
  source: 'signals' | 'engine_a' | 'engine_b' | 'manual';
  symbol: string;
  display?: string;
  signal?: unknown;
  primaryEngine?: 'A' | 'B';
  candidateId?: string;
  candidateRevision?: string;
  watchId?: string;
  preferredTf?: string;
  autoReview?: boolean;
  createdAt: string;
}

export interface ScalpWorkbenchIntent {
  id: string;
  source: 'scalp_lab' | 'manual';
  symbol: string;
  signal?: unknown;
  preferredTf?: string;
  autoReview?: boolean;
  createdAt: string;
}

/** Engine A v2 signal — emitted by `analyze_pair()` (camelCase keys). */
export interface EngineASignal {
  contractVersion?: string;
  signalId?: string;
  engine?: string;
  id?: string;
  timestamp?: string;
  pair?: string;
  display?: string;
  symbol?: string;
  type?: string; // forex | crypto | commodity | index | stock | etf
  scoreGroup?: string;
  family?: string;
  subclass?: string;
  horizon?: 'intraday' | 'swing' | string;
  entryTimeframe?: string | null;
  setupId?: string | null;
  decision?: 'TRADE' | 'WATCH' | 'NO_SIGNAL' | string;
  qualified?: boolean;
  decisionTime?: string;
  lastConfirmedCandleTs?: string | null;
  validUntil?: string;
  entryZone?: [number, number] | number[] | null;
  invalidation?: number | null;
  targets?: Array<{
    label?: string;
    price?: number;
    rr?: number;
  }>;
  predicates?: Array<{
    name?: string;
    passed?: boolean;
    actual?: unknown;
    expected?: string;
  }>;
  rejectionReasons?: string[];
  validationArtifact?: {
    artifactId?: string;
    family?: string;
    subclass?: string;
    horizon?: string;
    status?: string;
    metrics?: Record<string, unknown>;
    provenanceSha256?: string;
    profileSha256?: string | null;
  } | null;
  scoringProfile?: {
    enabled?: boolean;
    style?: string;
    trendTimeframes?: string[];
    momentumTf?: string;
    regimeTf?: string;
    executionTf?: string;
    chartTf?: string;
    trendWeights?: Record<string, number>;
    profileId?: string;
    scoreGroup?: string;
    horizon?: string;
    indicatorPeriods?: Record<string, number>;
    weights?: Record<string, number>;
    directionDeadband?: number;
    tradeThreshold?: number;
    exitPolicy?: 'SINGLE_TP1' | 'SPLIT_50_50';
    profileSha256?: string;
    scorerSha256?: string;
    status?: string;
    createdAt?: string | null;
    validUntil?: string | null;
  };
  validationStatus?: 'PROMOTED' | 'UNVALIDATED' | 'UNAVAILABLE' | string;
  executionScope?: 'DEMO_ONLY' | 'NONE' | string;
  exitPolicy?: 'SINGLE_TP1' | 'SPLIT_50_50' | string;
  componentScores?: Record<string, {
    signal?: number;
    quality?: number;
    weight?: number;
    contribution?: number;
    available?: boolean;
  }>;
  scoringTimeframes?: string[];
  momentumTimeframe?: string;
  regimeTimeframe?: string;
  executionTimeframe?: string;
  timeframePolicyVersion?: string;
  timeframePolicyHash?: string;
  timeframeProfile?: string;
  regimeTf?: string;
  biasTf?: string;
  structureTf?: string;
  setupTf?: string;
  triggerTf?: string;
  executionTf?: string;
  m5Role?: 'DISABLED' | 'ADVISORY' | 'REFINEMENT' | 'EXECUTION' | string;
  baselineSpeedClass?: 'SLOW' | 'NORMAL' | 'FAST' | 'EXTREME' | string;
  liveSpeedClass?: 'SLOW' | 'NORMAL' | 'FAST' | 'EXTREME' | string;
  speedPercentile?: number | null;
  policySource?: string;
  confirmedBarTimes?: Record<string, string | number | null>;
  formingBarTime?: Record<string, string | number> | null;
  entryReadiness?: 'READY' | 'PENDING' | 'UNAVAILABLE' | string;
  entryReadinessReason?: string;
  triggerConfirmed?: boolean;
  triggerCandleClosed?: boolean;
  livePriceDriftAtr?: number | null;
  atrValue?: number | null;
  atrTimeframe?: string | null;
  structureAgeBars?: number | null;
  quoteAgeSec?: number | null;
  direction?: Direction;
  price?: number;
  trade?: boolean;
  executable?: boolean | null;
  signalTier?: string;
  engineATradeEnabled?: boolean;
  engineATradeGate?: {
    enabled?: boolean;
    research_only?: boolean;
    source?: string;
    reason?: string;
    display?: string | null;
    symbol?: string | null;
    score_group?: string | null;
    asset_type?: string | null;
    [k: string]: unknown;
  };

  // Scoring (Engine A v2: 0–3.0 scale, all asset classes)
  score?: number;
  maxScore?: number;
  threshold?: number;
  confluenceScore?: number;
  legacyScore?: number;
  legacyDirection?: Direction;
  policyScore?: number;
  policyDirection?: Direction;
  authoritativeScore?: number;
  authoritativeDirection?: Direction;
  authoritativeScoreSource?: 'POLICY' | 'LEGACY';
  scoreDelta?: number | null;
  directionChanged?: boolean;
  confluenceThreshold?: number;
  confluencePct?: number;
  /** Pre-blend score fields (V3: decision uses score before intermarket/news). */
  pre_news_score?: number;
  news_adjustment?: number;
  newsSentimentDelta?: number;
  intermarketEngineADelta?: number;
  intermarketConfirmation?: {
    verdict?: string;
    score?: number;
    engineADelta?: number;
    explanation?: string;
    [k: string]: unknown;
  };
  conviction?: number; // 0–1
  signalClass?: string; // CRITERIA / WATCHLIST / SKIP / etc.
  grade?: string;

  // Factor diagnostics (factor_scoring.py)
  factorScores?: {
    trend?: number;
    momentum?: number;
    addon?: number; // legacy
    research_lab?: number;
    mean_reversion?: number;
    ortho?: Record<string, number>;
    ortho_term?: number;
    [k: string]: number | Record<string, number> | undefined;
  };
  factorWeights?: Record<string, number>;
  orthoEnabled?: boolean;
  orthoVote?: {
    votes?: Record<string, {
      vote?: number;
      weight?: number;
      status?: string;
      [k: string]: unknown;
    }>;
    weighted_sum?: number;
    ortho_term?: number;
    enabled?: boolean;
    [k: string]: unknown;
  } | null;
  factorDiagnostics?: {
    trendDirection?: Direction | string;
    trendCoherence?: Record<string, unknown>;
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
  /**
   * Optional ATR provenance block emitted by analyze_pair (Engine A), scanner
   * Engine B overlay, engine_c consensus, and scalp_engine (Engine D).
   * Observability only — does not influence execution.
   */
  atrDiagnostics?: {
    atr_value?: number | null;
    atr_tf?: string | null;
    atr_source?: string | null;
    atr_source_engine?: string | null;
    atr_candle_last_ts?: string | null;
    atr_age_seconds?: number | null;
    atr_confirmed_only?: boolean;
    bybit_atr_available?: boolean | null;
    engine_a_atr_diagnostics?: unknown;
    engine_b_atr_diagnostics?: unknown;
    sl_method?: string | null;
    tp_method?: string | null;
  };
  /**
   * Optional ATR freshness evaluation from CONFIG['ATR_FRESHNESS'].
   * Observability by default; ``would_block`` only enforced when the policy
   * enables ``BLOCK_EXECUTION_ON_STALE_ATR``.
   */
  atrFreshness?: {
    enabled?: boolean;
    stale?: boolean;
    reason?: string | null;
    age_seconds?: number | null;
    threshold_sec?: number | null;
    would_block?: boolean;
  };
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
  engine_b_score?: number;
  engine_b_gate_score?: number;
  engine_b_gate_max?: number;
  engine_b_min_score_scaled?: number;
  engine_b_confidence_passed?: boolean;
  naked_data?: EngineBNakedResult | null; // Engine B scan signals expose this

  // News / intermarket / vision context
  newsContext?: unknown;
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
    gate_score?: number;
    gate_max_possible?: number;
    gate_pct?: number;
    max_possible?: number;
    min_score_scaled?: number;
    bonus_points?: number;
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
  profile_points?: number;
  profile_ok?: boolean;
  profile_alignment?: string;
  profile_notes?: string;
  profile_context?: {
    enabled?: boolean;
    trusted?: boolean;
    reason?: string | null;
  };

  // Canonical gate fields (authoritative for UI badges)
  engine_b_canonical_actionable?: boolean;
  engine_b_canonical_status?: string;
  engine_b_rejection_reasons?: string[];
  canonical_structure_ok?: boolean;
  canonical_location_ok?: boolean;
  canonical_trigger_ok?: boolean;
  canonical_room_ok?: boolean;
  canonical_rr_ok?: boolean;
  canonical_trade_ok?: boolean;
  canonical_status?: string;
  canonical_primary_reject_reason?: string | null;
  canonical_secondary_reject_reasons?: string[];
  canonical_badge_state?: {
    structure?: string;
    location?: string;
    trigger?: string;
    room_rr?: string;
    confidence?: string;
    trade?: string;
  };
  canonical_room_rr_ok?: boolean;
  confidence_passed?: boolean;
  suggested_levels_executable?: boolean;
  structural_tp_too_close?: boolean;

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
  tradeSignals?: EngineASignal[];
  watchlist?: EngineASignal[];
  scanned?: number;
  scannedAt?: string;
  scan_time?: number;
  pairs_scanned?: number;
  totalPairs?: number;
  activePairs?: number;
  assetClass?: string | null;
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
  rejectedDiagnostics?: EngineASignal[];
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
  structured_trade_read?: AiVisionSummary;
}

/** /api/chart-analysis response. */
export interface ChartAnalysisResponse {
  analysis?: string;
  body?: string;
  structured?: ChartVisionStructured;
  structured_trade_read?: AiVisionSummary;
  model?: string;
  symbol?: string;
  tf?: string;
  dual_tf?: boolean;
  triple_tf?: boolean;
  chart_image?: string;           // base64/data-URI PNG of the H4 chart the AI analyzed
  chart_image_h1?: string;        // base64/data-URI PNG of H1 chart (triple mode)
  chart_image_d1?: string;        // base64/data-URI PNG of D1 chart (triple mode)
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
  levelsVerdict?: string | null;
  levelsReason?: string | null;
  suggestedSL?: string | null;
  suggestedTP?: string | null;
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
  decision?: string | null;
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
  gateScore?: number | null;
  gateMax?: number | null;
  totalScore?: number | null;
  bonusPoints?: number | null;
  direction: Direction | null;
  structuralVerdict: string | null;
  structuralDataValid: boolean;
  confidencePassed: boolean;
  confidenceDisplayLabel?: string;
  structure_ok: boolean;
  location_ok: boolean;
  entry_ok: boolean;
  room_rr_ok: boolean;
  canonicalStatus?: string | null;
  canonicalTradeOk?: boolean;
  canonicalStructureOk?: boolean;
  canonicalLocationOk?: boolean;
  canonicalTriggerOk?: boolean;
  canonicalRoomOk?: boolean;
  canonicalRrOk?: boolean;
  canonicalRoomRrOk?: boolean;
  canonicalPrimaryRejectReason?: string | null;
  canonicalSecondaryRejectReasons?: string[];
  suggestedLevelsExecutable?: boolean;
  structuralTpTooClose?: boolean;
  engineBRejectionReasons?: string[];
  d1_conflict: boolean | string | null;
  hardFailReasons: string[];
  softWarnings: string[];
  diagnosticNotes: string[];
  noTriggerClassification: string | null;
  entry: number | null;
  sl: number | null;
  tp: number | null;
  rr: number | null;
  nearestSupportZone?: {
    lower?: number | null;
    upper?: number | null;
    center?: number | null;
    type?: string | null;
  } | null;
  nearestResistanceZone?: {
    lower?: number | null;
    upper?: number | null;
    center?: number | null;
    type?: string | null;
  } | null;
  sourceTimeframes?: {
    zone_tf?: string | null;
    entry_tf?: string | null;
    trigger_tf?: string | null;
    atr_tf?: string | null;
  } | null;
  engineBStyle?: string | null;
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
  traceId?: string | null;
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

export interface AiTradeChatRequest {
  session_id?: string | null;
  trace_id?: string | null;
  symbol?: string | null;
  message: string;
  include_vision?: boolean;
  include_similar_setups?: boolean;
  compare_symbol?: string | null;
  signal?: AiTradeChatSignalPayload | null;
}

export interface AiTradeChatSignalPayload {
  trace_id?: string | null;
  symbol?: string | null;
  direction?: string | null;
  engine?: string | null;
  engine_source?: string | null;
  style?: string | null;
  timeframe?: string | null;
  score?: number | null;
  threshold?: number | null;
  confidence?: number | null;
  rr?: number | null;
  rr1?: number | null;
  min_rr?: number | null;
  entry?: number | null;
  sl?: number | null;
  tp?: number | null;
  tp1?: number | null;
  tp2?: number | null;
  latest_price?: number | null;
  spread?: number | null;
  state?: string | null;
  [k: string]: unknown;
}

export interface AiContextResolutionSummary {
  mode?: 'trace_id' | 'request_signal_payload' | 'latest_symbol_signal' | 'symbol_only' | 'none' | string;
  trace_id_received?: boolean;
  signal_payload_received?: boolean;
  resolved_symbol?: string | null;
  resolved_engine?: string | null;
  warnings?: string[];
}

export interface AiTradeChatResponse {
  session_id: string;
  trace_id: string | null;
  symbol: string | null;
  answer: string;
  decision: 'NO_TRADE' | 'WATCHLIST' | 'WAIT_FOR_CONFIRMATION' | 'VALID_SETUP' | 'BLOCKED_BY_RISK' | 'DATA_INSUFFICIENT' | string;
  market_read?: string | null;
  trade_thesis?: string | null;
  supports?: string[];
  contradictions?: string[];
  facts_used?: string[];
  missing_data?: string[];
  confirmation_needed?: string[];
  invalidation?: string | null;
  historical_analogue_summary?: string | null;
  risk_warning?: string | null;
  market_intelligence?: AiMarketIntelligenceSummary;
  vision_summary?: AiVisionSummary;
  contradiction_flags?: string[];
  final_action?: string | null;
  selected_signal?: AiSelectedSignalSummary | null;
  data_checked?: AiDataCheckedSummary;
  tool_calls?: AiToolCallSummary[];
  safety?: AiAgentSafetySummary;
  strategist_summary?: AiStrategistSummary | null;
  compared_symbol?: string | null;
  compare_summary?: string | null;
  context_resolution?: AiContextResolutionSummary | null;
  created_at?: string;
}

export type AiLeeConfirmationVerdict = 'CONTEXT_SUPPORTS' | 'WAIT' | 'CONTEXT_BLOCKS' | 'NEED_MORE_DATA';
export type AiLeeConfidence = 'low' | 'medium' | 'high';

export interface AiLeeConfirmationRequest {
  trace_id?: string | null;
  symbol?: string | null;
  signal?: AiTradeChatSignalPayload | null;
}

export interface AiLeeExternalContext {
  schema_version?: 'lee_external_context.v1' | string;
  market_intelligence_freshness?: 'fresh' | 'partial' | 'stale' | 'unavailable' | 'unknown' | string;
  vision_freshness?: string | null;
  source_status?: Record<string, unknown>;
  warnings?: string[];
  missing_fields?: string[];
  [k: string]: unknown;
}

export interface AiLeeSafetyEnvelope {
  read_only?: boolean;
  can_execute?: boolean;
  can_modify_thresholds?: boolean;
  can_modify_guardian?: boolean;
  deterministic_gates_required?: boolean;
  execution_blocked?: boolean;
  note?: string | null;
  [k: string]: unknown;
}

export interface AiLeeConfirmationResponse {
  schema_version?: 'lee_confirmation.v1' | string;
  generated_at?: string;
  trace_id?: string | null;
  symbol?: string | null;
  lee_verdict: AiLeeConfirmationVerdict | string;
  display_label?: string;
  confidence?: AiLeeConfidence | string;
  narrative?: string;
  supports?: string[];
  risks?: string[];
  missing_data?: string[];
  warnings?: string[];
  safety_flags?: string[];
  market_intelligence?: AiMarketIntelligenceSummary | Record<string, unknown>;
  external_context?: AiLeeExternalContext;
  selected_signal?: AiSelectedSignalSummary | null;
  context_resolution?: AiContextResolutionSummary | Record<string, unknown> | null;
  model_used?: string;
  advisory_only?: boolean;
  execution_allowed?: boolean;
  trade_specific_confirmation_allowed?: boolean;
  safety?: AiLeeSafetyEnvelope;
  [k: string]: unknown;
}

export interface AiSelectedSignalSummary {
  symbol?: string | null;
  trace_id?: string | null;
  direction?: Direction | string | null;
  engine?: string | null;
  state?: string | null;
  score?: number | null;
  threshold?: number | null;
  rr?: number | null;
  entry?: number | null;
  sl?: number | null;
  tp?: number | null;
  style?: string | null;
  [k: string]: unknown;
}

export interface AiToolCallSummary {
  name?: string;
  tool?: string;
  status?: 'ok' | 'error' | 'skipped' | 'unavailable' | string;
  reason?: string | null;
  duration_ms?: number | null;
  args?: Record<string, unknown>;
  input?: Record<string, unknown>;
  output_summary?: string | null;
  summary?: string | null;
  error?: string | null;
  [k: string]: unknown;
}

export interface AiDataCheckedSummary {
  signal?: boolean;
  market_intelligence?: boolean;
  vision?: boolean;
  similar_setups?: boolean;
  strategist?: boolean;
  freshness?: string | null;
  warnings?: string[];
  sources?: string[];
  [k: string]: unknown;
}

export interface AiAgentSafetySummary {
  advisory_only?: boolean;
  can_execute?: boolean;
  execution_blocked?: boolean;
  note?: string | null;
  warnings?: string[];
  blocked_reasons?: string[];
  [k: string]: unknown;
}

export interface AiStrategistSummary {
  headline?: string | null;
  macro_regime?: string | null;
  key_risks?: string[];
  avoid_conditions?: string[];
  data_warnings?: string[];
  [k: string]: unknown;
}

export interface AiMarketIntelligenceSummary {
  schema_version?: string;
  freshness_status?: 'fresh' | 'partial' | 'stale' | 'unavailable' | string | null;
  warnings?: string[];
  risk_regime?: string | null;
  macro_regime?: {
    risk_regime?: string | null;
    calendar_within_72h?: unknown[];
    [k: string]: unknown;
  };
  calendar_within_72h?: unknown[];
  source_status?: Record<string, unknown>;
  pair_context?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface AiVisionSummary {
  right_edge_status?: string | null;
  tf_alignment?: string | null;
  freshness_status?: string | null;
  allowed_for_execution_context?: boolean;
  style_ratings?: Partial<Record<'scalp' | 'intraday' | 'swing', string>> | Record<string, unknown>;
  visible_obstacles?: unknown[];
  memo?: string | null;
  [k: string]: unknown;
}

export interface AiStrategistBriefResponse {
  schema_version?: 'strategist_brief.v1' | string;
  generated_at?: string;
  asset_scope?: string;
  headline?: string;
  macro_regime?: string;
  key_risks?: string[];
  watchlist?: string[];
  avoid_conditions?: string[];
  open_positions_summary?: string;
  yesterday_outcomes?: string;
  calendar_risks?: unknown[];
  data_warnings?: string[];
  full_brief?: string;
  error?: string;
  [k: string]: unknown;
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

/** ASE v2.1 shadow signal row (/api/ase-scan). */
export interface ASEFxContext {
  bias?: number | null;
  carry_diff?: number | null;
  trend_diff?: number | null;
  freshness?: string;
  [k: string]: unknown;
}

export interface ASETriangularContext {
  label?: 'CONSISTENT' | 'PARTIALLY_CONSISTENT' | 'CONTRADICTORY' | 'INSUFFICIENT_DATA' | string;
  legs?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface ASEModelProvenance {
  family?: string;
  horizon?: string;
  version?: string;
  available?: boolean;
  runtimeEligible?: boolean;
  promotionFailures?: string[];
  threshold?: number;
  thresholdSource?: string | null;
  thresholdFallback?: boolean;
  datasetHash?: string;
  trainedAt?: string;
}

export interface ASESignalRow {
  engineVersion?: string;
  modelFamily?: string;
  modelVersion?: string;
  horizon?: 'intraday' | 'swing';
  decisionStatus?: 'TRADE' | 'WATCH' | 'FLAT' | 'ERROR';
  direction?: 'LONG' | 'SHORT' | 'NONE';
  expectedNetR?: number;
  expectedNetBps?: number;
  probabilityPositive?: number;
  decisionMargin?: number;
  signalStrength?: number;
  returnQ?: Record<string, number>;
  maeQ?: Record<string, number>;
  mfeQ?: Record<string, number>;
  holdQ?: Record<string, number>;
  entryReference?: number;
  entryZone?: [number, number];
  sl?: number;
  tp1?: number;
  tp2?: number;
  maxHoldBars?: number;
  primarySignals?: Array<{ name: string; direction: number; rawStrength: number }>;
  fxContext?: ASEFxContext | null;
  triangular?: ASETriangularContext | null;
  predictionDiagnostics?: Record<string, unknown>;
  dataQuality?: Record<string, unknown>;
  modelHealth?: Record<string, unknown>;
  instrument?: string;
  decisionTimeMs?: number;
  confluenceScore?: number;
  confidence?: number;
  [k: string]: unknown;
}

export interface ASEScanResponse {
  success?: boolean;
  horizon?: string;
  family?: string;
  candidateCount?: number;
  signalCount?: number;
  statusCounts?: Record<string, number>;
  deployment?: string;
  diagnostics?: ASEScanDiagnostics;
  journalError?: string;
  signals?: ASESignalRow[];
  executions?: ASEExecutionOutcome[];
  error?: string;
}

export interface ASEBacktestDiagnostics {
  candidateCount?: number;
  tradeCount?: number;
  signalCounts?: Record<string, number>;
  nonTradeReasons?: Record<string, number>;
  sampleNonTradeSignals?: Array<Record<string, unknown>>;
  horizons?: string[];
  lookbackDays?: number;
  instrument?: string;
  family?: string;
  [k: string]: unknown;
}

export interface ASEBacktestResponse {
  success?: boolean;
  error?: string;
  engine?: 'ASE' | string;
  pair?: string;
  symbol?: string;
  type?: string;
  totalTrades?: number;
  winRate?: number;
  profitFactor?: number | null;
  expectancy?: number | null;
  sqn?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  maxDrawdownPct?: number | null;
  btStyle?: string;
  btStyleRequested?: string;
  aseDiagnostics?: ASEBacktestDiagnostics;
  equityCurve?: number[] | { idx?: number; equity: number; date?: string }[];
  trades?: Array<Record<string, unknown>>;
  [k: string]: unknown;
}

export interface ASEBatchBacktestResponse {
  success?: boolean;
  error?: string;
  engine?: 'ASE' | string;
  results?: ASEBacktestResponse[];
  errors?: Array<{ pair?: string; error?: string }>;
  totalPairs?: number;
  family?: string;
}

export interface ASEExecutionOutcome {
  executed?: boolean;
  reason?: string;
  venue?: string;
  result?: Record<string, unknown>;
  approval_volume?: number | null;
  [k: string]: unknown;
}

export interface ASEExecuteResponse {
  success?: boolean;
  executed?: boolean;
  reason?: string;
  error?: string;
  signal?: ASESignalRow;
  execution?: ASEExecutionOutcome;
}

export interface ASEScanDiagnostics {
  ptisRoot?: string;
  ptisSeriesCount?: number;
  eurusdH1CloseRows?: number;
  artifactsRoot?: string;
  horizon?: string;
  familiesWithArtifacts?: string[];
}

export interface ASEHealthResponse {
  success?: boolean;
  ready?: boolean;
  blockers?: string[];
  ptisRoot?: string;
  ptisSeriesCount?: number;
  eurusdH1CloseRows?: number;
  artifactsRoot?: string;
  horizon?: string;
  familiesWithArtifacts?: string[];
  modelProvenance?: ASEModelProvenance[];
  error?: string;
}

export interface ASEExecutedTrade {
  instrument?: string;
  direction?: string;
  horizon?: string;
  modelFamily?: string;
  entryReference?: number | null;
  sl?: number | null;
  tp1?: number | null;
  tp2?: number | null;
  expectedNetR?: number | null;
  signalStrength?: number | null;
  probabilityPositive?: number | null;
  decisionTimeMs?: number | null;
  recordedAt?: string | null;
  orderId?: string | null;
  executionStatus?: string | null;
  executionReason?: string | null;
  approvalVolume?: number | null;
  pipeline?: Partial<Record<'gate' | 'guardian' | 'risk' | 'fill', string>>;
  rr1?: number | null;
  brokerFill?: {
    fillPrice?: number;
    volume?: number;
    ticket?: string;
  };
}

export interface ASEExecutedTradesResponse {
  success?: boolean;
  trades?: ASEExecutedTrade[];
  totalExecuted?: number;
  error?: string;
}

export interface ASEShadowSummary {
  success?: boolean;
  deployment?: {
    families?: Record<string, string>;
    watchMax?: Record<string, boolean>;
  };
  journal?: {
    totalRows?: number;
    byStatus?: Record<string, number>;
    byFamily?: Record<string, number>;
    reconciled?: number;
    journalPath?: string;
  };
  error?: string;
}

// ============================================================================
// AI Chart Review v1 — POST /api/ai/chart-review
// Mirrors normalized AI response + Engine-A-vs-AI concordance from
// ai_review/normalizer.py and ai_review/concordance.py.
// Read-only; never used for execution.
// ============================================================================

export type AIReviewProvider = 'grok' | 'claude' | 'openai';
export type AIChartReviewProvider = 'default' | 'grok' | 'claude' | 'openai' | 'anthropic' | 'xai';
export type AIChartReviewVerdict = 'VALID' | 'CAUTION' | 'INVALID' | 'NO_TRADE';
export type AIChartReviewHumanAction =
  | 'take'
  | 'wait'
  | 'reject'
  | 'needs_fresher_data'
  | 'needs_better_rr';
export type AIChartReviewConcordanceState =
  | 'agree'
  | 'partial'
  | 'disagree'
  | 'unknown';
export type AIChartReviewDivergenceType =
  | 'none'
  | 'visual_contradiction'
  | 'atr_rr_issue'
  | 'freshness_issue'
  | 'entry_displacement'
  | 'missing_context'
  | 'other';

export interface AIChartReviewNormalized extends TradeSkillReview {
  verdict: AIChartReviewVerdict;
  confidence: number;
  setup_type?: string;
  visual_confirmation?: string;
  visual_contradiction?: string;
  engine_a_alignment?: string;
  atr_rr_assessment?: string;
  freshness_assessment?: string;
  entry_quality?: string;
  supporting_reasons?: string[];
  risks?: string[];
  missing_context?: string[];
  human_action?: AIChartReviewHumanAction;
  raw_model_response?: string;
  strategyPlaybookVerdict?: StrategyPlaybookVerdict | null;
  strategy_playbook_verdict?: StrategyPlaybookVerdict | null;
}

export type AIChartReviewSummaryHumanAction = 'trade' | 'wait' | 'reject' | 'watch';
export type AIChartReviewProviderStatus =
  | 'success'
  | 'failed_auth'
  | 'insufficient_credit'
  | 'rate_limited'
  | 'timeout'
  | 'fallback_used'
  | 'parse_error'
  | 'unknown';

export type AIChartReviewComparisonVerdict =
  | 'engine_a_confirmed'
  | 'engine_a_direction_confirmed_entry_rejected'
  | 'engine_a_contradicted'
  | 'engine_a_missing'
  | 'mixed'
  | 'unknown';

export interface AIChartReviewEngineAVerdictComparison {
  engineAProvided?: boolean;
  engineABiasValid?: boolean | null;
  engineAPassed?: boolean | null;
  engineADirection?: string | null;
  engineAScore?: number | null;
  engineAMaxScore?: number | null;
  engineAThreshold?: number | null;
  engineANormalizedScore?: number | null;
  engineAActiveFactors?: string[] | null;
  chartConfirmsEngineADirection?: boolean | null;
  chartContradictsEngineADirection?: boolean | null;
  chartConfirmsEntryTiming?: boolean | null;
  chartContradictsEntryTiming?: boolean | null;
  aiAgreesWithEngineA?: boolean | null;
  aiDowngradedEngineA?: boolean;
  aiUpgradedEngineA?: boolean;
  comparisonVerdict?: AIChartReviewComparisonVerdict | string;
  downgradeReasons?: string[];
  upgradeReasons?: string[];
  finalDecision?: AIChartReviewSummaryHumanAction | string | null;
  finalReason?: string | null;
}

export interface AIChartReviewEngineBVerdictComparison {
  engineBProvided?: boolean;
  engineBBiasValid?: boolean | null;
  engineBPassed?: boolean | null;
  engineBDirection?: string | null;
  engineBScore?: number | null;
  engineBMaxScore?: number | null;
  engineBThreshold?: number | null;
  engineBNormalizedScore?: number | null;
  engineBStructuralVerdict?: string | null;
  chartConfirmsEngineBDirection?: boolean | null;
  chartContradictsEngineBDirection?: boolean | null;
  chartConfirmsEntryTiming?: boolean | null;
  chartContradictsEntryTiming?: boolean | null;
  aiAgreesWithEngineB?: boolean | null;
  aiDowngradedEngineB?: boolean;
  aiUpgradedEngineB?: boolean;
  comparisonVerdict?: string;
  downgradeReasons?: string[];
  upgradeReasons?: string[];
  finalDecision?: AIChartReviewSummaryHumanAction | string | null;
  finalReason?: string | null;
}

export interface AIChartReviewEngineBContext {
  symbol?: string;
  timeframe?: string;
  chart_timeframe?: string;
  direction?: string;
  confluence_score?: number;
  max_score_override?: number;
  threshold?: number;
  passed?: boolean;
  structure_context?: Record<string, unknown>;
  timeframe_route?: TimeframeRoute;
  primary_engine?: string;
  review_type?: string;
}

export interface AIChartReviewEngineSummary {
  score: number | null;
  maxScore: number | null;
  threshold: number | null;
  normalizedScore: number | null;
  passed?: boolean | null;
  /** Provenance for `passed`: 'engine_a_v3_decision' | 'score_threshold'. */
  passedBasis?: string | null;
  direction?: string | null;
  activeFactors?: string[] | null;
  decisionState?: string | null;
  setupType?: string | null;
}

export interface AIChartReviewProviderFailure {
  provider?: string | null;
  error?: string | null;
  providerStatus?: string | null;
}

export interface AIChartReviewSummary {
  provider: string | null;
  model: string | null;
  providerStatus: AIChartReviewProviderStatus;
  fallbackUsed: boolean;
  selectedProvider?: string | null;
  providerFailure?: AIChartReviewProviderFailure | null;
  provider_failure?: AIChartReviewProviderFailure | null;
  humanAction: AIChartReviewSummaryHumanAction | string | null;
  setupType?: string | null;
  overallScore: number | null;
  tradeabilityScore: number | null;
  engineAlignmentScore: number | null;
  visualConfirmationScore: number | null;
  entryQualityScore: number | null;
  riskScore: number | null;
  confidence: number | null;
  modelConfidence?: number | null;
  confidenceCalibrated?: boolean;
  deterministicScores?: {
    overallScore: number | null;
    tradeabilityScore: number | null;
    engineAlignmentScore: number | null;
    visualConfirmationScore: number | null;
    entryQualityScore: number | null;
    riskScore: number | null;
  };
  modelScores?: {
    overallScore: number | null;
    tradeabilityScore: number | null;
    engineAlignmentScore: number | null;
    visualConfirmationScore: number | null;
    entryQualityScore: number | null;
    riskScore: number | null;
  };
  finalReason: string | null;
  sourceQualityScore?: number | null;
  engineA: AIChartReviewEngineSummary | null;
  engineB: AIChartReviewEngineSummary | null;
  engineC: AIChartReviewEngineSummary | null;
  engineD: AIChartReviewEngineSummary | null;
}

export interface AIChartReviewContextCompletenessMetadata {
  chartCapturedAt?: string | null;
  scanTimestamp?: string | null;
  latestCandleTimestamp?: string | null;
  chartProvider?: string | null;
  engineProvider?: string | null;
  providerMismatch?: boolean | null;
}

export interface AIChartReviewContextCompleteness {
  score: number | null;
  status: 'complete' | 'partial' | 'insufficient' | string;
  missingRequired: string[];
  missingOptional: string[];
  notApplicable: string[];
  metadata: AIChartReviewContextCompletenessMetadata;
}

export interface AIChartReviewMissingContextItem {
  key: string;
  label: string;
  reason: string;
  impact?: 'high' | 'medium' | 'low' | string;
  blocksTrade?: boolean;
}

export interface AIChartReviewNotApplicableContextItem {
  key: string;
  label: string;
  reason: string;
}

export interface AIChartReviewMissingContextDetailed {
  required: AIChartReviewMissingContextItem[];
  optional: AIChartReviewMissingContextItem[];
  notApplicable: AIChartReviewNotApplicableContextItem[];
}

export interface AIChartReviewFundingOi {
  fundingRate?: number | null;
  fundingRateZ?: number | null;
  openInterest?: number | null;
  openInterestDelta?: number | null;
  openInterestDeltaPct?: number | null;
  source?: string | null;
  timestamp?: string | number | null;
}

export interface AddonContext {
  addonType?: string | null;
  addonValue?: number | null;
  addonStatus?: string | null;
  addonUnsupported?: boolean | null;
  feedStatus?: Record<string, unknown> | null;
  appliesToAssetClass?: boolean | null;
  interpretation?: string | null;
}

export interface DerivativesContext extends AIChartReviewFundingOi {
  status?: 'ok' | 'unavailable' | 'not_applicable' | string;
}

export interface MicrostructureContext {
  orderBookImbalance?: number | null;
  liquidityWallDetection?: number | null;
  orderflowDelta?: number | null;
  liquidityPressure?: number | null;
  volumeMomentumSpread?: number | null;
  source?: string | null;
  ageSeconds?: number | null;
  status?: 'ok' | 'unavailable' | 'not_applicable' | string;
}

export interface IntermarketContext {
  verdict?: string | null;
  score?: number | null;
  engineADelta?: number | null;
  supportDirection?: string | null;
  supportStrength?: string | null;
  stable?: boolean | null;
  flippedRecently?: boolean | null;
  activeWindow?: number | string | null;
  topSupporting?: unknown[];
  topContradictory?: unknown[];
  unavailablePriors?: unknown[];
  severeContradiction?: boolean | null;
  explanation?: string | null;
}

export interface NewsContext {
  vote?: number | null;
  sentimentScore?: number | null;
  confidence?: number | null;
  direction?: string | null;
  delta?: number | null;
  rawDelta?: number | null;
  articleCountUsed?: number | null;
  freshArticleCount?: number | null;
  majorEventDetected?: boolean | null;
  majorEventDescription?: string | null;
  keyThemes?: string[];
  reasoningSummary?: string | null;
}

export interface EngineANonVisualContext {
  addonContext?: AddonContext;
  derivativesContext?: DerivativesContext;
  microstructureContext?: MicrostructureContext;
  intermarketContext?: IntermarketContext;
  newsContext?: NewsContext;
  macroContext?: Record<string, unknown>;
}

export interface ScoreAttribution {
  technicalScoreRaw?: number | null;
  intermarketDelta?: number | null;
  scoreAfterIntermarket?: number | null;
  newsSentimentDelta?: number | null;
  finalEngineAScore?: number | null;
  threshold?: number | null;
  maxScore?: number | null;
  scoreSource?: string | null;
  aiReviewCanMutateScore?: boolean;
}

export interface AIChartReviewAtrDiagnostics {
  atrD1?: number | null;
  atrH4?: number | null;
  atrChartTf?: number | null;
  atrSource?: string | null;
  atrTimeframe?: string | null;
  atrAgeSeconds?: number | null;
  atrConfirmedOnly?: boolean | null;
  atrCandleLastTs?: string | null;
}

export interface AIChartReviewResistanceMap {
  nearestResistance?: number | null;
  distanceToNearestResistance?: number | null;
  tp?: number | null;
  tpClearsResistance?: boolean | null;
  htfSwingHighs: number[];
  profileLevels: {
    poc?: number | null;
    vah?: number | null;
    val?: number | null;
  };
  profileLevelsTrusted?: boolean;
  emaLevels: {
    ema50?: number | null;
    ema200?: number | null;
    dema200?: number | null;
  };
  supplyZones: unknown[];
}

export interface AIChartReviewConcordance {
  engine: 'A' | 'B';
  engine_a_direction?: 'LONG' | 'SHORT' | 'NONE';
  engine_b_direction?: 'LONG' | 'SHORT' | 'NONE';
  engine_a_score?: number | null;
  engine_b_score?: number | null;
  engine_a_threshold?: number | null;
  engine_b_threshold?: number | null;
  engine_a_passed?: boolean;
  engine_b_passed?: boolean;
  engine_b_structural_verdict?: string | null;
  ai_verdict?: AIChartReviewVerdict;
  ai_human_action?: AIChartReviewHumanAction;
  concordance: AIChartReviewConcordanceState;
  divergence_type: AIChartReviewDivergenceType;
  divergence_note?: string;
  should_flag_for_review: boolean;
}

export interface TimeframeRoute {
  schemaVersion?: string;
  enabled?: boolean;
  engine?: string;
  assetGroup?: string;
  sourceGroup?: string;
  contextTf?: string;
  regimeTf?: string;
  biasTf?: string;
  structureTf?: string;
  setupTf?: string;
  triggerTf?: string;
  entryTf?: string;
  executionTf?: string;
  executionMode?: string;
  m5Role?: string;
  m5Policy?: string;
  policyVersion?: string;
  policyKey?: string;
  routeSource?: string;
  autoSelectTf?: string;
  mode?: string;
  reason?: string;
}

export interface AIChartReviewEngineAContext {
  symbol?: string;
  timeframe?: string;
  asset_class?: string;
  asset_group?: string;
  direction?: 'LONG' | 'SHORT' | 'NONE';
  regime?: string;
  scan_timestamp?: string;
  candidate_timestamp?: string;
  latest_candle_ts?: string;
  chart_captured_at?: string;
  engine_a_provider?: string;
  chart_provider_hint?: string;
  provider_mismatch?: boolean;
  confluence_score?: number | null;
  max_score_override?: number | null;
  threshold?: number | null;
  passed?: boolean;
  factor_diagnostics?: Record<string, unknown>;
  multiplier_diagnostics?: Record<string, unknown>;
  equity_session?: {
    applied?: boolean;
    reason?: string | null;
    utc_hour?: number | null;
    multiplier?: number | null;
  };
  session_diagnostics?: Record<string, unknown>;
  directional_alignment?: Record<string, unknown>;
  atr?: {
    atr_value?: number | null;
    atr_tf?: string;
    atr_source?: string;
    atr_candle_last_ts?: string;
    atr_age_seconds?: number | null;
    atr_confirmed_only?: boolean;
    atr_cache_hit?: boolean;
    atr_freshness_status?: 'fresh' | 'expected_lag' | 'stale' | 'unknown';
    max_expected_age_seconds?: number;
  };
  geometry?: {
    candidate_entry?: number | null;
    current_price?: number | null;
    stop_loss?: number | null;
    take_profit?: number | null;
    risk_points?: number | null;
    reward_points?: number | null;
    rr?: number | null;
    price_displacement_from_candidate_entry?: number | null;
    sl_tp_source?: string;
  };
  freshness?: {
    cache_hit?: boolean;
    bucket_lag?: number | null;
    stale_warnings?: string[];
  };
  non_visual_context?: EngineANonVisualContext;
  engine_a_non_visual_context?: EngineANonVisualContext;
  score_attribution?: ScoreAttribution;
  timeframe_route?: TimeframeRoute;
  mismatch_warnings?: string[];
  [k: string]: unknown;
}

export interface ReviewInputMeta {
  symbol?: string | null;
  signalEngine?: 'A' | 'B' | 'D' | 'unknown' | string;
  signalTimeframe?: string | null;
  chartTimeframe?: string | null;
  structureChartTimeframe?: string | null;
  entryChartTimeframe?: string | null;
  hasEngineASignal?: boolean;
  hasEngineBOverlay?: boolean;
  hasChartImage?: boolean;
  hasStructureChartImage?: boolean;
  hasEntryChartImage?: boolean;
  reviewImagePolicySource?: 'selected_candidate' | 'review_reanalysis' | string;
  reviewImagePolicyDifferences?: Array<{
    role?: 'structure' | 'entry' | string;
    candidate?: string | null;
    reanalysis?: string | null;
  }>;
  timeframeRouteApplied?: boolean;
}

export interface AIChartReviewResponse {
  review_id: string | null;
  provider: string;
  selectedProvider?: string | null;
  fallbackUsed?: boolean;
  fallback_used?: boolean;
  providerFailure?: Record<string, unknown> | null;
  provider_failure?: Record<string, unknown> | null;
  model: string | null;
  latency_ms?: number | null;
  engine_a_context?: AIChartReviewEngineAContext;
  engine_b_context?: AIChartReviewEngineBContext;
  engineBContext?: AIChartReviewEngineBContext;
  primaryEngine?: 'A' | 'B' | string;
  ai_review: AIChartReviewNormalized;
  concordance: AIChartReviewConcordance;
  timestamps: {
    scan_timestamp?: string | null;
    chart_captured_at?: string | null;
    structure_chart_captured_at?: string | null;
    entry_chart_captured_at?: string | null;
    latest_candle_ts?: string | null;
  };
  mismatch_warnings: string[];
  dedup_hit: boolean;
  ai_review_summary?: AIChartReviewSummary;
  aiReviewSummary?: AIChartReviewSummary;
  engineAVerdictComparison?: AIChartReviewEngineAVerdictComparison;
  engine_a_verdict_comparison?: AIChartReviewEngineAVerdictComparison;
  engineBVerdictComparison?: AIChartReviewEngineBVerdictComparison;
  engine_b_verdict_comparison?: AIChartReviewEngineBVerdictComparison;
  timeframeRoute?: TimeframeRoute;
  timeframe_route?: TimeframeRoute;
  reviewInputMeta?: ReviewInputMeta;
  contextCompleteness?: AIChartReviewContextCompleteness;
  missingContextDetailed?: AIChartReviewMissingContextDetailed;
  fundingOi?: AIChartReviewFundingOi;
  derivativesContext?: DerivativesContext;
  nonVisualContext?: EngineANonVisualContext;
  engineANonVisualContext?: EngineANonVisualContext;
  scoreAttribution?: ScoreAttribution;
  engineAScoreAttribution?: ScoreAttribution;
  atrDiagnostics?: AIChartReviewAtrDiagnostics;
  resistanceMap?: AIChartReviewResistanceMap;
  suggestedTradePlan?: SuggestedTradePlan | null;
  suggested_trade_plan?: SuggestedTradePlan | null;
  strategyPlaybookVerdict?: StrategyPlaybookVerdict | null;
  strategy_playbook_verdict?: StrategyPlaybookVerdict | null;
}

export interface StrategyPlaybookVerdict {
  final_verdict?: 'TRADE_VALID' | 'WAIT_FOR_TRIGGER' | 'NO_TRADE' | 'CONFLICT_REJECT' | 'DATA_INSUFFICIENT' | string;
  matched_strategy_model?: string;
  direction?: 'LONG' | 'SHORT' | 'NONE' | string;
  market_state?: string | null;
  location_quality?: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | string;
  trigger_quality?: 'CONFIRMED' | 'PARTIAL' | 'MISSING' | 'INVALID' | string;
  invalidation_level?: number | null;
  target_logic?: string | null;
  rr_quality?: 'GOOD' | 'ACCEPTABLE' | 'POOR' | 'UNKNOWN' | string;
  conflict_notes?: string[];
  required_next_confirmation?: string[];
  classifier_agreement?: 'AGREE' | 'OVERRIDE_ADDED' | 'OVERRIDE_REJECTED' | 'N/A' | string;
  plain_english_reason?: string;
}

export interface AIChartReviewScreenshotMeta {
  width: number;
  height: number;
  native_chart: true;
  visible_range_start?: string;
  visible_range_end?: string;
  chart_timeframe: string;
  overlays: string[];
  captured_at: string;
  provider?: string;
  chart_provider?: string;
  chart_snapshot?: AIChartReviewChartSnapshot;
  /** Advisory routing hints for server-side analyze_pair style (not trusted for scoring). */
  analyze_style?: string;
  signal_style?: string;
  scoring_timeframes?: string[];
  momentum_timeframe?: string;
  regime_timeframe?: string;
  execution_timeframe?: string;
  /** Advisory seed for Engine B chart review direction (server re-validates). */
  candidate_direction?: string;
  /** Server lookup key/assertion only; never a trusted score or entry source. */
  candidate_id?: string;
  candidate_revision?: string;
  /** Server-side suggested-trade watch lookup key; resolves parity from the persisted watch, not the scan cache. */
  suggested_watch_id?: string;
  primary_engine?: 'A' | 'B';
  signal_engine?: 'A' | 'B';
  renderedLayers?: Record<string, boolean>;
  review_role?: 'structure' | 'entry';
}

export interface AIChartReviewChartSnapshot {
  renderedLayers: string[];
  visibleRange?: {
    from?: string | number | null;
    to?: string | number | null;
  } | null;
  visibleCandleCount?: number | null;
  lastCandleTs?: string | number | null;
  lastCandleAgeSec?: number | null;
  chartDataStatus?: 'ready' | 'stale' | 'unavailable';
  indicatorLayerStates?: Record<string, boolean>;
  engineBOverlayCount?: number;
  engineBContext?: Record<string, unknown> | null;
  engineBOverlayStatus?: 'disabled' | 'loading' | 'ready' | 'stale' | 'error' | 'unavailable';
  engineBOverlayAgeSec?: number | null;
  engineBOverlayError?: string | null;
  engineBOverlayMeta?: {
    chartTimeframe?: string | null;
    sourceTimeframe?: string | null;
    engineBStyle?: string | null;
    overlaySource?: string | null;
    sourceTimeframes?: {
      zone_tf?: string | null;
      entry_tf?: string | null;
      trigger_tf?: string | null;
      atr_tf?: string | null;
    } | null;
  } | null;
  priceRange?: {
    min?: number | null;
    max?: number | null;
  } | null;
  provider?: string | null;
  /**
   * Diagnostics-only snapshot of the indicator values drawn on the chart, used
   * by the backend to flag chart-vs-Engine-A indicator divergence. Never trusted
   * for scoring; the backend re-runs Engine A from server state.
   */
  chartIndicators?: {
    timeframe?: string | null;
    /** Trend EMA drawn at the per-group period Engine A scores with. */
    emaTrend?: number | null;
    ema50?: number | null;
    ema200?: number | null;
    rsi14?: number | null;
    atr14?: number | null;
    adx14?: number | null;
    periods?: {
      ema?: { trend?: number | null; momentum?: number | null; long?: number | null } | null;
      rsi?: number | null;
      adx?: number | null;
      atr?: number | null;
      source?: string | null;
    } | null;
  } | null;
}

export interface AIChartReviewRequest {
  symbol: string;
  /** Server-resolved structure timeframe represented by screenshot_base64. */
  timeframe: string;
  provider?: AIChartReviewProvider;
  /** Structure/zone chart image. */
  screenshot_base64: string;
  screenshot_meta: AIChartReviewScreenshotMeta;
  /** Trigger/entry chart image. */
  entry_screenshot_base64: string;
  entry_screenshot_meta: AIChartReviewScreenshotMeta;
}

export interface EngineBHotlistCandidate {
  symbol: string;
  display: string;
  asset_type: string;
  source: string;
  latest_price: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  change_pct: number | null;
  strength_score: number;
  rank: number;
  reason: string;
  freshness_seconds?: number | null;
  freshness_status?: 'fresh' | 'delayed' | 'stale' | 'missing' | string;
  components: {
    freshness: number;
    movement: number;
    rangeExpansion: number;
    activity?: number;
    spread: number;
    session: number;
  };
}

export type EngineBHotlistSlotStatus =
  | 'PRIORITY'
  | 'HOT'
  | 'SCOUT'
  | 'WEAK_SCOUT'
  | 'NO_HOT_SETUP';

export interface EngineBHotlistSlot {
  group: string;
  symbol: string | null;
  display: string | null;
  asset_type: string | null;
  selected_at: string;
  last_score: number | null;
  status: EngineBHotlistSlotStatus | string;
  data_status: 'fresh' | 'stale' | 'data_missing' | string;
  locked: boolean;
  below_since: number | null;
  switched: boolean;
  switch_cause: string;
  previous_symbol: string | null;
  eligible_for_prime: boolean;
  ai_review_enabled: boolean;
  reason: string;
  candidate: EngineBHotlistCandidate | null;
}

export interface EngineBHotlistAlternate {
  symbol: string;
  display: string;
  asset_type: string;
  strength_score: number;
  change_pct: number | null;
  freshness_status?: string | null;
  reason: string;
  rank: number;
}

export interface EngineBHotlistGroup {
  group: 'forex' | 'crypto' | 'commodity' | 'index' | string;
  winner: EngineBHotlistCandidate | null;
  selected?: EngineBHotlistSlot;
  alternates?: EngineBHotlistAlternate[];
  candidates: EngineBHotlistCandidate[];
}

export interface EngineBHotlistResponse {
  success: boolean;
  payloadVersion: 'engine-b-hotbench-v1' | 'engine-b-hotbench-v2' | string;
  generated_at: string;
  groups: Record<string, EngineBHotlistGroup>;
  selectedSymbols: string[];
  selectedSlots?: Record<string, EngineBHotlistSlot>;
  thresholds?: {
    switch_margin: number;
    scout_min_score: number;
    hot_min_score: number;
    priority_min_score: number;
    weak_ttl_sec: number;
  };
  scoring: {
    usesAi: false;
    usesScreenshots: false;
    usesFullEngineB: false;
  };
}

export type EngineBHotBenchStatus =
  | 'SCOUTING'
  | 'PRIMED'
  | 'ENGINE_B_CLEAR'
  | 'NEAR_ZONE'
  | 'WATCH_ARMED'
  | 'READY_FOR_AI_REVIEW'
  | 'AI_ENTRY_NOW'
  | 'AI_WAIT'
  | 'AI_SKIP'
  | 'INVALIDATED'
  | 'EXPIRED'
  | 'MISSING';

export interface EngineBHotBenchCardState {
  status: EngineBHotBenchStatus;
  reason: string;
  watchStatus?: string | null;
  reviewStatus?: 'AI_ENTRY_NOW' | 'AI_WAIT' | 'AI_SKIP' | null;
}

export type ScalpAIReviewHumanAction = 'trade' | 'wait' | 'reject' | 'watch';

export type ScalpAIReviewComparisonVerdict =
  | 'setup_confirmed'
  | 'direction_confirmed_entry_rejected'
  | 'setup_contradicted'
  | 'source_quality_insufficient'
  | 'setup_missing'
  | 'mixed'
  | 'unknown';

export interface ScalpAIReviewSummary {
  provider?: string | null;
  model?: string | null;
  providerStatus?: string | null;
  fallbackUsed?: boolean;
  humanAction?: ScalpAIReviewHumanAction | string | null;
  setupType?: string | null;
  overallScore?: number | null;
  tradeabilityScore?: number | null;
  visualConfirmationScore?: number | null;
  entryQualityScore?: number | null;
  riskScore?: number | null;
  sourceQualityScore?: number | null;
  confidence?: number | null;
  finalReason?: string | null;
  engineD?: {
    aiGrade?: string | null;
    aiScore?: number | null;
    direction?: string | null;
    executable?: boolean | null;
    executionTf?: string | null;
  } | null;
}

export interface ScalpVerdictComparison {
  setupProvided?: boolean;
  setupDirection?: string | null;
  setupGrade?: string | null;
  setupScore?: number | null;
  setupPassed?: boolean | null;
  chartConfirmsDirection?: boolean | null;
  chartContradictsDirection?: boolean | null;
  chartConfirmsEntryTiming?: boolean | null;
  chartContradictsEntryTiming?: boolean | null;
  sourceQualitySupportsReview?: boolean | null;
  aiDowngradedSetup?: boolean;
  comparisonVerdict?: ScalpAIReviewComparisonVerdict | string;
  downgradeReasons?: string[];
  finalDecision?: string | null;
  finalReason?: string | null;
}

export interface ScalpContextCompleteness {
  score?: number;
  status?: 'complete' | 'partial' | 'insufficient' | string;
  missingRequired?: string[];
  missingOptional?: string[];
  notApplicable?: string[];
  metadata?: {
    chartCapturedAt?: string | null;
    latestCandleTimestamp?: string | null;
    latestCandleTs?: string | null;
    candleSource?: string | null;
    orderflowSource?: string | null;
    vpSource?: string | null;
  };
}

export interface ScalpAIReviewConcordance {
  engine?: 'D';
  setup_direction?: string | null;
  setup_grade?: string | null;
  setup_executable?: boolean;
  ai_verdict?: string;
  ai_human_action?: string;
  concordance?: 'agree' | 'partial' | 'disagree' | 'unknown';
  divergence_type?: string;
  divergence_note?: string;
  should_flag_for_review?: boolean;
}

export interface ScalpAIReviewNormalized extends TradeSkillReview {
  verdict?: string;
  confidence?: number;
  setup_type?: string;
  visual_confirmation?: string;
  visual_contradiction?: string;
  entry_quality?: string;
  source_quality_assessment?: string;
  supporting_reasons?: string[];
  risks?: string[];
  missing_context?: string[];
  human_action?: string;
  parse_success?: boolean;
  structured?: Record<string, unknown>;
}

export interface ScalpEngineDContext {
  symbol?: string;
  timeframe?: string;
  execution_tf?: string;
  direction?: string;
  ai_grade?: string | null;
  ai_score?: number | null;
  executable?: boolean;
  gate_result?: string | null;
  strict_fabio_pass?: boolean | null;
  setup_available?: boolean;
  scan_timestamp?: string;
  latest_candle_ts?: string;
  chart_captured_at?: string;
  scalpSetup?: Record<string, unknown>;
  marketLocation?: Record<string, unknown>;
  aggressionContext?: Record<string, unknown>;
  sourceContract?: Record<string, unknown>;
  mismatch_warnings?: string[];
}

export interface ScalpAIChartReviewResponse {
  review_id: string | null;
  provider: string;
  selectedProvider?: string | null;
  fallbackUsed?: boolean;
  fallback_used?: boolean;
  providerFailure?: Record<string, unknown> | null;
  provider_failure?: Record<string, unknown> | null;
  model: string | null;
  latency_ms?: number | null;
  dedup_hit?: boolean;
  engine_d_context?: ScalpEngineDContext;
  engineDContext?: ScalpEngineDContext;
  ai_review?: ScalpAIReviewNormalized;
  concordance?: ScalpAIReviewConcordance;
  timestamps?: {
    scan_timestamp?: string | null;
    chart_captured_at?: string | null;
    latest_candle_ts?: string | null;
  };
  mismatch_warnings?: string[];
  aiReviewSummary?: ScalpAIReviewSummary;
  ai_review_summary?: ScalpAIReviewSummary;
  scalpVerdictComparison?: ScalpVerdictComparison;
  scalp_verdict_comparison?: ScalpVerdictComparison;
  reviewInputMeta?: ReviewInputMeta;
  contextCompleteness?: ScalpContextCompleteness;
  context_completeness?: ScalpContextCompleteness;
  strategy_layer?: Record<string, unknown>;
  strategyLayer?: Record<string, unknown>;
  suggestedTradePlan?: SuggestedTradePlan | null;
  suggested_trade_plan?: SuggestedTradePlan | null;
}

export interface ScalpChartSnapshot {
  symbol: string | null;
  timeframe: string | null;
  chartCapturedAt: string;
  visibleRange: { from: string | null; to: string | null };
  latestCandleTs?: string | null;
  selectedSignal: {
    direction: string | null;
    setupType: string | null;
    entry: number | null;
    stopLoss: number | null;
    tp1: number | null;
    tp2: number | null;
    rr1: number | null;
    rr2: number | null;
    grade?: string | null;
    candidate_phase?: string | null;
    setup_type?: string | null;
  };
  profile: {
    poc: number | null;
    vah: number | null;
    val: number | null;
    priorPoc: number | null;
    priorVah: number | null;
    priorVal: number | null;
    lvns: number[];
    hvns: number[];
  };
  liquidity: Record<string, number | null | undefined>;
  engineBContext: Record<string, unknown>;
  orderFlow: {
    source: string;
    largeTradeEvents: unknown[];
    absorptionEvents: unknown[];
    initiativeEvents: unknown[];
    exhaustionEvents: unknown[];
    cvdSeriesAvailable: boolean;
    liquidationEventsAvailable: boolean;
  };
  advisory: {
    oldGateResult: string | null;
    oldExecutable: boolean;
    warnings: string[];
    riskAdvisory: Record<string, unknown>;
    sourceAdvisory: Record<string, unknown>;
  };
  renderedLayers: Record<string, boolean>;
  thesisBadge?: Record<string, string | null | undefined>;
  marketLocation?: Record<string, unknown>;
  aggressionContext?: Record<string, unknown>;
  sourceContract?: Record<string, unknown>;
}

export interface ScalpAIChartReviewScreenshotMeta {
  width: number;
  height: number;
  native_chart: true;
  chart_timeframe: string;
  overlays: string[];
  captured_at: string;
  execution_tf?: string;
  visible_range_start?: string;
  visible_range_end?: string;
  chart_provider?: string;
  chart_snapshot?: ScalpChartSnapshot;
  rendered_layers?: Record<string, boolean>;
  missing_layers?: string[];
  source_fidelity_label?: string;
}

export interface ScalpAIChartReviewRequest {
  symbol: string;
  timeframe: string;
  provider?: AIChartReviewProvider;
  screenshot_base64: string;
  screenshot_meta: ScalpAIChartReviewScreenshotMeta;
}

// ---------------------------------------------------------------------------
// Phase 6 — UX surface types (citations, calibration, disagreement, what-if)
// Advisory-only; every response carries do_not_auto_apply where applicable.
// ---------------------------------------------------------------------------

export interface EvidenceRef {
  claim_id?: string | number;
  text: string;
  source_field?: string;
  source_value?: unknown;
  ungrounded: boolean;
}

export interface EvidenceRefsResponse {
  enabled: boolean;
  evidence_refs: EvidenceRef[];
  do_not_auto_apply: boolean;
  error?: string;
}

export interface CalibrationBin {
  bin: string;
  count: number;
  avg_confidence: number | null;
  realized_accuracy: number | null;
  gap: number | null;
}

export interface ConfidenceCalibrationResponse {
  schema_version: string;
  enabled: boolean;
  bins: CalibrationBin[];
  surface?: string | null;
  lookback_days?: number;
  do_not_auto_apply: boolean;
  error?: string;
}

export interface AIDisagreementResponse {
  schema_version: string;
  enabled: boolean;
  surfaces?: string[];
  decisions?: Record<string, string | null>;
  confidences?: Record<string, number | null>;
  decision_agreement?: boolean;
  confidence_spread?: number;
  divergent: boolean;
  summary?: string;
  advisory_only: boolean;
  error?: string;
}

export interface WhatIfReplayResponse {
  schema_version: string;
  enabled: boolean;
  hypothetical_context: Record<string, unknown> | null;
  applied_overrides: string[];
  rejected_overrides: string[];
  advisory_only: boolean;
  do_not_auto_apply: boolean;
  error?: string;
}

export interface EvidenceRefClaim {
  claim_id?: string | number;
  text?: string;
  claim?: string;
  source_field?: string;
}

export interface AiSurfaceVerdict {
  decision?: string | null;
  verdict?: string | null;
  confidence?: number | null;
}
