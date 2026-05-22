import type {
  AIChartReviewResponse,
  EngineASignal,
  ScalpAIChartReviewResponse,
} from '@/types/athena';

export type QuickExecuteStyle = 'scalp' | 'intraday' | 'swing' | 'auto';

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

export function buildQuickExecutePayload(args: {
  signal: EngineASignal;
  engineBOverlay?: Record<string, unknown>;
  isEngineBOnly?: boolean;
  pipMode: string;
  sizingOverride: number;
}): Record<string, unknown> {
  const { signal, engineBOverlay, isEngineBOnly, pipMode, sizingOverride } = args;
  const signalPayload = isEngineBOnly ? signal : stripEngineBFromSignal(signal);
  const nakedData = isEngineBOnly
    ? (signal.naked_data ?? signal.engine_b ?? {})
    : {};
  const effectiveStyle = pipMode || signal.style || 'swing';
  return {
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
    },
    engine_b: (engineBOverlay ?? nakedData) as Record<string, unknown>,
    pip_mode: effectiveStyle,
    sizing_override: Math.max(0.25, Math.min(1.0, sizingOverride || 1.0)),
  };
}

function isPositiveNumber(value: unknown): boolean {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
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
  if (comparison?.chartContradictsEntryTiming === true) return true;
  const entryQuality = String(review.ai_review?.entry_quality || '').toLowerCase();
  return entryQuality.includes('poor') || entryQuality.includes('extended') || entryQuality.includes('late');
}

export function aiReviewBlocksManualExecute(review: AIChartReviewResponse | null): boolean {
  if (!review) return false;
  const verdict = String(review.ai_review?.verdict || '').toUpperCase();
  if (verdict === 'NO_TRADE' || verdict === 'INVALID') return true;
  const action = aiHumanAction(review);
  if (['wait', 'reject', 'needs_fresher_data', 'needs_better_rr', 'watch'].includes(action)) return true;
  if (aiReviewEntryTimingRejected(review)) return true;
  const finalDecision = String(
    (review.engineAVerdictComparison ?? review.engine_a_verdict_comparison)?.finalDecision || '',
  ).toLowerCase();
  return finalDecision === 'reject' || finalDecision === 'watch';
}

export function canExecuteEngineASignalTier(signal: EngineASignal | null): boolean {
  if (!signal) return false;
  const tier = String(
    signal.signalTier || signal.scan_tier || signal.signalClass || '',
  ).toLowerCase();
  if (tier.includes('watch') || tier === 'skip' || tier === 'blocked') return false;
  return tier === 'trade' || tier === 'criteria' || signal.trade === true;
}

export function evaluateTvChartExecuteBlock(args: {
  signal: EngineASignal | null;
  chartSymbolKey: string | null;
  aiReview: AIChartReviewResponse | null;
  isTestMode: boolean;
  isPaper?: boolean;
}): string | null {
  const { signal, chartSymbolKey, aiReview, isTestMode, isPaper } = args;
  if (isTestMode) return 'Test mode';
  if (!signal) return 'No selected signal';
  const signalKey = normalizeSymbolKey(signal.symbol || signal.pair || signal.display);
  if (chartSymbolKey && signalKey && chartSymbolKey !== signalKey) return 'No selected signal';
  const direction = String(signal.direction || '').toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') return 'No selected signal';
  const entry = signal.entry ?? signal.price;
  const tp = signal.tp1 ?? signal.tp;
  if (!isPositiveNumber(entry) || !isPositiveNumber(signal.sl) || !isPositiveNumber(tp)) {
    return 'Missing SL/TP';
  }
  if (!canExecuteEngineASignalTier(signal)) return 'Watchlist only';
  if (isPaper) return 'Paper mode';
  const reviewSymbolKey = normalizeSymbolKey(aiReview?.engine_a_context?.symbol);
  if (aiReview && reviewSymbolKey && chartSymbolKey && reviewSymbolKey !== chartSymbolKey) {
    return 'Review not current';
  }
  if (aiReviewBlocksManualExecute(aiReview)) return 'AI says wait';
  return null;
}

export function shouldHideTvChartExecuteNow(args: {
  aiReview: AIChartReviewResponse | null;
  canFlagWatch: boolean;
}): boolean {
  if (args.canFlagWatch) return true;
  if (aiReviewEntryTimingRejected(args.aiReview)) return true;
  if (aiReviewBlocksManualExecute(args.aiReview)) return true;
  return false;
}

export function scalpAiReviewBlocksExecute(review: ScalpAIChartReviewResponse | null): boolean {
  if (!review) return false;
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

export function evaluateScalpExecuteBlock(args: {
  signal: ScalpExecuteSignalLike | null;
  aiReview: ScalpAIChartReviewResponse | null;
  isTestMode?: boolean;
}): string | null {
  const { signal, aiReview, isTestMode } = args;
  if (isTestMode) return 'Test mode';
  if (!signal) return 'No scalp candidate';
  const direction = String(signal.direction || '').toUpperCase();
  if (direction !== 'LONG' && direction !== 'SHORT') return 'Direction missing';
  const gate = String(signal.gate_result || '').toUpperCase();
  if (signal.executable === false) return 'Not executable';
  if (gate && gate !== 'PASS') return 'Gate failed';
  if ('strict_fabio_pass' in signal && signal.strict_fabio_pass !== true) return 'Fabio gate failed';
  if (scalpSourceFidelityHardFail(signal)) return 'Source fidelity failed';
  const entry = signal.entry ?? signal.price;
  if (!isPositiveNumber(entry) || !isPositiveNumber(signal.sl) || !isPositiveNumber(signal.tp1 ?? signal.tp)) {
    return 'Missing levels';
  }
  if (scalpAiReviewBlocksExecute(aiReview)) return 'AI says wait';
  return null;
}

export function buildScalpExecutePayload(args: {
  symbol: string;
  signal: ScalpExecuteSignalLike;
  sizingOverride: number;
}): Record<string, unknown> {
  return {
    symbol: args.symbol,
    signal: args.signal,
    sizing_override: Math.max(0.25, Math.min(1.0, args.sizingOverride || 1.0)),
  };
}
