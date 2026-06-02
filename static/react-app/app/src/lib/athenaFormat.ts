// Display helpers for Athena payloads. Keep these tiny — heavy logic belongs
// inside the renderers, not in formatters.

import { fmtNum, toNum } from './utils';
import type { EngineASignal } from '@/types/athena';

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
  const candidates = [
    sig.threshold,
    (sig as Record<string, unknown>).engine_a_threshold,
    (sig as Record<string, unknown>).liveThreshold,
    (sig as Record<string, unknown>).scanThresholdEffective,
    (sig as Record<string, unknown>).scanThreshold,
    (sig as Record<string, unknown>).scanThresholdStatic,
  ];
  for (const raw of candidates) {
    const n = toNum(raw, NaN);
    if (Number.isFinite(n) && n > 0) return n;
  }
  return null;
}

/** Compute a confluence percent anchored to the per-pair threshold (~67% = passing).
 *  Prefer backend-computed confluencePct when available (it rounds to integer).
 */
export function confluencePct(sig: EngineASignal | null | undefined): number | null {
  if (!sig) return null;
  // Backend pre-computes this with rounding — use it if present
  if (sig.confluencePct != null && Number.isFinite(sig.confluencePct)) {
    return Math.max(0, Math.min(100, sig.confluencePct));
  }
  const score = toNum(sig.confluenceScore ?? sig.score, NaN);
  const threshold = engineAThreshold(sig);
  const max = toNum(sig.maxScore, NaN);
  if (!Number.isFinite(score)) return null;
  if (threshold != null && threshold > 0) {
    // Anchor: at threshold show ~67%, scale linearly above/below
    const pct = Math.round((score / threshold) * 67);
    return Math.max(0, Math.min(100, pct));
  }
  if (Number.isFinite(max) && max > 0) {
    return Math.max(0, Math.min(100, Math.round((score / max) * 100)));
  }
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
