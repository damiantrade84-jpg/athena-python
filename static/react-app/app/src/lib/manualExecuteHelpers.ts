import type {
  AIChartReviewResponse,
  EngineASignal,
  ScalpAIChartReviewResponse,
  SuggestedTradePlan,
} from '@/types/athena';

export type QuickExecuteStyle = 'scalp' | 'intraday' | 'swing' | 'auto';
export type ExecutionVolumeMode = 'min_lot' | 'calculated';

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

export function buildQuickExecutePayload(args: {
  signal: EngineASignal;
  engineBOverlay?: Record<string, unknown>;
  isEngineBOnly?: boolean;
  pipMode: string;
  volumeMode?: ExecutionVolumeMode;
  sizingOverride?: number;
  exitMode?: ExitModeSelection;
  reviewId?: string | null;
}): Record<string, unknown> {
  const { signal, engineBOverlay, isEngineBOnly, pipMode, volumeMode, sizingOverride, exitMode, reviewId } = args;
  const signalPayload = isEngineBOnly ? signal : stripEngineBFromSignal(signal);
  const nakedData = isEngineBOnly
    ? (signal.naked_data ?? signal.engine_b ?? {})
    : {};
  const effectiveStyle = pipMode || signal.style || 'swing';
  const volumePayload = buildExecutionVolumePayload({ volumeMode, sizingOverride });
  // Per-trade exit-mode override is Engine-A only (backend no-ops it for engine_b).
  const exitModePayload = isEngineBOnly ? {} : buildExitModePayload({ exitMode });
  const payload: Record<string, unknown> = {
    signal: {
      ...signalPayload,
      symbol: signal.symbol || signal.pair || signal.display,
      pair: signal.pair || signal.display,
      display: signal.display || signal.pair,
      type: signal.type,
      direction: signal.direction,
      price: signal.entry ?? signal.price,
      entry: signal.entry ?? signal.price,
      sl: signal.sl,
      tp1: signal.tp1 ?? signal.tp,
      tp2: signal.tp2 ?? signal.tp,
      style: effectiveStyle,
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

export function canExecuteEngineASignalTier(signal: EngineASignal | null): boolean {
  if (!signal) return false;
  if (signal.engineATradeEnabled === false) return false;
  const tier = String(
    signal.signalTier || signal.scan_tier || signal.signalClass || '',
  ).toLowerCase();
  if (tier.includes('watch') || tier === 'skip' || tier === 'blocked') return false;
  return tier === 'trade' || tier === 'criteria' || signal.trade === true;
}

/** Engine A execute gate. Engine B overlay staleness is advisory-only (see TVChartPanel). */
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
  const entry = signal.entry ?? signal.price;
  const tp = signal.tp1 ?? signal.tp;
  if (!isPositiveNumber(entry) || !isPositiveNumber(signal.sl) || !isPositiveNumber(tp)) {
    return 'Missing SL/TP';
  }
  if (signal.engineATradeEnabled === false) return 'Research-only';
  if (!canExecuteEngineASignalTier(signal)) return 'Watchlist only';
  if (isPaper) return 'Paper mode';
  const reviewSymbolKey = normalizeSymbolKey(aiReview?.engine_a_context?.symbol);
  if (aiReview && reviewSymbolKey && chartSymbolKey && reviewSymbolKey !== chartSymbolKey) {
    return 'Review not current (symbol mismatch)';
  }
  const reviewTimeframe = aiReview?.engine_a_context?.timeframe;
  if (aiReview && reviewTimeframe && chartTimeframe && reviewTimeframe !== chartTimeframe) {
    return 'Review not current (timeframe mismatch)';
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
  return grade === 'A' || grade === 'B';
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
}): string | null {
  const { signal, chartSymbolKey, chartTimeframe, aiReview, suggestedTradePlan, isTestMode, isPaper } = args;
  if (isTestMode) return 'Test mode';
  if (isPaper) return 'Paper mode';
  if (!signal) return 'No scalp candidate';
  const direction = String(signal.direction || '').toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') return 'Direction missing';
  const grade = scalpSignalGrade(signal);
  if (grade === 'D') return 'Grade D not executable';
  if ((grade === 'A' || grade === 'B') && !aiReview?.review_id && !aiReview?.ai_review) {
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
    if (requiresScalpAiEntryNow(aiReview)) return 'AI ENTRY_NOW required';
    if (scalpAiReviewBlocksExecute(aiReview)) return 'AI says wait';
  } else {
    const mechanical = scalpMechanicalGateBlock(signal);
    if (mechanical) return mechanical;
    if (requiresScalpAiEntryNow(aiReview)) return 'AI ENTRY_NOW required';
    if (scalpAiReviewBlocksExecute(aiReview)) return 'AI says wait';
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
  return payload;
}
