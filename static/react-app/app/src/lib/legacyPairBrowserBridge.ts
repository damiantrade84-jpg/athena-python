// Legacy pair-browser globals used by static/js/features/pair_browser.js

import {
  confluencePct,
  engineAListScore,
  engineAThreshold,
  fmtPrice,
  fmtScore,
} from '@/lib/athenaFormat';
import { toNum, fmtNum } from '@/lib/utils';

function signalContext(sig: unknown): { pair?: string; type?: string } {
  if (!sig || typeof sig !== 'object') return {};
  const row = sig as Record<string, unknown>;
  return {
    pair: String(row.display ?? row.symbol ?? row.pair ?? ''),
    type: String(row.type ?? ''),
  };
}

export function formatConfluenceText(sig: unknown): string {
  if (!sig || typeof sig !== 'object') return 'n/a';
  const row = sig as Record<string, unknown>;

  // Engine B naked / structure payload
  if ('passed' in row || 'current_swing_sequence' in row || 'gate_score' in row) {
    const score = toNum(row.score ?? row.gate_score, NaN);
    const threshold = toNum(row.min_score ?? row.threshold, NaN);
    if (!Number.isFinite(score)) return 'n/a';
    if (Number.isFinite(threshold)) return fmtScore(score, threshold);
    return fmtNum(score, 2);
  }

  const ctx = signalContext(sig);
  const score = engineAListScore(row as never);
  const threshold = engineAThreshold(row as never);
  const max = toNum(row.maxScore, 3);
  const pct = confluencePct(row as never);
  const denom = threshold ?? max;
  if (pct != null && Number.isFinite(score)) {
    return `${fmtNum(score, 2)} / ${fmtNum(denom, 2)} (${pct}%)`;
  }
  if (Number.isFinite(score)) return fmtScore(score, denom);
  return 'n/a';
}

export function getSignalLivePrice(sig: unknown): number | null {
  if (!sig || typeof sig !== 'object') return null;
  const row = sig as Record<string, unknown>;
  for (const key of ['livePrice', 'price', 'entry', 'current_price']) {
    const n = toNum(row[key], NaN);
    if (Number.isFinite(n) && n > 0) return n;
  }
  const ctx = signalContext(sig);
  const latest = window.AppState?.latestPrices?.[ctx.pair || ''];
  if (latest && Number.isFinite(latest.price) && latest.price > 0) return latest.price;
  return null;
}

export function escSigHtml(value: unknown): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function fmtPriceForSig(value: number | string, ctx?: unknown): string {
  const ctxRow = ctx && typeof ctx === 'object' ? (ctx as Record<string, unknown>) : {};
  const pair = String(ctxRow.display ?? ctxRow.symbol ?? ctxRow.pair ?? '');
  const type = String(ctxRow.type ?? '');
  return fmtPrice(value, pair, type);
}

export function initLegacyPairBrowserBridge(): void {
  window.formatConfluenceText = formatConfluenceText;
  window.getSignalLivePrice = getSignalLivePrice;
  window.escSigHtml = escSigHtml;
  window.fmtPriceForSig = fmtPriceForSig;
}
