export type DirectionKey = 'LONG' | 'SHORT' | 'UNKNOWN';

export type DirectionBreakdown = {
  LONG: number;
  SHORT: number;
  UNKNOWN: number;
  [key: string]: number;
};

export function resolveDirectionBreakdown(
  serverBreakdown: unknown,
  trades: Array<Record<string, unknown>> | undefined,
): DirectionBreakdown {
  const counts: DirectionBreakdown = { LONG: 0, SHORT: 0, UNKNOWN: 0 };

  if (serverBreakdown && typeof serverBreakdown === 'object' && !Array.isArray(serverBreakdown)) {
    for (const [key, value] of Object.entries(serverBreakdown as Record<string, unknown>)) {
      const direction = normalizeBacktestDirection(key);
      const count = typeof value === 'number' ? value : Number(value);
      if (Number.isFinite(count)) {
        counts[direction] = (counts[direction] ?? 0) + count;
      }
    }
    return counts;
  }

  for (const trade of trades ?? []) {
    const direction = normalizeBacktestDirection(trade.direction);
    counts[direction] = (counts[direction] ?? 0) + 1;
  }
  return counts;
}

export function normalizeBacktestDirection(value: unknown): DirectionKey {
  const direction = String(value ?? '').trim().toUpperCase();
  if (direction === 'LONG' || direction === 'BUY') return 'LONG';
  if (direction === 'SHORT' || direction === 'SELL') return 'SHORT';
  return 'UNKNOWN';
}
