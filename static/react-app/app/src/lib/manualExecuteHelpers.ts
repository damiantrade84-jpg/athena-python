import type {
  AIChartReviewResponse,
  AiTextReviewResponse,
  EngineASignal,
  EngineBNakedResult,
  ScalpAIChartReviewResponse,
  SuggestedTradePlan,
} from '@/types/athena';
import { isEngineAV3Signal, resolveEngineAV3Signal } from '@/lib/engineAV3';
import { readEngineBCanonicalGatesFromNaked } from '@/lib/engineBCanonicalGates';

export type QuickExecuteStyle = 'scalp' | 'intraday' | 'swing' | 'auto';
export type ExecutionVolumeMode = 'min_lot' | 'calculated';
export type ScalpExecutionMode = 'paper' | 'demo' | 'live_disabled' | 'live';

export type ExitMode = 'traditional_static' | 'adaptive_trail' | 'manual' | 'time_based';
// 'default' = no per-trade override; backend resolves per-group -> global.
export type ExitModeSelection = ExitMode | 'default';

export function buildExitModePayload(
  args: { exitMode?: ExitModeSelection } = {},
): { exit_mode?: ExitMode } {
  const m = args.exitMode;
  return m && m !== 'default' ? { exit_mode: m } : {};
}

export interface ExecutionVolumeArgs {
  volumeMode?: ExecutionVolumeMode;
  sizingOverride?: number;
}

export function buildExecutionVolumePayload(args: ExecutionVolumeArgs = {}): {
  volume_mode: ExecutionVolumeMode;
  sizing_override: number;
} {
  const volumeMode = args.volumeMode ?? 'min_lot';
  const sizingOverride = Math.max(0.25, Math.min(1.0, args.sizingOverride ?? 1.0));
  return {
    volume_mode: volumeMode,
    sizing_override: volumeMode === 'calculated' ? sizingOverride : 1.0,
  };
}

export interface RiskPreviewResponse {
  approved?: boolean;
  reason?: string;
  volume?: number;
  volume_mode?: string;
  volume_source?: string;
  risk_amount?: number;
  risk_pct?: number;
  vol_min?: number;
  vol_step?: number;
  venue?: string;
  pair?: string;
  asset_type?: string;
  error?: string;
}

export function formatRiskPreviewLine(preview: RiskPreviewResponse | null): string | null {
  if (!preview || preview.error) return null;
  if (!preview.approved) {
    return preview.reason ? `Volume unavailable: ${preview.reason}` : 'Volume unavailable';
  }
  const unit = preview.asset_type === 'stock'
    ? 'shares'
    : preview.asset_type === 'crypto'
      ? 'units'
      : 'lots';
  const vol = typeof preview.volume === 'number' ? preview.volume : null;
  const riskAmt = typeof preview.risk_amount === 'number' ? preview.risk_amount : null;
  const riskPct = typeof preview.risk_pct === 'number' ? preview.risk_pct * 100 : null;
  const venue = preview.venue ? preview.venue.toUpperCase() : '—';
  if (vol == null) return null;
  const riskPart = riskAmt != null && riskPct != null
    ? ` · Risk: $${riskAmt.toFixed(2)} (${riskPct.toFixed(2)}%)`
    : '';
  return `Volume: ${vol} ${unit}${riskPart} · Venue: ${venue}`;
}

export interface ScalpExecuteSignalLike {
  symbol?: string | null;
  pair?: string;
  display?: string;
  direction?: string;
  type?: string;
  entry?: number;
  price?: number;
  sl?: number;
  tp1?: number;
  tp?: number;
  executable?: boolean;
  gate_result?: string;
  ai_grade?: string;
  ai_review_min_grade?: string;
  grade?: string;
  strict_fabio_pass?: boolean;
  strict_fabio_pillars?: Record<string, boolean>;
  strictOrderflowSourcePass?: boolean | null;
  data_fidelity?: {
    vp_is_proxy?: boolean;
    cvd_is_proxy?: boolean;
    absorption_is_proxy?: boolean;
    aggression_uses_real_order_flow?: boolean;
  };
  vp_is_proxy?: boolean;
  cvd_is_proxy?: boolean;
  absorption_is_proxy?: boolean;
  aggression_uses_real_order_flow?: boolean;
  [key: string]: unknown;
}

export function normalizeSymbolKey(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const upper = value.trim().toUpperCase();
  if (!upper) return null;
  const withoutProvider = upper.includes(':') ? upper.split(':').pop() || upper : upper;
  const withoutYahooFxSuffix = withoutProvider.replace(/=X$/, '');
  const key = withoutYahooFxSuffix.replace(/[^A-Z0-9]/g, '');
  return key || null;
}

function normalizeBackendTf(tf: string | null | undefined): string | null {
  if (!tf) return null;
  const raw = tf.trim().toUpperCase().replace(/\s+/g, '');
  if (!raw) return null;
  if (raw === '240' || raw === '4H') return 'H4';
  if (raw === '60' || raw === '1H') return 'H1';
  if (raw === '15' || raw === '15M') return 'M15';
  if (raw === '5' || raw === '5M') return 'M5';
  if (raw === '1' || raw === '1M') return 'M1';
  if (raw === 'D' || raw === '1D') return 'D1';
  if (raw === 'W' || raw === '1W') return 'W1';
  return raw;
}

export function stripEngineBFromSignal(signal: EngineASignal): EngineASignal {
  const payload = { ...signal } as Record<string, unknown>;
  delete payload.engine_b;
  delete payload.naked_data;
  delete payload.is_naked;
  return payload as EngineASignal;
}

function positiveNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined;
}

/** Resolve entry/SL for risk preview when Engine B stores levels off top-level fields. */
export function resolveEngineBExecutionPreviewLevels(
  signal: EngineASignal,
  opts?: { engineBOnly?: boolean },
): { entry?: number; sl?: number } {
  const raw = signal as Record<string, unknown>;
  const status = raw.engine_b_status as Record<string, unknown> | undefined;
  const engineB = raw.engine_b as Record<string, unknown> | undefined;
  const naked = raw.naked_data as Record<string, unknown> | undefined;

  const sl =
    positiveNumber(raw.engine_b_execution_sl as number | undefined)
    ?? positiveNumber(status?.execution_sl as number | undefined)
    ?? positiveNumber(status?.engine_b_execution_sl as number | undefined)
    ?? (opts?.engineBOnly ? undefined : positiveNumber(raw.sl as number | undefined))
    ?? positiveNumber(engineB?.execution_sl as number | undefined)
    ?? positiveNumber(naked?.execution_sl as number | undefined)
    ?? positiveNumber(engineB?.recommended_stop_loss as number | undefined)
    ?? positiveNumber(naked?.recommended_stop_loss as number | undefined)
    ?? positiveNumber(raw.sl as number | undefined);

  const entry =
    positiveNumber(raw.entry as number | undefined)
    ?? positiveNumber(raw.price as number | undefined)
    ?? positiveNumber(status?.current_price as number | undefined)
    ?? positiveNumber(engineB?.current_price as number | undefined)
    ?? positiveNumber(naked?.current_price as number | undefined)
    ?? positiveNumber(engineB?.price as number | undefined);

  return { entry, sl };
}

function engineBNakedPayload(signal: EngineASignal): EngineBNakedResult | null {
  const raw = signal as Record<string, unknown>;
  const naked = (raw.naked_data ?? raw.engine_b) as EngineBNakedResult | undefined;
  return naked && typeof naked === 'object' ? naked : null;
}

function engineBCanonicalTradeOk(signal: EngineASignal): boolean | null {
  const raw = signal as Record<string, unknown>;
  const topLevel = raw.canonical_trade_ok ?? raw.engine_b_canonical_actionable;
  if (topLevel !== undefined && topLevel !== null) return Boolean(topLevel);
  const naked = engineBNakedPayload(signal);
  if (!naked) return null;
  const gates = readEngineBCanonicalGatesFromNaked(naked);
  return gates?.canonicalTradeOk ?? null;
}

function engineBExecutableTp(signal: EngineASignal): number | undefined {
  const raw = signal as Record<string, unknown>;
  const status = raw.engine_b_status as Record<string, unknown> | undefined;
  const engineB = raw.engine_b as Record<string, unknown> | undefined;
  const naked = engineBNakedPayload(signal);
  return positiveNumber(raw.engine_b_execution_tp1)
    ?? positiveNumber(raw.engine_b_execution_tp)
    ?? positiveNumber(status?.execution_tp1)
    ?? positiveNumber(status?.execution_tp)
    ?? positiveNumber(signal.tp1 ?? signal.tp)
    ?? positiveNumber(naked?.execution_tp1)
    ?? positiveNumber(naked?.execution_tp)
    ?? positiveNumber(naked?.recommended_take_profit as number | undefined)
    ?? positiveNumber(naked?.final_take_profit as number | undefined)
    ?? positiveNumber(engineB?.execution_tp1)
    ?? positiveNumber(engineB?.execution_tp)
    ?? positiveNumber(engineB?.recommended_take_profit as number | undefined);
}

/** Engine B score threshold pass is separate from canonical actionability. */
export function canExecuteEngineBSignal(signal: EngineASignal | null): boolean {
  if (!signal || !isEngineBOnlySignal(signal)) return false;
  if (signal.executable === false) return false;
  const canonicalOk = engineBCanonicalTradeOk(signal);
  if (canonicalOk === false) return false;
  const { entry, sl } = resolveEngineBExecutionPreviewLevels(signal, { engineBOnly: true });
  const tp = engineBExecutableTp(signal);
  return isPositiveNumber(entry) && isPositiveNumber(sl) && isPositiveNumber(tp);
}

export function engineBExecuteBlockReason(signal: EngineASignal | null): string | null {
  if (!signal || !isEngineBOnlySignal(signal)) return null;
  if (signal.executable === false) {
    // MT5 scan spread gate (apply_mt5_spread_to_sl_scan_gate) flips executable
    // off after revalidation - surface its reason instead of a generic block.
    const spreadGate = signal as {
      spreadToSlBlockReason?: string;
      executionBlockReason?: string;
      spreadToSlRatio?: number;
      spreadToSlRatioCap?: number;
    };
    if (
      spreadGate.spreadToSlBlockReason === 'SPREAD_TOO_WIDE_FOR_SL'
      || spreadGate.executionBlockReason === 'SPREAD_TOO_WIDE_FOR_SL'
    ) {
      const ratio = spreadGate.spreadToSlRatio;
      const cap = spreadGate.spreadToSlRatioCap;
      return typeof ratio === 'number' && typeof cap === 'number'
        ? `Spread too wide: ${(ratio * 100).toFixed(1)}% of SL distance (cap ${(cap * 100).toFixed(0)}%)`
        : 'Spread too wide for SL';
    }
    const freshness = (signal as { dataFreshness?: { reason?: string } }).dataFreshness;
    if (freshness?.reason) return 'Stale data';
    const readiness = String(
      (signal as { entryReadiness?: string }).entryReadiness
      || (signal.naked_data as { entryReadiness?: string } | undefined)?.entryReadiness
      || '',
    ).toUpperCase();
    if (readiness && readiness !== 'READY') {
      const detail = String(
        (signal as { entryReadinessReason?: string }).entryReadinessReason
        || (signal.naked_data as { entryReadinessReason?: string } | undefined)?.entryReadinessReason
        || readiness,
      );
      return `Entry timing: ${detail}`;
    }
    return 'Not executable';
  }
  const canonicalOk = engineBCanonicalTradeOk(signal);
  if (canonicalOk === false) {
    const naked = engineBNakedPayload(signal);
    const gates = naked ? readEngineBCanonicalGatesFromNaked(naked) : null;
    const raw = signal as Record<string, unknown>;
    const status = gates?.canonicalStatus
      ?? (typeof raw.engine_b_canonical_status === 'string' ? raw.engine_b_canonical_status : null);
    if (status) return `NO ENTRY · ${status}`;
    if (gates?.confidencePassed && !gates.canonicalTradeOk) return 'NO ENTRY';
    return 'NO ENTRY';
  }
  const { entry, sl } = resolveEngineBExecutionPreviewLevels(signal, { engineBOnly: true });
  const tp = engineBExecutableTp(signal);
  if (!isPositiveNumber(entry) || !isPositiveNumber(sl) || !isPositiveNumber(tp)) {
    return 'Missing SL/TP';
  }
  return null;
}

export interface AiLevelOverride {
  sl: number;
  tp1: number;
  tp2?: number;
  source: 'marcus_ai';
}

export function parseAiLevelString(value: string | null | undefined): number | null {
  if (value == null) return null;
  const trimmed = String(value).trim();
  if (!trimmed) return null;
  const match = trimmed.match(/-?\d[\d,]*(?:\.\d+)?(?:e[-+]?\d+)?/i);
  if (!match) return null;
  const parsed = Number.parseFloat(match[0].replace(/,/g, ''));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function aiLevelOverrideFromReview(
  review: AiTextReviewResponse | null | undefined,
  opts?: { fallbackTp1?: number | null },
): AiLevelOverride | null {
  if (!review) return null;
  const verdict = String(review.levelsVerdict || '').trim().toLowerCase();
  const fallbackTp1 = typeof opts?.fallbackTp1 === 'number' && opts.fallbackTp1 > 0
    ? opts.fallbackTp1
    : null;

  if (verdict === 'adjust' || verdict === 'reject') {
    const sl = parseAiLevelString(review.suggestedSL);
    const tp1 = parseAiLevelString(review.suggestedTP);
    if (sl == null || tp1 == null) return null;
    return { sl, tp1, tp2: tp1, source: 'marcus_ai' };
  }

  if (verdict === 'accept') {
    const sl = parseAiLevelString(review.invalidation);
    const tp1 = parseAiLevelString(review.suggestedTP) ?? fallbackTp1;
    if (sl == null || tp1 == null) return null;
    return { sl, tp1, tp2: tp1, source: 'marcus_ai' };
  }

  return null;
}

export function computeLevelOverrideRR(
  entry: number | null | undefined,
  sl: number | null | undefined,
  tp1: number | null | undefined,
): number | null {
  if (entry == null || sl == null || tp1 == null) return null;
  if (!Number.isFinite(entry) || !Number.isFinite(sl) || !Number.isFinite(tp1)) return null;
  if (entry <= 0 || sl <= 0 || tp1 <= 0) return null;
  const risk = Math.abs(entry - sl);
  const reward = Math.abs(tp1 - entry);
  if (risk <= 0) return null;
  return reward / risk;
}

export function buildQuickExecutePayload(args: {
  signal: EngineASignal;
  engineBOverlay?: Record<string, unknown>;
  isEngineBOnly?: boolean;
  pipMode: string;
  volumeMode?: ExecutionVolumeMode;
  sizingOverride?: number;
  exitMode?: ExitModeSelection;
  reviewId?: string | null;
  levelOverride?: AiLevelOverride | null;
}): Record<string, unknown> {
  const {
    signal,
    engineBOverlay,
    isEngineBOnly,
    pipMode,
    volumeMode,
    sizingOverride,
    exitMode,
    reviewId,
    levelOverride,
  } = args;
  const resolved = isEngineBOnly ? signal : resolveEngineAV3Signal(signal);
  const signalPayload = isEngineBOnly ? resolved : stripEngineBFromSignal(resolved);
  const nakedData = isEngineBOnly
    ? (resolved.naked_data ?? resolved.engine_b ?? {})
    : {};
  const effectiveStyle = isEngineAV3Signal(resolved)
    ? String(resolved.horizon || resolved.style || pipMode || 'swing')
    : (pipMode || resolved.style || 'swing');
  const volumePayload = buildExecutionVolumePayload({ volumeMode, sizingOverride });
  // Per-trade exit-mode override is Engine-A only (backend no-ops it for engine_b).
  const exitModePayload = isEngineBOnly ? {} : buildExitModePayload({ exitMode });
  const payload: Record<string, unknown> = {
    signal: {
      ...signalPayload,
      engine: isEngineBOnly ? 'engine_b' : resolved.engine,
      symbol: resolved.symbol || resolved.pair || resolved.display,
      pair: resolved.pair || resolved.display,
      display: resolved.display || resolved.pair,
      type: resolved.type,
      direction: resolved.direction,
      price: resolved.entry ?? resolved.price,
      entry: resolved.entry ?? resolved.price,
      sl: resolved.sl,
      tp1: resolved.tp1 ?? resolved.tp,
      tp2: resolved.tp2 ?? resolved.tp,
      style: effectiveStyle,
      horizon: resolved.horizon ?? effectiveStyle,
      source: isEngineBOnly ? 'engine_b' : 'engine_a',
      ...exitModePayload,
    },
    engine_b: (engineBOverlay ?? nakedData) as Record<string, unknown>,
    pip_mode: effectiveStyle,
    ...volumePayload,
  };
  if (reviewId) {
    payload.review_id = reviewId;
  }
  if (levelOverride && !isEngineAV3Signal(resolved)) {
    payload.level_override = {
      sl: levelOverride.sl,
      tp1: levelOverride.tp1,
      tp2: levelOverride.tp2 ?? levelOverride.tp1,
      source: levelOverride.source,
    };
  }
  return payload;
}

function isPositiveNumber(value: unknown): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

const _HARD_BLOCK_DECISIONS = new Set(['NO_TRADE', 'INVALIDATED']);

export function tradeSkillBlocksExecute(
  aiReview: { ai_review?: { decision?: string; entryAllowedNow?: boolean } | null } | null,
): boolean {
  if (!aiReview?.ai_review) return false;
  const decision = String(aiReview.ai_review.decision || '').toUpperCase();
  return _HARD_BLOCK_DECISIONS.has(decision);
}

function aiHumanAction(review: AIChartReviewResponse | null): string {
  if (!review) return '';
  const summary = review.aiReviewSummary ?? review.ai_review_summary;
  const fromSummary = summary && typeof summary === 'object'
    ? String((summary as { humanAction?: string }).humanAction || '')
    : '';
  const fromReview = String(review.ai_review?.human_action || '');
  return (fromSummary || fromReview).toLowerCase();
}

export function aiReviewEntryTimingRejected(review: AIChartReviewResponse | null): boolean {
  if (!review) return false;
  const comparison = review.engineAVerdictComparison ?? review.engine_a_verdict_comparison;
  const comparisonVerdict = String(comparison?.comparisonVerdict || '').trim();
  if (comparisonVerdict === 'engine_a_direction_confirmed_entry_rejected') return true;
  if (comparison?.chartContradictsEntryTiming === true) return true;
  const entryQuality = String(review.ai_review?.entry_quality || '').toLowerCase();
  return entryQuality.includes('poor') || entryQuality.includes('extended') || entryQuality.includes('late');
}

export function aiReviewBlocksManualExecute(review: AIChartReviewResponse | null): boolean {
  if (!review) return false;
  if (tradeSkillBlocksExecute(review)) return true;
  const verdict = String(review.ai_review?.verdict || '').toUpperCase();
  if (verdict === 'NO_TRADE' || verdict === 'INVALID') return true;
  const action = aiHumanAction(review);
  if (action === 'reject') return true;
  return false;
}

export function aiReviewWarningForExecute(review: AIChartReviewResponse | null): string | null {
  if (!review) return null;
  const action = aiHumanAction(review);
  if (['wait', 'needs_fresher_data', 'needs_better_rr', 'watch'].includes(action)) {
    return `AI suggests: ${action.replace(/_/g, ' ')}`;
  }
  if (aiReviewEntryTimingRejected(review)) return 'AI: entry timing concern';
  const decision = String(review.ai_review?.decision || '').toUpperCase();
  if (decision === 'WAIT_FOR_PULLBACK' || decision === 'WAIT_FOR_ACCEPTANCE' || decision === 'WATCH_ONLY') {
    return `AI decision: ${decision.replace(/_/g, ' ').toLowerCase()}`;
  }
  const finalDecision = String(
    (review.engineAVerdictComparison ?? review.engine_a_verdict_comparison)?.finalDecision || '',
  ).toLowerCase();
  if (finalDecision === 'watch') return 'AI: watch only';
  return null;
}

export function isEngineBOnlySignal(signal: EngineASignal | null): boolean {
  if (!signal) return false;
  const raw = signal as Record<string, unknown>;
  const engine = String(
    raw.engine_source ?? raw.source_engine ?? raw.engine ?? '',
  ).toUpperCase();
  return (
    engine === 'B'
    || engine === 'ENGINE_B'
    || engine === 'NAKED'
    || raw.is_naked === true
    || Boolean(raw.naked_data)
  );
}

function reviewContextFromAiReview(review: AIChartReviewResponse | null): {
  symbol: string | null;
  timeframe: string | null;
  primaryEngine: 'A' | 'B';
  primaryPassed: boolean | null;
} {
  const primaryEngine = String(review?.primaryEngine || 'A').toUpperCase() === 'B' ? 'B' : 'A';
  const ctx = primaryEngine === 'B'
    ? (review?.engine_b_context ?? review?.engineBContext)
    : review?.engine_a_context;
  const record = ctx && typeof ctx === 'object' ? (ctx as Record<string, unknown>) : {};
  return {
    primaryEngine,
    symbol: typeof record.symbol === 'string' ? record.symbol : null,
    timeframe: typeof record.timeframe === 'string' ? record.timeframe : null,
    primaryPassed: typeof record.passed === 'boolean'
      ? record.passed
      : null,
  };
}

export function canExecuteEngineASignalTier(signal: EngineASignal | null): boolean {
  if (!signal) return false;
  const tier = String(
    signal.signalTier || signal.scan_tier || signal.signalClass || '',
  ).toLowerCase();
  if (tier.includes('watch') || tier === 'skip' || tier === 'blocked') return false;
  if (isEngineAV3Signal(signal)) {
    const resolved = resolveEngineAV3Signal(signal);
    return tier === 'trade'
      && resolved.decision === 'TRADE'
      && resolved.qualified === true
      && resolved.engineATradeEnabled === true;
  }
  if (signal.engineATradeEnabled === false) return false;
  return tier === 'trade' || tier === 'criteria' || signal.trade === true;
}

/** TV Chart execute gate. Engine B overlay staleness is advisory-only (see TVChartPanel). */
export function evaluateTvChartExecuteBlock(args: {
  signal: EngineASignal | null;
  chartSymbolKey: string | null;
  chartTimeframe: string | null;
  aiReview: AIChartReviewResponse | null;
  suggestedTradePlan?: SuggestedTradePlan | null;
  isTestMode: boolean;
  isPaper?: boolean;
}): string | null {
  const { signal, chartSymbolKey, chartTimeframe, aiReview, isTestMode, isPaper } = args;
  if (isTestMode) return 'Test mode';
  if (!signal) return 'No selected signal';
  const signalKey = normalizeSymbolKey(signal.symbol || signal.pair || signal.display);
  if (chartSymbolKey && signalKey && chartSymbolKey !== signalKey) return 'Symbol mismatch';
  const direction = String(signal.direction || '').toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') return 'No selected signal';

  const isEngineBOnly = isEngineBOnlySignal(signal);
  if (isEngineBOnly) {
    const blockReason = engineBExecuteBlockReason(signal);
    if (blockReason) return blockReason;
  } else {
    const entry = signal.entry ?? signal.price;
    const tp = signal.tp1 ?? signal.tp;
    if (!isPositiveNumber(entry) || !isPositiveNumber(signal.sl) || !isPositiveNumber(tp)) {
      return 'Missing SL/TP';
    }
    if (signal.engineATradeEnabled === false) return 'Research-only';
    if (!canExecuteEngineASignalTier(signal)) return 'Watchlist only';
  }

  if (isPaper) return 'Paper mode';
  const reviewCtx = reviewContextFromAiReview(aiReview);
  const reviewSymbolKey = normalizeSymbolKey(reviewCtx.symbol);
  if (aiReview && reviewSymbolKey && chartSymbolKey && reviewSymbolKey !== chartSymbolKey) {
    return 'Review not current (symbol mismatch)';
  }
  const reviewTimeframe = normalizeBackendTf(reviewCtx.timeframe);
  const currentTf = normalizeBackendTf(chartTimeframe);
  if (aiReview && reviewTimeframe && currentTf && reviewTimeframe !== currentTf) {
    return 'Review not current (timeframe mismatch)';
  }
  if (isEngineBOnly && reviewCtx.primaryEngine === 'B' && reviewCtx.primaryPassed === false) {
    return 'Engine B no longer confirmed after AI review';
  }
  if (!isEngineBOnly && reviewCtx.primaryEngine === 'A' && reviewCtx.primaryPassed === false) {
    return 'Engine A no longer confirmed after AI review';
  }
  if (aiReviewBlocksManualExecute(aiReview)) return 'AI review: no trade';
  return null;
}

export function shouldHideTvChartExecuteNow(args: {
  aiReview: AIChartReviewResponse | null;
  canFlagWatch: boolean;
  suggestedAction?: string;
}): boolean {
  if (args.canFlagWatch && args.suggestedAction !== 'WATCH_ONLY') return true;
  if (aiReviewEntryTimingRejected(args.aiReview)) return true;
  if (aiReviewBlocksManualExecute(args.aiReview)) return true;
  return false;
}

export function isScalpAiReviewEligible(signal: ScalpExecuteSignalLike | null): boolean {
  if (!signal) return false;
  const direction = String(signal.direction || '').toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') return false;
  const grade = String(signal.ai_grade || '').toUpperCase();
  // Grade D is never review-eligible (BLOCKED / context-only).
  if (grade === 'D' || grade === '') return false;
  // Config-driven floor from the backend (SCALP_AI_REVIEW_MIN_GRADE, default "C") so borderline
  // setups can be sent to advisory AI review. Execution stays gated separately (EXECUTION_MIN_GRADE
  // via evaluateScalpExecuteBlock). Falls back to legacy A/B when the field is absent.
  const rank: Record<string, number> = { A: 4, B: 3, C: 2, D: 1 };
  const minGrade = String(signal.ai_review_min_grade || 'B').toUpperCase();
  const floor = rank[minGrade] ?? rank.B;
  return (rank[grade] ?? 0) >= floor;
}

export function requiresScalpAiEntryNow(review: ScalpAIChartReviewResponse | null): boolean {
  if (!review) return true;
  const structured = review.ai_review?.structured as Record<string, unknown> | undefined;
  const decision = String(review.ai_review?.decision || structured?.decision || '').toUpperCase();
  const entryAllowed = review.ai_review && 'entryAllowedNow' in review.ai_review
    ? review.ai_review.entryAllowedNow
    : structured?.entryAllowedNow;
  if (decision !== 'ENTRY_NOW') return true;
  if (entryAllowed !== true) return true;
  return false;
}

export function scalpAiReviewBlocksExecute(review: ScalpAIChartReviewResponse | null): boolean {
  if (!review) return true;
  if (tradeSkillBlocksExecute(review)) return true;
  const comparison = review.scalpVerdictComparison ?? review.scalp_verdict_comparison;
  const finalDecision = String(comparison?.finalDecision || '').toLowerCase();
  if (finalDecision === 'reject' || finalDecision === 'watch') return true;
  if (comparison?.chartContradictsEntryTiming === true) return true;
  const verdict = String(review.ai_review?.verdict || '').toUpperCase();
  if (verdict === 'NO_TRADE' || verdict === 'INVALID') return true;
  const summary = review.aiReviewSummary ?? review.ai_review_summary;
  const action = String(
    (summary && typeof summary === 'object' ? (summary as { humanAction?: string }).humanAction : '')
    || review.ai_review?.human_action
    || '',
  ).toLowerCase();
  return ['wait', 'reject', 'watch', 'needs_fresher_data', 'needs_better_rr'].includes(action);
}

export function scalpSourceFidelityHardFail(signal: ScalpExecuteSignalLike | null): boolean {
  if (!signal) return true;
  const pillars = signal.strict_fabio_pillars;
  if (pillars && typeof pillars === 'object') {
    if (pillars.orderflow === false || pillars.volume === false) return true;
  }
  const strictOrderflow = signal.strictOrderflowSourcePass;
  if (strictOrderflow === false) return true;
  const fidelity = signal.data_fidelity;
  const proxyFlags = [
    signal.vp_is_proxy,
    signal.cvd_is_proxy,
    signal.absorption_is_proxy,
    fidelity?.vp_is_proxy,
    fidelity?.cvd_is_proxy,
    fidelity?.absorption_is_proxy,
  ];
  const realOrderflow = signal.aggression_uses_real_order_flow ?? fidelity?.aggression_uses_real_order_flow;
  if (realOrderflow === false && proxyFlags.some((v) => v === true)) return true;
  return false;
}

function scalpSignalGrade(signal: ScalpExecuteSignalLike): string {
  return String(signal.ai_grade || signal.grade || '').toUpperCase();
}

function scalpMechanicalGateBlock(signal: ScalpExecuteSignalLike): string | null {
  const gate = String(signal.gate_result || '').toUpperCase();
  if (gate === 'BLOCKED') return 'Blocked';
  if (signal.executable === false) return 'Not executable';
  if (gate && gate !== 'PASS') return 'Gate failed';
  if ('strict_fabio_pass' in signal && signal.strict_fabio_pass !== true) return 'Fabio gate failed';
  if (scalpSourceFidelityHardFail(signal)) return 'Source fidelity failed';
  return null;
}

export function evaluateScalpExecuteBlock(args: {
  signal: ScalpExecuteSignalLike | null;
  chartSymbolKey: string | null;
  chartTimeframe: string | null;
  aiReview: ScalpAIChartReviewResponse | null;
  suggestedTradePlan?: SuggestedTradePlan | null;
  isTestMode?: boolean;
  isPaper?: boolean;
  executionMode?: ScalpExecutionMode;
  requireAiReview?: boolean;
}): string | null {
  const {
    signal,
    chartSymbolKey,
    chartTimeframe,
    aiReview,
    suggestedTradePlan,
    isTestMode,
    isPaper,
    executionMode,
    requireAiReview,
  } = args;
  if (isTestMode) return 'Test mode';
  const mode = executionMode || (isPaper ? 'paper' : undefined);
  if (mode === 'live' || mode === 'live_disabled') return 'Live execution disabled';
  if (!signal) return 'No scalp candidate';
  const direction = String(signal.direction || '').toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') return 'Direction missing';
  const grade = scalpSignalGrade(signal);
  if (grade === 'D') return 'Grade D not executable';
  if (grade !== 'A' && grade !== 'B') return 'Grade below B';
  const needsAiReview = requireAiReview !== false;
  if (needsAiReview && !aiReview?.review_id && !aiReview?.ai_review) {
    return 'AI review required';
  }
  const reviewSymbolKey = aiReview?.engine_d_context?.symbol ? normalizeSymbolKey(aiReview.engine_d_context.symbol) : null;
  if (aiReview && reviewSymbolKey && chartSymbolKey && reviewSymbolKey !== chartSymbolKey) {
    return 'Review not current (symbol mismatch)';
  }
  const reviewTimeframe = aiReview?.engine_d_context?.timeframe;
  if (aiReview && reviewTimeframe && chartTimeframe && reviewTimeframe !== chartTimeframe) {
    return 'Review not current (timeframe mismatch)';
  }
  const entry = signal.entry ?? signal.price;
  if (!isPositiveNumber(entry) || !isPositiveNumber(signal.sl) || !isPositiveNumber(signal.tp1 ?? signal.tp)) {
    return 'Missing levels';
  }
  if (grade === 'A' || grade === 'B') {
    const mechanical = scalpMechanicalGateBlock(signal);
    if (mechanical === 'Blocked') return mechanical;
    if (needsAiReview) {
      if (requiresScalpAiEntryNow(aiReview)) return 'AI ENTRY_NOW required';
      if (scalpAiReviewBlocksExecute(aiReview)) return 'AI says wait';
    }
  }
  const planAction = String(suggestedTradePlan?.action || '').toUpperCase();
  if (planAction === 'WAIT_FOR_LEVEL') return 'Waiting for level';
  if (planAction === 'WAIT_FOR_ZONE') return 'Waiting for zone';
  return null;
}

export function buildScalpExecutePayload(args: {
  symbol: string;
  signal: ScalpExecuteSignalLike;
  volumeMode?: ExecutionVolumeMode;
  sizingOverride?: number;
  reviewId?: string | null;
  executionMode?: ScalpExecutionMode;
  requireAiReview?: boolean;
}): Record<string, unknown> {
  const volumePayload = buildExecutionVolumePayload({
    volumeMode: args.volumeMode,
    sizingOverride: args.sizingOverride,
  });
  const payload: Record<string, unknown> = {
    symbol: args.symbol,
    signal: args.signal,
    ...volumePayload,
  };
  if (args.reviewId) {
    payload.review_id = args.reviewId;
  }
  if (args.executionMode) {
    payload.execution_mode = args.executionMode;
  }
  if (typeof args.requireAiReview === 'boolean') {
    payload.require_ai_review = args.requireAiReview;
  }
  return payload;
}
