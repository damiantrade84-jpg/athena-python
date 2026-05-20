import { useCallback, useEffect, useMemo, useState } from 'react';
import apiClient from '@/lib/apiClient';

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

type LivePriceSnapshot = {
  data: PricesResponse | null;
  loading: boolean;
  error: string | null;
};

export const LIVE_PRICE_POLL_INTERVAL_MS = 2000;

const listeners = new Set<() => void>();
let snapshot: LivePriceSnapshot = { data: null, loading: true, error: null };
let intervalId: ReturnType<typeof setInterval> | null = null;
let inFlight = false;

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

function notify() {
  for (const listener of listeners) listener();
}

async function fetchSharedPrices() {
  if (inFlight) return;
  inFlight = true;
  snapshot = { ...snapshot, loading: snapshot.data == null };
  notify();
  try {
    const data = await apiClient.getJson('/api/prices') as PricesResponse;
    snapshot = { data, loading: false, error: null };
  } catch (e: unknown) {
    snapshot = {
      ...snapshot,
      loading: false,
      error: e instanceof Error ? e.message : 'Unknown error',
    };
  } finally {
    inFlight = false;
    notify();
  }
}

function subscribe(listener: () => void, intervalMs: number) {
  listeners.add(listener);
  if (!intervalId) {
    void fetchSharedPrices();
    intervalId = setInterval(fetchSharedPrices, intervalMs);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && intervalId) {
      clearInterval(intervalId);
      intervalId = null;
    }
  };
}

export function useLivePrices(intervalMs = LIVE_PRICE_POLL_INTERVAL_MS) {
  const [local, setLocal] = useState(snapshot);

  useEffect(() => subscribe(() => setLocal(snapshot), intervalMs), [intervalMs]);

  const priceIndex = useMemo(() => {
    const index = new Map<string, LivePriceEntry>();
    const prices = local.data?.prices || {};
    for (const [key, entry] of Object.entries(prices)) {
      if (!entry || typeof entry !== 'object') continue;
      for (const alias of pairAliases(key)) {
        index.set(alias, entry);
      }
    }
    return index;
  }, [local.data?.prices]);

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
    prices: local.data?.prices || {},
    loading: local.loading,
    error: local.error,
    refresh: fetchSharedPrices,
    priceEntryFor,
    priceFor,
    ageSecFor,
    sourceFor,
    staleFor,
  };
}
