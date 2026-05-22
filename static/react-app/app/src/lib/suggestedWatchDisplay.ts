import type { SuggestedTradeWatch } from '@/types/athena';

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
