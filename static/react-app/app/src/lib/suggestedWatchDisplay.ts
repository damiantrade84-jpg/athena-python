import type { SuggestedTradeWatch } from '@/types/athena';

export function suggestedWatchStatus(watch: SuggestedTradeWatch): string {
  return String(watch.status || '').toUpperCase();
}

export function isSuggestedWatchReady(watch: SuggestedTradeWatch | string): boolean {
  const status = typeof watch === 'string' ? watch.toUpperCase() : suggestedWatchStatus(watch);
  return status === 'READY_FOR_REVIEW' || status === 'LEVEL_REACHED';
}

export function suggestedWatchId(watch: SuggestedTradeWatch): string {
  return String(watch.watch_id || '').trim();
}

export function suggestedWatchLabel(watch: SuggestedTradeWatch): string {
  return String(watch.display || watch.symbol || 'setup').trim() || 'setup';
}

export function isScalpSuggestedWatch(watch: SuggestedTradeWatch): boolean {
  return String(watch.source || '').toLowerCase().includes('scalp');
}

export function readySuggestedWatches(watches: SuggestedTradeWatch[] | undefined): SuggestedTradeWatch[] {
  return (Array.isArray(watches) ? watches : []).filter((watch) => (
    isSuggestedWatchReady(watch) && suggestedWatchId(watch)
  ));
}

export function compactWatchStatusLabel(watches: SuggestedTradeWatch[]): string | null {
  if (watches.length === 0) return null;

  const statuses = watches.map((watch) => String(watch.status || '').toUpperCase());
  if (statuses.some((status) => status === 'READY_FOR_REVIEW' || status === 'LEVEL_REACHED')) {
    return 'Ready for review';
  }
  if (statuses.every((status) => status === 'EXPIRED')) return 'Expired';
  if (statuses.every((status) => status === 'CANCELLED')) return 'Cancelled';
  if (statuses.some((status) => status === 'EXPIRED')) return 'Expired';
  if (statuses.some((status) => status === 'CANCELLED')) return 'Cancelled';
  return 'Watching';
}

export function watchDetailLabel(watch: SuggestedTradeWatch): string {
  const dir = watch.direction || '';
  if (watch.zone_low != null && watch.zone_high != null) {
    return `Watching: ${watch.zone_low}-${watch.zone_high} ${dir} zone`.trim();
  }
  if (watch.level != null) {
    return `Watching: ${watch.level} ${dir}`.trim();
  }
  return `Watching: ${watch.symbol || ''} ${dir}`.trim();
}
