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

/** Split display confluence from the pre-blend score used for V3 decision / legacy tier. */
export function engineAScoreBreakdown(sig: EngineASignal | null | undefined): EngineAScoreBreakdown | null {
  if (!sig) return null;
  const raw = sig as Record<string, unknown>;
  const displayScore = readSignalNumber(raw, 'confluenceScore', 'score', 'engine_a_confluenceScore');
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
  if (Number.isFinite(preNews)) {
    decisionScore = preNews - (Number.isFinite(intermarketDeltaResolved) ? intermarketDeltaResolved : 0);
  } else if (
    (Number.isFinite(intermarketDeltaResolved) && intermarketDeltaResolved !== 0)
    || newsAdjustmentResolved !== 0
  ) {
    decisionScore = displayScore
      - (Number.isFinite(intermarketDeltaResolved) ? intermarketDeltaResolved : 0)
      - newsAdjustmentResolved;
  }

  const threshold = engineAThreshold(sig);
  const maxScore = readSignalNumber(raw, 'maxScore', 'engine_a_maxScore');
  const maxScoreResolved = Number.isFinite(maxScore) ? maxScore : null;
  const hasAdjustments = (
    (Number.isFinite(intermarketDeltaResolved) && Math.abs(intermarketDeltaResolved) > 0.0001)
    || Math.abs(newsAdjustmentResolved) > 0.0001
  );

  return {
    displayScore,
    decisionScore: Number.isFinite(decisionScore) ? decisionScore : null,
    threshold,
    maxScore: maxScoreResolved,
    intermarketDelta: Number.isFinite(intermarketDeltaResolved) ? intermarketDeltaResolved : 0,
    newsAdjustment: newsAdjustmentResolved,
    hasAdjustments,
    decisionPasses: threshold != null && threshold > 0 && Number.isFinite(decisionScore) && decisionScore >= threshold,
    displayPasses: threshold != null && threshold > 0 && displayScore >= threshold,
  };
}

export interface EngineBScoreBreakdown {
  gateScore: number | null;
  gateMax: number | null;
  totalScore: number | null;
  totalMax: number | null;
  minScore: number | null;
  gatePasses: boolean;
  totalPasses: boolean;
  bonusPoints: number | null;
}

function engineBConfidenceSource(
  data: EngineBNakedResult | Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  if (!data || typeof data !== 'object') return {};
  const row = data as Record<string, unknown>;
  const conf = row.confidence;
  return conf && typeof conf === 'object' && !Array.isArray(conf)
    ? conf as Record<string, unknown>
    : row;
}

/** Engine B pass floor uses gate_score; UI often showed total_score only. */
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
  const gateMaxFromTop = readSignalNumber(row, 'engine_b_gate_max', 'gate_max_possible');
  const resolvedGateMax = Number.isFinite(gateMax) ? gateMax : gateMaxFromTop;

  const totalMax = readSignalNumber(conf, 'max_possible', 'max_score');
  const totalMaxFromTop = readSignalNumber(row, 'confidence_max', 'max_score', 'maxScore');
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

  if (
    !Number.isFinite(resolvedGate)
    && !Number.isFinite(resolvedTotal)
    && !Number.isFinite(resolvedMin)
  ) {
    return null;
  }

  const gatePasses = resolvedMin != null && resolvedMin > 0
    && Number.isFinite(resolvedGate)
    && resolvedGate >= resolvedMin;
  const totalPasses = resolvedMin != null && resolvedMin > 0
    && Number.isFinite(resolvedTotal)
    && resolvedTotal >= resolvedMin;

  return {
    gateScore: Number.isFinite(resolvedGate) ? resolvedGate : null,
    gateMax: Number.isFinite(resolvedGateMax) ? resolvedGateMax : null,
    totalScore: Number.isFinite(resolvedTotal) ? resolvedTotal : null,
    totalMax: Number.isFinite(resolvedTotalMax) ? resolvedTotalMax : null,
    minScore: Number.isFinite(resolvedMin) ? resolvedMin : null,
    gatePasses,
    totalPasses,
    bonusPoints: resolvedBonus,
  };
}

/** Compute a confluence percent anchored to the per-pair threshold (~67% = passing). */
export function confluencePct(
  sig: EngineASignal | null | undefined,
  scoreOverride?: number | null,
): number | null {
  if (!sig) return null;
  const breakdown = engineAScoreBreakdown(sig);
  const useDecisionScore = isEngineAV3Signal(sig) || breakdown?.hasAdjustments;
  const score = scoreOverride != null && Number.isFinite(scoreOverride)
    ? scoreOverride
    : useDecisionScore && breakdown?.decisionScore != null
      ? breakdown.decisionScore
      : toNum(sig.confluenceScore ?? sig.score, NaN);
  const threshold = engineAThreshold(sig);
  const max = toNum(sig.maxScore, NaN);
  if (!Number.isFinite(score)) return null;
  if (threshold != null && threshold > 0) {
    const pct = Math.round((score / threshold) * 67);
    return Math.max(0, Math.min(100, pct));
  }
  if (Number.isFinite(max) && max > 0) {
    return Math.max(0, Math.min(100, Math.round((score / max) * 100)));
  }
  return null;
}

/** Score for list sort / group headers — decision-time for V3 or adjusted payloads. */
export function engineAListScore(sig: EngineASignal | null | undefined): number {
  if (!sig) return 0;
  const breakdown = engineAScoreBreakdown(sig);
  const useDecision = isEngineAV3Signal(sig) || breakdown?.hasAdjustments;
  if (useDecision && breakdown?.decisionScore != null) return breakdown.decisionScore;
  return toNum(sig.confluenceScore ?? sig.score, 0);
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
