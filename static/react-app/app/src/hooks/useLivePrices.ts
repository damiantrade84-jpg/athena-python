import { useCallback, useMemo } from 'react';
import { useApiPoll } from '@/hooks/useApiData';

type LivePriceEntry = {
  price?: number;
  bid?: number | null;
  ask?: number | null;
  ts?: number | string;
  ageSec?: number | null;
  source?: string;
  [k: string]: unknown;
};

interface PricesResponse {
  prices?: Record<string, LivePriceEntry>;
  count?: number;
  ts?: string;
}

function compactKey(value: unknown): string {
  return String(value || '')
    .trim()
    .toUpperCase()
    .replace(/[\s/_:-]/g, '');
}

function pairAliases(value: unknown): string[] {
  const raw = String(value || '').trim();
  if (!raw) return [];
  const aliases = new Set<string>([raw, raw.toUpperCase(), compactKey(raw)]);
  if (raw.includes('/')) aliases.add(raw.replace('/', ''));
  if (!raw.includes('/') && raw.toUpperCase().endsWith('USDT')) {
    aliases.add(`${raw.slice(0, -4)}/USDT`);
  }
  return [...aliases].filter(Boolean);
}

export function useLivePrices(intervalMs = 10000) {
  const { data, loading, error, refresh } = useApiPoll<PricesResponse>('/api/prices', intervalMs);

  const priceIndex = useMemo(() => {
    const index = new Map<string, LivePriceEntry>();
    const prices = data?.prices || {};
    for (const [key, entry] of Object.entries(prices)) {
      if (!entry || typeof entry !== 'object') continue;
      for (const alias of pairAliases(key)) {
        index.set(alias, entry);
      }
    }
    return index;
  }, [data?.prices]);

  const priceEntryFor = useCallback(
    (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined): LivePriceEntry | undefined => {
      const aliases = typeof item === 'string'
        ? pairAliases(item)
        : [
            ...pairAliases(item?.display),
            ...pairAliases(item?.pair),
            ...pairAliases(item?.symbol),
          ];
      for (const alias of aliases) {
        const found = priceIndex.get(alias);
        if (found) return found;
      }
      return undefined;
    },
    [priceIndex],
  );

  const priceFor = useCallback(
    (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined): number | undefined => {
      const value = priceEntryFor(item)?.price;
      const num = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(num) && num > 0 ? num : undefined;
    },
    [priceEntryFor],
  );

  // GAP-5: surface freshness/provenance so components can render stale states.
  // ageSec comes pre-decorated from /api/prices (athena_app/api/routes_market_data.py).
  const ageSecFor = useCallback(
    (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined): number | undefined => {
      const value = priceEntryFor(item)?.ageSec;
      const num = typeof value === 'number' ? value : Number(value);
      return Number.isFinite(num) && num >= 0 ? num : undefined;
    },
    [priceEntryFor],
  );

  const sourceFor = useCallback(
    (item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined): string | undefined => {
      const value = priceEntryFor(item)?.source;
      return typeof value === 'string' && value.length > 0 ? value : undefined;
    },
    [priceEntryFor],
  );

  // True when ageSec is known AND exceeds thresholdSec. Unknown age returns
  // false so callers can render a separate "no age" state if they want.
  const staleFor = useCallback(
    (
      item: { display?: unknown; pair?: unknown; symbol?: unknown } | string | null | undefined,
      thresholdSec: number,
    ): boolean => {
      if (!(Number.isFinite(thresholdSec) && thresholdSec > 0)) return false;
      const age = ageSecFor(item);
      return age !== undefined && age > thresholdSec;
    },
    [ageSecFor],
  );

  return {
    prices: data?.prices || {},
    loading,
    error,
    refresh,
    priceEntryFor,
    priceFor,
    ageSecFor,
    sourceFor,
    staleFor,
  };
}
