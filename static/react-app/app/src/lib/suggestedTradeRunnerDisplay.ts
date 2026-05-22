import type { SuggestedTradeRunnerStatus } from '@/types/athena';

export function runnerBadgeLabel(runner: SuggestedTradeRunnerStatus | undefined): string {
  if (!runner?.enabled) return 'INACTIVE';
  if (runner.error) return 'ERROR';
  if (runner.mode === 'background_thread' && runner.active) return 'ACTIVE';
  if (runner.mode === 'frontend_poll') return 'POLLING ONLY';
  if (runner.mode === 'manual_only') return 'INACTIVE';
  return runner.active ? 'ACTIVE' : 'POLLING ONLY';
}

export function runnerBadgeClass(runner: SuggestedTradeRunnerStatus | undefined): string {
  const label = runnerBadgeLabel(runner);
  if (label === 'ACTIVE') return 'border-long/40 text-long';
  if (label === 'ERROR') return 'border-short/40 text-short';
  if (label === 'POLLING ONLY') return 'border-warning/40 text-warning';
  return 'border-muted-foreground/30 text-muted-foreground';
}

export function runnerCompactLabel(runner: SuggestedTradeRunnerStatus | undefined): string {
  return runnerBadgeLabel(runner);
}
