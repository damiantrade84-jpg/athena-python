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

export function manualExecutionBlockReason(
  health: GhostHealth,
  signal: GhostSignal,
): string | null {
  if (health.mode === 'SHADOW') return 'shadow_mode';
  if (health.executionStatus !== 'DEMO VERIFIED') return 'demo_account_not_verified';
  if (signal.canExecute) return null;
  const reasons = signal.reasons.filter((reason) => reason !== 'shadow_mode');
  return reasons.join(' · ') || 'signal_not_execution_eligible';
}

export function confirmManualExecution(
  signal: GhostSignal,
  confirm: (message: string) => boolean,
): boolean {
  return confirm([
    `Send DEMO order for ${signal.instrument.canonicalSymbol} ${signal.direction}?`,
    `Entry: ${signal.entry ?? 'market'}`,
    `Stop: ${signal.stop ?? 'unavailable'}`,
    `Target: ${signal.target ?? 'unavailable'}`,
    'The server will re-run demo attestation, sizing, risk, freshness, and guardian checks.',
  ].join('\n'));
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

export function confirmAutoToggle(
  enabled: boolean,
  confirm: (message: string) => boolean,
): boolean {
  if (!enabled) return true;
  return confirm(
    'Enable Ghost Trade DEMO_AUTO? Orders can be sent only to currently verified demo/testnet accounts.',
  );
}
