import type { GhostAssetGroup, GhostHealth, GhostSignal } from './types';

export const groupLabels: Record<GhostAssetGroup, string> = {
  forex: 'Forex', crypto: 'Crypto', metals: 'Metals', energy: 'Energy',
  commodities_other: 'Other Commodities', indices: 'Indices',
  equities: 'Equities', other: 'Other',
};

export function bannerForHealth(health: GhostHealth): string {
  if (health.mode === 'SHADOW' || health.executionStatus === 'SHADOW ONLY') {
    return 'SHADOW MODE — NO ORDERS CAN BE SENT';
  }
  if (health.executionStatus === 'DEMO VERIFIED') return 'DEMO VERIFIED';
  return 'DEMO EXECUTION DISABLED — ACCOUNT COULD NOT BE VERIFIED';
}

export function canShowExecute(health: GhostHealth, signal: GhostSignal): boolean {
  return health.mode !== 'SHADOW'
    && health.executionStatus === 'DEMO VERIFIED'
    && signal.canExecute;
}

export function groupSignals(signals: GhostSignal[]): Record<GhostAssetGroup, GhostSignal[]> {
  const result: Record<GhostAssetGroup, GhostSignal[]> = {
    forex: [], crypto: [], metals: [], energy: [], commodities_other: [],
    indices: [], equities: [], other: [],
  };
  signals.forEach((signal) => result[signal.instrument.assetGroup].push(signal));
  return result;
}

export function sortSignals(signals: GhostSignal[], sort: 'score' | 'symbol' | 'age'): GhostSignal[] {
  return [...signals].sort((left, right) => {
    if (sort === 'symbol') return left.instrument.canonicalSymbol.localeCompare(right.instrument.canonicalSymbol);
    if (sort === 'age') return Date.parse(right.decisionTime) - Date.parse(left.decisionTime);
    return right.confirmedScore - left.confirmedScore
      || left.instrument.canonicalSymbol.localeCompare(right.instrument.canonicalSymbol);
  });
}

export function scoreLabel(value: number): string {
  if (value >= 0.75) return 'VERY STRONG';
  if (value >= 0.55) return 'STRONG';
  if (value >= 0.35) return 'MODERATE';
  return 'LOW';
}

export function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}`;
}
