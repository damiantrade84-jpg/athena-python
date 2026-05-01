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
  const threshold = toNum(sig.threshold, NaN);
  const max = toNum(sig.maxScore, NaN);
  if (!Number.isFinite(score)) return null;
  if (Number.isFinite(threshold) && threshold > 0) {
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
  return regime.label || regime.smoothed || '—';
}

export function sessionLabel(session: EngineASignal['session']): string {
  if (!session) return '—';
  if (typeof session === 'string') return session;
  return session.name || '—';
}
