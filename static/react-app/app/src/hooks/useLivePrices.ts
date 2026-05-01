import { useCallback, useMemo } from 'react';
import { useApiPoll } from '@/hooks/useApiData';

type LivePriceEntry = {
  price?: number;
  bid?: number | null;
  ask?: number | null;
  ts?: number | string;
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

export function useLivePrices(intervalMs = 3000) {
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

  return { prices: data?.prices || {}, loading, error, refresh, priceEntryFor, priceFor };
}
