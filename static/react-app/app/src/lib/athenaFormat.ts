// Display helpers for Athena payloads. Keep these tiny — heavy logic belongs
// inside the renderers, not in formatters.

import { fmtNum, toNum } from './utils';
import type { EngineASignal, EngineBNakedResult } from '@/types/athena';
import { isEngineAV3Signal } from './engineAV3';

/** Choose decimals based on pair type (forex/JPY-pair-aware, crypto-aware). */
export function priceDecimals(pair: string | undefined, type: string | undefined): number {
  const p = (pair || '').toUpperCase();
  if (type === 'crypto') {
    // Low-value coins need higher precision
    if (p.includes('DOGE') || p.includes('SHIB') || p.includes('PEPE') || p.includes('FLOKI') || p.includes('BONK')) return 6;
    if (p.includes('TRX') || p.includes('XRP') || p.includes('ADA') || p.includes('DOT')) return 4;
    return 2;
  }
  if (type === 'stock' || type === 'etf' || type === 'index') return 2;
  if (type === 'commodity' && p.includes('XAU')) return 2;
  if (type === 'commodity' && p.includes('XAG')) return 3;
  if (p.includes('JPY')) return 3;
  return 5;
}

export function fmtPrice(value: unknown, pair?: string, type?: string): string {
  return fmtNum(value, priceDecimals(pair, type));
}

export function fmtPct(value: unknown, decimals = 1, fallback = '—'): string {
  const n = toNum(value, NaN);
  if (!Number.isFinite(n)) return fallback;
  return `${n.toFixed(decimals)}%`;
}

export function fmtScore(score: unknown, max: unknown, decimals = 2): string {
  return `${fmtNum(score, decimals)} / ${fmtNum(max, decimals)}`;
}

export function fmtLiveQuoteAge(ageSec: unknown): string {
  const n = toNum(ageSec, NaN);
  if (!Number.isFinite(n) || n < 0) return 'age n/a';
  if (n < 1) return '<1s ago';
  if (n < 60) return `${Math.round(n)}s ago`;
  if (n < 3600) return `${Math.round(n / 60)}m ago`;
  const hours = n / 3600;
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours).toString()}h ago`;
}

export function fmtLiveQuoteMeta(ageSec: unknown, source?: unknown): string {
  const src = typeof source === 'string' && source.trim() ? source.trim() : '';
  return src ? `${fmtLiveQuoteAge(ageSec)} / ${src}` : fmtLiveQuoteAge(ageSec);
}

/** Render the meta line for the SignalsPanel ATR row.
 *
 *  Shows the timeframe, ATR age and (when CONFIG.ATR_FRESHNESS.ENABLED is true
 *  on the backend) a "stale" flag. Observability only — the operator can rely
 *  on this to triage suspected ATR plumbing issues even though the backend
 *  itself never gates execution on this unless BLOCK_EXECUTION_ON_STALE_ATR is
 *  flipped in config.
 */
export function fmtAtrMeta(
  diagnostics?: EngineASignal['atrDiagnostics'],
  freshness?: EngineASignal['atrFreshness'],
): string | undefined {
  if (!diagnostics && !freshness) return undefined;
  const tf = diagnostics?.atr_tf ? String(diagnostics.atr_tf).toUpperCase() : '';
  const source = diagnostics?.atr_source ? String(diagnostics.atr_source) : '';
  const ageNum = toNum(diagnostics?.atr_age_seconds, NaN);
  const ageBit = Number.isFinite(ageNum)
    ? Math.round(ageNum) < 60
      ? `${Math.max(0, Math.round(ageNum))}s`
      : Math.round(ageNum) < 3600
        ? `${Math.round(ageNum / 60)}m`
        : `${(ageNum / 3600).toFixed(1)}h`
    : '';
  const parts: string[] = [];
  if (tf) parts.push(tf);
  if (ageBit) parts.push(ageBit);
  if (source) parts.push(source);
  let meta = parts.join(' · ');
  if (freshness?.enabled && freshness?.stale) {
    meta = meta ? `${meta} · STALE` : 'STALE';
  }
  return meta || undefined;
}

/** Resolve Engine A scan threshold from API payload (field names differ by route). */
export function engineAThreshold(sig: EngineASignal | null | undefined): number | null {
  if (!sig) return null;
  const raw = sig as Record<string, unknown>;
  const candidates = [
    sig.confluenceThreshold,
    raw.engine_a_confluenceThreshold,
    sig.threshold,
    raw.engine_a_threshold,
    raw.liveThreshold,
    raw.scanThresholdEffective,
    raw.scanThreshold,
    raw.scanThresholdStatic,
  ];
  for (const candidate of candidates) {
    const n = toNum(candidate, NaN);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

export interface EngineAScoreBreakdown {
  displayScore: number | null;
  decisionScore: number | null;
  threshold: number | null;
  maxScore: number | null;
  intermarketDelta: number;
  newsAdjustment: number;
  hasAdjustments: boolean;
  decisionPasses: boolean;
  displayPasses: boolean;
}

function readSignalNumber(sig: Record<string, unknown>, ...keys: string[]): number {
  for (const key of keys) {
    const n = toNum(sig[key], NaN);
    if (Number.isFinite(n)) return n;
  }
  return NaN;
}

/** Split display confluence from the pre-blend score used for V3 decision (not legacy tier). */
export function engineAScoreBreakdown(sig: EngineASignal | null | undefined): EngineAScoreBreakdown | null {
  if (!sig) return null;
  const raw = sig as Record<string, unknown>;
  const isV3 = isEngineAV3Signal(sig);
  const isBOnlyStub = String(raw.engine_source ?? raw.engine ?? '').toUpperCase() === 'ENGINE_B'
    || (String(raw.engine_source ?? raw.engine ?? '').toUpperCase() === 'B' && raw.engine_a_present === false);
  const displayScore = readSignalNumber(
    raw,
    ...(isBOnlyStub ? ['engine_a_confluenceScore'] as const : []),
    'confluenceScore',
    'score',
    ...(isBOnlyStub ? [] as const : ['engine_a_confluenceScore'] as const),
  );
  if (!Number.isFinite(displayScore)) return null;

  const intermarket = (raw.intermarketConfirmation && typeof raw.intermarketConfirmation === 'object'
    ? raw.intermarketConfirmation
    : {}) as Record<string, unknown>;
  const intermarketDelta = readSignalNumber(
    raw,
    'intermarketEngineADelta',
    'engine_a_intermarketEngineADelta',
  );
  const intermarketDeltaResolved = Number.isFinite(intermarketDelta)
    ? intermarketDelta
    : readSignalNumber(intermarket, 'engineADelta');

  const newsAdjustment = readSignalNumber(
    raw,
    'news_adjustment',
    'newsSentimentDelta',
    'engine_a_news_adjustment',
  );
  const newsAdjustmentResolved = Number.isFinite(newsAdjustment) ? newsAdjustment : 0;

  const preNews = readSignalNumber(raw, 'pre_news_score', 'engine_a_pre_news_score');
  let decisionScore = displayScore;
  if (isV3 && Number.isFinite(preNews)) {
    decisionScore = preNews - (Number.isFinite(intermarketDeltaResolved) ? intermarketDeltaResolved : 0);
  } else if (
    isV3
    && (
      (Number.isFinite(intermarketDeltaResolved) && intermarketDeltaResolved !== 0)
      || newsAdjustmentResolved !== 0
    )
  ) {
    decisionScore = displayScore
      - (Number.isFinite(intermarketDeltaResolved) ? intermarketDeltaResolved : 0)
      - newsAdjustmentResolved;
  }

  const threshold = engineAThreshold(sig);
  const maxScore = readSignalNumber(raw, 'maxScore', 'engine_a_maxScore');
  const maxScoreResolved = Number.isFinite(maxScore) ? maxScore : null;
  const hasAdjustments = isV3 && (
    (Number.isFinite(intermarketDeltaResolved) && Math.abs(intermarketDeltaResolved) > 0.0001)
    || Math.abs(newsAdjustmentResolved) > 0.0001
  );

  const passScore = isV3 && hasAdjustments ? decisionScore : displayScore;

  return {
    displayScore,
    decisionScore: Number.isFinite(decisionScore) ? decisionScore : null,
    threshold,
    maxScore: maxScoreResolved,
    intermarketDelta: Number.isFinite(intermarketDeltaResolved) ? intermarketDeltaResolved : 0,
    newsAdjustment: newsAdjustmentResolved,
    hasAdjustments,
    decisionPasses: threshold != null && threshold > 0 && Number.isFinite(passScore) && passScore >= threshold,
    displayPasses: threshold != null && threshold > 0 && displayScore >= threshold,
  };
}

export interface EngineBScoreBreakdown {
  gateScore: number | null;
  gateMax: number | null;
  totalScore: number | null;
  totalMax: number | null;
  minScore: number | null;
  scoreFloorPasses: boolean;
  confidencePasses: boolean;
  bonusPoints: number | null;
  /**
   * Earned quality points **net of penalties**, and the weight denominator they
   * are scored against. `bonusPoints` is the pre-penalty figure, so on a signal
   * carrying a hard-counter penalty the two disagree — e.g. DAI on 2026-07-29
   * had bonus_points 0.34 but quality_points_net 0.089 against a 0.95
   * denominator (9.4%). Display the net pair so the fraction and the percent
   * can never contradict each other on the card.
   */
  qualityPointsNet: number | null;
  qualityDenominator: number | null;
  /** Quality-layer percent (0-100). The only Engine B score that varies on pass. */
  qualityPct: number | null;
  /**
   * False when the style/regime floor cannot reject anything for this signal —
   * under the legacy `total` basis a passing signal always has gate_score ==
   * gate_max_possible, which already exceeds the configured floor.
   */
  minScoreFloorBinding: boolean | null;
}

function engineBConfidenceSource(
  data: EngineBNakedResult | Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  if (!data || typeof data !== 'object') return {};
  const row = data as Record<string, unknown>;
  const nested = row.naked_data ?? row.engine_b;
  const nestedRow = nested && typeof nested === 'object' && !Array.isArray(nested)
    ? nested as Record<string, unknown>
    : null;
  for (const candidate of [
    row.confidence,
    row.engine_b_status,
    nestedRow?.confidence,
    nestedRow,
  ]) {
    if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
      return candidate as Record<string, unknown>;
    }
  }
  return row;
}

/** Engine B's style/regime score floor applies to total score, not gate score. */
export function engineBScoreBreakdown(
  data: EngineBNakedResult | Record<string, unknown> | null | undefined,
): EngineBScoreBreakdown | null {
  if (!data || typeof data !== 'object') return null;
  const row = data as Record<string, unknown>;
  const conf = engineBConfidenceSource(data);

  const gateScore = readSignalNumber(
    conf,
    'gate_score',
    'gateScore',
  );
  const gateScoreFromTop = readSignalNumber(
    row,
    'gateScore',
    'engine_b_gate_score',
    'gate_score',
  );
  const resolvedGate = Number.isFinite(gateScore) ? gateScore : gateScoreFromTop;

  const totalScore = readSignalNumber(conf, 'score', 'total_score');
  const totalFromTop = readSignalNumber(
    row,
    'engine_b_score',
    'score',
    'confidence_score',
    'confluenceScore',
  );
  const resolvedTotal = Number.isFinite(totalScore) ? totalScore : totalFromTop;

  const gateMax = readSignalNumber(conf, 'gate_max_possible', 'gate_max');
  const gateMaxFromTop = readSignalNumber(row, 'gateMax', 'engine_b_gate_max', 'gate_max_possible');
  const resolvedGateMax = Number.isFinite(gateMax) ? gateMax : gateMaxFromTop;

  const totalMax = readSignalNumber(conf, 'max_possible', 'max_score');
  const totalMaxFromTop = readSignalNumber(row, 'engine_b_max', 'confidence_max', 'max_score', 'maxScore');
  const resolvedTotalMax = Number.isFinite(totalMax) ? totalMax : totalMaxFromTop;

  const minScore = readSignalNumber(
    conf,
    'min_score_scaled',
    'min_score',
  );
  const minFromTop = readSignalNumber(
    row,
    'engine_b_min_score_scaled',
    'min_score',
    'threshold',
  );
  const resolvedMin = Number.isFinite(minScore) ? minScore : minFromTop;

  const bonusPoints = readSignalNumber(conf, 'bonus_points', 'bonusPoints');
  const resolvedBonus = Number.isFinite(bonusPoints) ? bonusPoints : null;

  const qualityPointsNet = readSignalNumber(conf, 'quality_points_net', 'qualityPointsNet');
  const qualityDenominator = readSignalNumber(conf, 'quality_denominator', 'qualityDenominator');

  const qualityPct = readSignalNumber(conf, 'quality_pct', 'qualityPct');
  const qualityPctFromTop = readSignalNumber(row, 'engine_b_quality_pct', 'quality_pct');
  const resolvedQualityPct = Number.isFinite(qualityPct) ? qualityPct : qualityPctFromTop;

  const floorBindingRaw = conf.min_score_floor_binding ?? row.engine_b_min_score_floor_binding;
  const resolvedFloorBinding = typeof floorBindingRaw === 'boolean' ? floorBindingRaw : null;

  if (
    !Number.isFinite(resolvedGate)
    && !Number.isFinite(resolvedTotal)
    && !Number.isFinite(resolvedMin)
  ) {
    return null;
  }

  const scoreFloorPasses = resolvedMin == null || resolvedMin <= 0
    ? Number.isFinite(resolvedTotal)
    : Number.isFinite(resolvedTotal) && resolvedTotal >= resolvedMin;
  const explicitPass = conf.passed
    ?? conf.checklist_passed
    ?? row.engine_b_confidence_passed
    ?? row.passed
    ?? row.checklist_passed;
  const confidencePasses = typeof explicitPass === 'boolean'
    ? explicitPass && scoreFloorPasses
    : scoreFloorPasses;

  return {
    gateScore: Number.isFinite(resolvedGate) ? resolvedGate : null,
    gateMax: Number.isFinite(resolvedGateMax) ? resolvedGateMax : null,
    totalScore: Number.isFinite(resolvedTotal) ? resolvedTotal : null,
    totalMax: Number.isFinite(resolvedTotalMax) ? resolvedTotalMax : null,
    minScore: Number.isFinite(resolvedMin) ? resolvedMin : null,
    scoreFloorPasses,
    confidencePasses,
    bonusPoints: resolvedBonus,
    qualityPointsNet: Number.isFinite(qualityPointsNet) ? qualityPointsNet : null,
    qualityDenominator: Number.isFinite(qualityDenominator) ? qualityDenominator : null,
    qualityPct: Number.isFinite(resolvedQualityPct) ? resolvedQualityPct : null,
    minScoreFloorBinding: resolvedFloorBinding,
  };
}

/**
 * Confluence as a percent of the absolute scale maximum (score / maxScore).
 *
 * maxScore is Engine A V3 MAX_SCORE (3.0). Group trade bars (p70-recalibrated
 * ~0.9-1.8) sit on the same axis and are marked separately via
 * `confluenceThresholdPct` — do not rescale the bar around the threshold or
 * scores stop being comparable across groups.
 */
export function confluencePct(
  sig: EngineASignal | null | undefined,
  scoreOverride?: number | null,
): number | null {
  if (!sig) return null;
  const breakdown = engineAScoreBreakdown(sig);
  const useDecisionScore = isEngineAV3Signal(sig) && breakdown?.hasAdjustments;
  const score = scoreOverride != null && Number.isFinite(scoreOverride)
    ? scoreOverride
    : useDecisionScore && breakdown?.decisionScore != null
      ? breakdown.decisionScore
      : toNum(sig.confluenceScore ?? sig.score, NaN);
  const max = toNum(sig.maxScore, NaN);
  if (!Number.isFinite(score)) return null;
  if (Number.isFinite(max) && max > 0) {
    return Math.max(0, Math.min(100, Math.round((score / max) * 100)));
  }
  const norm = toNum((sig as Record<string, unknown>).scoreNorm, NaN);
  if (Number.isFinite(norm)) {
    return Math.max(0, Math.min(100, Math.round(norm * 100)));
  }
  return null;
}

/**
 * Where this pair's trade threshold sits on the same 0-100 axis as
 * `confluencePct`, so the score bar can render it as a marker instead of
 * rescaling the whole axis around it.
 */
export function confluenceThresholdPct(
  sig: EngineASignal | null | undefined,
): number | null {
  if (!sig) return null;
  const threshold = engineAThreshold(sig);
  const max = toNum(sig.maxScore, NaN);
  if (threshold == null || !(threshold > 0) || !Number.isFinite(max) || !(max > 0)) {
    return null;
  }
  return Math.max(0, Math.min(100, Math.round((threshold / max) * 100)));
}

/** Score for list sort / group headers — decision-time for V3 or adjusted payloads. */
export function engineAListScore(sig: EngineASignal | null | undefined): number {
  if (!sig) return 0;
  const breakdown = engineAScoreBreakdown(sig);
  const useDecision = isEngineAV3Signal(sig) && breakdown?.hasAdjustments;
  if (useDecision && breakdown?.decisionScore != null) return breakdown.decisionScore;
  return toNum(sig.confluenceScore ?? sig.score, 0);
}

/**
 * Scale-free sort key (0-1) for lists that mix Engine A and Engine B-only rows.
 *
 * `engineAListScore` returns the raw `confluenceScore`, but for Engine B-only
 * rows the backend writes Engine B's total (0-~6.0 scale) into that same field,
 * so a B row outranks every Engine A row (max 3.0) on any tiebreak that reaches
 * the score comparison. Normalising by each row's own max keeps the comparison
 * meaningful; Engine B rows prefer their quality percent, which is the only
 * Engine B number that varies between passing signals.
 */
export function unifiedListSortKey(sig: EngineASignal | null | undefined): number {
  if (!sig) return 0;
  const raw = sig as Record<string, unknown>;
  const engine = String(raw.engine_source ?? raw.engine ?? '').toUpperCase();
  const isBRow = engine === 'ENGINE_B' || engine === 'B' || engine === 'NAKED'
    || raw.is_naked === true;

  if (isBRow) {
    const qualityPct = toNum(raw.engine_b_quality_pct ?? raw.quality_pct, NaN);
    if (Number.isFinite(qualityPct)) {
      return Math.max(0, Math.min(1, qualityPct / 100));
    }
  }

  const score = engineAListScore(sig);
  const max = toNum(sig.maxScore, NaN);
  if (Number.isFinite(max) && max > 0) {
    return Math.max(0, Math.min(1, score / max));
  }
  const norm = toNum(raw.scoreNorm, NaN);
  return Number.isFinite(norm) ? Math.max(0, Math.min(1, norm)) : 0;
}

/**
 * Conviction for sorting/display. Engine A's `conviction` is its own score_norm;
 * Engine B-only rows carry no `conviction` at all, so sorting on the raw field
 * treated every Engine B row as zero conviction. Fall back to the blended
 * consensus value, then to each engine's own normalized score.
 */
export function signalConviction(sig: EngineASignal | null | undefined): number | null {
  if (!sig) return null;
  const raw = sig as Record<string, unknown>;
  for (const key of ['conviction', 'combinedConviction', 'scoreNorm'] as const) {
    const n = toNum(raw[key], NaN);
    if (Number.isFinite(n)) return Math.max(0, Math.min(1, n));
  }
  const qualityPct = toNum(raw.engine_b_quality_pct ?? raw.quality_pct, NaN);
  if (Number.isFinite(qualityPct)) return Math.max(0, Math.min(1, qualityPct / 100));
  return null;
}

export function convictionTier(c: number | null | undefined): {
  tier: 'HIGH' | 'MEDIUM' | 'LOW' | 'SKIP' | 'NA';
  color: string;
} {
  const n = toNum(c, NaN);
  if (!Number.isFinite(n)) return { tier: 'NA', color: 'text-muted-foreground' };
  if (n >= 0.7) return { tier: 'HIGH', color: 'text-long' };
  if (n >= 0.5) return { tier: 'MEDIUM', color: 'text-warning' };
  if (n >= 0.35) return { tier: 'LOW', color: 'text-muted-foreground' };
  return { tier: 'SKIP', color: 'text-short' };
}

export function regimeLabel(regime: EngineASignal['regime']): string {
  if (!regime) return '—';
  if (typeof regime === 'string') return regime;
  const row = regime as { label?: string; state?: string; smoothed?: string };
  return row.label || row.state || row.smoothed || '—';
}

export function sessionLabel(session: EngineASignal['session']): string {
  if (!session) return '—';
  if (typeof session === 'string') return session;
  const row = session as { name?: string; session?: string };
  if (row.name) return row.name;
  if (typeof row.session === 'string') return row.session;
  return '—';
}
